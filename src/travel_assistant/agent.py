"""旅行出行助手 —— 智能体主循环。

职责
----
* 组装系统提示（人格 + 长期记忆 + 工具使用规范）
* 维护会话短期记忆（持久化在 SQLite）
* 识图预处理：图片 → 景点/坐标 → 注入上下文供模型规划
* function calling 编排：模型 → 工具 → 结果 → 模型 … 直到给出最终答复
* 自动写入长期记忆（用户画像抽取）与行程归档

事件流
------
:meth:`TravelAssistant.stream` 产出结构化事件，便于 CLI 与 Web 共用：

* ``{"type": "status", "text": ...}``      阶段性提示
* ``{"type": "vision", "result": {...}}``  图片识别结果
* ``{"type": "tool_call", "name", "args"}``
* ``{"type": "tool_result", "name", "ok", "summary"}``
* ``{"type": "final", "content": ...}``    最终答复
* ``{"type": "error", "message": ...}``
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .llm import DeepSeekClient, LLMEmptyResponseError
from .memory import PROFILE_KEYS, MemoryStore
from .models import Message, Role, ToolResult, VisionResult
from .tools import build_default_registry
from .tools.registry import ToolRegistry
from .vision import VisionAnalyzer

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是「旅行出行助手」，一位专业、体贴、务实的中国旅游规划顾问。

# 你擅长的事
1. **出行方案规划**：为目的地设计逐日行程，考虑动线顺序、体力分配、开放时间与预约要求。
2. **游玩措施推荐**：不仅列景点，还要给出怎么玩——最佳时段、避坑提示、排队策略、交通接驳、拍照机位。
3. **费用规划**：给出交通/住宿/餐饮/门票/市内交通/购物六项明细与总计，说明估算依据与浮动区间。
4. **看图识景**：用户发来景点照片时，识别地点并围绕它给出游玩方案。

# 工具使用规范（重要）
* 涉及**具体景点、门票、时长、最佳季节**时，必须先调用 `search_attractions` / `attraction_detail` 获取真实数据，
  **不要凭记忆编造**。工具返回为空或未收录该城市时，可以基于常识回答，但必须明确说明「这部分是通用建议，非本地数据库数据」。
* 涉及**行程**用 `plan_itinerary`，涉及**费用**用 `estimate_budget`，涉及**天气**用 `get_weather`。
* 生成行程前若用户没说出发地，且长期记忆里也没有常住城市，**先问一次**，或按北京估算并明确标注假设。
* 识别出图片地点后，用 `nearby_attractions` 找周边可一并游玩的地方，再用 `get_weather` 看天气。
* 一次可以并行调用多个工具。工具报错时不要慌张，读懂错误信息调整参数重试，或换一条路。

# 记忆与连续性
* 下方「用户长期记忆」是你在以往对话中了解到的用户情况，**必须主动利用**，不要重复询问已知信息。
* 保持上下文连贯，牢记用户本次会话中已说过的天数、人数、预算、偏好，**绝不重复提问**。
* 用户改变主意时，以最新说法为准。

# 回答风格
* 用中文，结构清晰，适度使用小标题与列表，但不要过度堆砌 emoji。
* 具体、可执行：给出时间点、价格数字、操作步骤，而不是笼统建议。
* 涉及价格一律标注「参考价」，并提示旺季浮动。
* 不确定的地方如实说明，不要编造景点、价格或开放时间。
* 回答结尾可以用一句话提示下一步可以做什么（例如「需要我按这个方案出预算明细吗？」）。"""

#: 触发长期记忆抽取的对话轮次间隔
PROFILE_EVERY_TURNS = 1


class TravelAssistant:
    """旅行出行助手智能体。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        memory: MemoryStore | None = None,
        client: DeepSeekClient | None = None,
        registry: ToolRegistry | None = None,
        session_id: str | None = None,
        enable_long_term_memory: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.validate_ready()
        self.settings.ensure_dirs()

        self.memory = memory or MemoryStore(self.settings)
        self.client = client or DeepSeekClient(self.settings)
        # 注意：注册表需要在 client 之后构建，因为坐标反查工具依赖模型知识
        self.registry = registry or build_default_registry(self.client)
        self.vision = VisionAnalyzer(self.client, self.settings)
        self.enable_long_term_memory = enable_long_term_memory

        self.session_id = self.memory.ensure_session(session_id)
        self._turns = 0

    # ------------------------------------------------------------------ #
    # 提示组装
    # ------------------------------------------------------------------ #
    def build_system_prompt(self) -> str:
        profile = self.memory.profile_text()
        tool_list = "、".join(f"`{n}`" for n in self.registry.names())
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"# 用户长期记忆（跨会话保留）\n{profile}\n\n"
            f"# 你当前可用的工具\n{tool_list}"
        )

    def _api_messages(self, extra_user: Message | None = None) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.build_system_prompt()}]
        history = self.memory.get_context_messages(self.session_id)
        for m in history:
            msgs.append(m.to_api())
        if extra_user is not None:
            msgs.append(extra_user.to_api())
        return msgs

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    async def stream(
        self,
        user_text: str,
        *,
        image_paths: Iterable[str | Path] | None = None,
        image_hint: str = "",
        save_user_message: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """处理一轮用户输入，产出事件流。"""
        paths = [Path(p) for p in (image_paths or [])]
        vision_results: list[tuple[VisionResult, tuple[float, float] | None]] = []

        # ---------- 1. 识图 ----------
        for p in paths:
            yield {"type": "status", "text": f"正在识别图片：{p.name}"}
            try:
                data_url = self.vision.encode_file(p)
                res = await self.vision.analyze_data_url(data_url, image_hint)
            except Exception as e:  # noqa: BLE001
                yield {"type": "error", "message": f"图片处理失败：{type(e).__name__}: {e}"}
                continue
            coords = None
            raw = _try_json(res.raw)
            if raw:
                la, lo = raw.get("lat"), raw.get("lon")
                if isinstance(la, (int, float)) and isinstance(lo, (int, float)):
                    coords = (float(la), float(lo))
            vision_results.append((res, coords))
            yield {"type": "vision", "result": res.model_dump()}

        # ---------- 2. 组装本轮用户消息 ----------
        text_parts = [user_text.strip()] if user_text and user_text.strip() else []
        if not text_parts and vision_results:
            text_parts.append("请帮我分析这张图片，告诉我这是哪里，并给出游玩方案。")
        for res, coords in vision_results:
            text_parts.append(self.vision.render(res, coords))
        final_user_text = "\n\n".join(text_parts)

        user_msg = Message(role=Role.USER, content=final_user_text)
        if save_user_message:
            self.memory.add_message(self.session_id, user_msg)

        # ---------- 3. function calling 循环 ----------
        rounds = 0
        final_content = ""
        while rounds < self.settings.max_tool_rounds:
            rounds += 1
            try:
                resp = await self.client.chat(
                    self._api_messages(user_msg if rounds == 1 else None),
                    tools=self.registry.api_tools(),
                )
            except LLMEmptyResponseError as e:
                yield {"type": "error", "message": str(e)}
                return
            except Exception as e:  # noqa: BLE001
                yield {"type": "error", "message": f"模型调用失败：{type(e).__name__}: {e}"}
                return

            tool_calls = resp.get("tool_calls") or []
            content = resp.get("content") or ""

            # 记录助手消息（含工具调用请求）
            assistant_msg = Message(
                role=Role.ASSISTANT,
                content=content,
                tool_calls=tool_calls or None,
            )
            self.memory.add_message(self.session_id, assistant_msg)

            if not tool_calls:
                final_content = content
                break

            # 执行工具
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                args = _parse_args(raw_args)
                yield {"type": "tool_call", "name": name, "args": args}

                result = await self.registry.execute(name, args)
                yield {
                    "type": "tool_result",
                    "name": name,
                    "ok": result.ok,
                    "summary": result.summary,
                }

                # 行程/预算结果归档到长期记忆
                if result.ok:
                    self._archive(name, args, result)

                tool_msg = Message(
                    role=Role.TOOL,
                    content=_tool_payload(result),
                    tool_call_id=call.get("id"),
                    name=name,
                )
                self.memory.add_message(self.session_id, tool_msg)

            if rounds >= self.settings.max_tool_rounds:
                yield {
                    "type": "status",
                    "text": f"已达到工具调用轮次上限（{self.settings.max_tool_rounds}），正在总结",
                }
                try:
                    resp = await self.client.chat(
                        self._api_messages(), tools=None
                    )
                    final_content = resp.get("content") or ""
                    self.memory.add_message(
                        self.session_id, Message(role=Role.ASSISTANT, content=final_content)
                    )
                except Exception as e:  # noqa: BLE001
                    yield {"type": "error", "message": f"总结失败：{e}"}
                    return
                break

        if not final_content:
            final_content = "抱歉，我没能生成有效回复，请换个说法再试一次。"

        # ---------- 4. 长期记忆抽取 ----------
        self._turns += 1
        if self.enable_long_term_memory and self._turns % PROFILE_EVERY_TURNS == 0:
            yield {"type": "status", "text": "正在更新长期记忆"}
            try:
                await self.extract_profile()
            except Exception as e:  # noqa: BLE001
                logger.warning("长期记忆抽取失败：%s", e)

        yield {"type": "final", "content": final_content}

    async def chat(
        self,
        user_text: str,
        *,
        image_paths: Iterable[str | Path] | None = None,
        image_hint: str = "",
    ) -> str:
        """非流式便捷入口，返回最终答复文本。"""
        final = ""
        async for ev in self.stream(user_text, image_paths=image_paths, image_hint=image_hint):
            if ev["type"] == "final":
                final = ev["content"]
            elif ev["type"] == "error":
                final = f"[错误] {ev['message']}"
        return final

    # ------------------------------------------------------------------ #
    # 长期记忆抽取
    # ------------------------------------------------------------------ #
    async def extract_profile(self) -> dict[str, str]:
        """从最近对话中抽取用户画像，写入长期记忆。"""
        recent = self.memory.get_context_messages(self.session_id)
        if not recent:
            return {}
        convo = "\n".join(
            f"{'用户' if m.role is Role.USER else '助手' if m.role is Role.ASSISTANT else '工具'}：{m.content[:600]}"
            for m in recent[-12:]
            if m.role in (Role.USER, Role.ASSISTANT) and m.content
        )
        if not convo.strip():
            return {}

        keys = "\n".join(f"- {k}：{v}" for k, v in PROFILE_KEYS.items())
        prompt = (
            "从下面的旅行对话中抽取用户信息，输出 JSON。\n"
            "规则：\n"
            "1. 键必须来自给定清单，只输出对话中确实提到的字段；\n"
            "2. 忽略寒暄、临时提问、一次性的行程细节；\n"
            "3. 没提到任何字段就输出 {}；\n"
            "4. 直接输出 JSON，不要分析过程。\n\n"
            f"字段清单：\n{keys}\n\n"
            f"对话：\n{convo}\n\n"
            "JSON："
        )
        try:
            # 该模型推理链很长，抽取任务本身很短，但仍需给足预算，
            # 否则 token 会被推理吃光导致正文为空（见 llm.py 的说明）。
            raw = await self.client.complete(
                prompt, json_mode=True, max_tokens=4000, temperature=1.0
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("画像抽取调用失败：%s", e)
            return {}
        data = _parse_json_object(raw)
        updated: dict[str, str] = {}
        for k, v in (data or {}).items():
            if not isinstance(v, (str, int, float)):
                continue
            sv = str(v).strip()
            if not sv or sv.lower() in {"null", "none", "未知", "无"}:
                continue
            if self.memory.upsert_profile(str(k), sv, source="auto"):
                updated[str(k)] = sv
        if updated:
            logger.info("长期记忆已更新：%s", list(updated))
        return updated

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _archive(self, tool_name: str, args: dict[str, Any], result: ToolResult) -> None:
        """把行程/预算沉淀到长期记忆的行程表。"""
        try:
            data = result.data if isinstance(result.data, dict) else {}
            if tool_name == "plan_itinerary" and data.get("city"):
                self.memory.save_trip(
                    session_id=self.session_id,
                    city=str(data.get("city")),
                    days=int(data.get("days") or 0),
                    people=int(data.get("travelers") or 1),
                    tier="",
                    plan=data,
                )
            elif tool_name == "estimate_budget" and data.get("city"):
                self.memory.save_trip(
                    session_id=self.session_id,
                    city=str(data.get("city")),
                    days=int(data.get("days") or 0),
                    people=int(data.get("people") or 1),
                    tier=str(data.get("tier") or ""),
                    budget=data,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("行程归档失败：%s", e)

    async def aclose(self) -> None:
        await self.client.close()
        self.memory.close()

    async def __aenter__(self) -> "TravelAssistant":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


# ---------------------------------------------------------------------- #
# 工具函数
# ---------------------------------------------------------------------- #
def _parse_args(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        obj = json.loads(raw or "{}")
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        logger.warning("工具参数不是合法 JSON：%s", raw[:200])
        return {}


def _parse_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(t[s:e + 1])
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _try_json(text: str) -> dict[str, Any]:
    return _parse_json_object(text)


def _tool_payload(result: ToolResult) -> str:
    """把工具结果转成给模型看的紧凑文本。"""
    if not result.ok:
        return f"[失败] {result.error}"
    body = result.summary or ""
    if result.data is not None and not body:
        try:
            body = json.dumps(result.data, ensure_ascii=False)[:4000]
        except (TypeError, ValueError):
            body = str(result.data)[:4000]
    return body
