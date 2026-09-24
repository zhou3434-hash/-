"""图片识别模块：景点识别 + 定位。

流程
----
1. 读取图片 → base64 data URL（限制大小，控制成本）；
2. 让模型以 **JSON 模式** 输出结构化识别结果（可同时识别多个地标并给坐标）；
3. 容错解析（模型有时会包裹 markdown 代码块）；
4. 交由智能体继续调用 nearby_attractions / get_weather / plan_itinerary 生成游玩方案。

注意
----
* 图片只能放在 `user` 消息中，`system`/`assistant` 带图会返回 400；
* 该模型会先进行思维链推理，因此 max_tokens 必须给足，否则正文为空。
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .llm import DeepSeekClient
from .models import VisionResult

logger = logging.getLogger(__name__)

SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}

VISION_PROMPT = """你是旅游出行助手的「看图识景」模块。请分析用户上传的图片，识别其中的景点/地标。

要求：
1. 如果是著名景点、地标建筑、自然景观或城市街景，尽量给出**准确的景点名称**，以及所在**城市、国家**；
2. 如果能判断具体地理位置，请给出**经纬度**（越准确越好，定位到景点级别）；
3. 如果图片不是旅游景点（例如证件、截图、商品、纯文字等），把 is_travel_image 设为 false，并在 description 说明原因；
4. 如果图片是景点但你无法确定具体是哪里，请如实说明，不要编造；此时 confidence 设为 low；
5. 可以识别出多个地标时，全部列在 landmarks 中。

只输出 JSON，不要输出任何解释文字。JSON 结构：
{
  "is_travel_image": true,
  "scene_type": "景点类型，如 历史古迹/自然风光/城市地标/海岛/宗教建筑/街区/博物馆",
  "landmarks": ["识别到的景点或地标名称，按可能性排序"],
  "city": "城市名，不确定填 null",
  "country": "国家名，不确定填 null",
  "region": "省/州/地区，不确定填 null",
  "lat": 纬度数字或 null,
  "lon": 经度数字或 null,
  "description": "对图片内容的简短客观描述（2-3句，中文）",
  "confidence": "high 或 medium 或 low",
  "reason": "判断依据（简短）"
}"""


class VisionAnalyzer:
    """图片识别器。"""

    def __init__(self, client: DeepSeekClient | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or DeepSeekClient(self.settings)

    # ------------------------------------------------------------------ #
    # 图片编码
    # ------------------------------------------------------------------ #
    def encode_file(self, path: str | Path) -> str:
        """把本地图片编码为 data URL，并做大小与格式校验。"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"图片不存在：{p}")
        size_mb = p.stat().st_size / 1024 / 1024
        limit = self.settings.vision_max_image_mb
        if size_mb > limit:
            raise ValueError(
                f"图片 {size_mb:.1f} MB 超过限制 {limit} MB。请压缩后重试。"
            )
        mime = mimetypes.guess_type(p.name)[0] or ""
        if mime not in SUPPORTED:
            # 让模型按内容判断，这里只做提示
            logger.warning("图片扩展名推断类型为 %s，非标准格式，仍按原样提交", mime or "未知")
            mime = mime if mime in SUPPORTED else "image/jpeg"
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def encode_bytes(data: bytes, mime: str = "image/jpeg") -> str:
        if mime not in SUPPORTED:
            mime = "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    # ------------------------------------------------------------------ #
    # 识别
    # ------------------------------------------------------------------ #
    async def analyze_data_url(self, data_url: str, extra_hint: str = "") -> VisionResult:
        prompt = VISION_PROMPT
        if extra_hint:
            prompt += f"\n\n用户补充线索：{extra_hint}"

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": self.settings.vision_detail},
                },
            ],
        }]

        # 视觉 + JSON 输出，思维链较长，给足预算
        resp = await self.client.chat(
            messages,
            max_tokens=max(self.settings.deepseek_max_tokens, 3000),
            json_mode=True,
        )
        raw = resp.get("content") or ""
        parsed = _loads_lenient(raw)
        if not parsed:
            logger.warning("图片识别返回无法解析为 JSON，原文前 300 字：%s", raw[:300])
            return VisionResult(
                is_travel_image=False,
                scene_type="未知",
                description="图片识别结果解析失败，请重试或换一张更清晰的图片。",
                confidence="low",
                raw=raw,
            )

        return VisionResult(
            is_travel_image=bool(parsed.get("is_travel_image", True)),
            scene_type=str(parsed.get("scene_type") or "景点"),
            landmarks=[str(x) for x in (parsed.get("landmarks") or []) if x],
            city=_opt_str(parsed.get("city")),
            country=_opt_str(parsed.get("country")),
            region=_opt_str(parsed.get("region")),
            description=str(parsed.get("description") or ""),
            confidence=_conf(parsed.get("confidence")),
            raw=raw,
        )

    async def analyze_file(self, path: str | Path, extra_hint: str = "") -> VisionResult:
        return await self.analyze_data_url(self.encode_file(path), extra_hint)

    async def analyze_bytes(
        self, data: bytes, mime: str = "image/jpeg", extra_hint: str = ""
    ) -> VisionResult:
        return await self.analyze_data_url(self.encode_bytes(data, mime), extra_hint)

    # ------------------------------------------------------------------ #
    # 把识别结果转成给模型看的文本
    # ------------------------------------------------------------------ #
    @staticmethod
    def render(result: VisionResult, coords: tuple[float, float] | None = None) -> str:
        if not result.is_travel_image:
            return (
                "【图片识别】未能从图片中识别出旅游景点。\n"
                f"识别说明：{result.description}\n"
                f"场景判断：{result.scene_type}"
            )
        lines = [
            "【图片识别结果】",
            f"场景类型：{result.scene_type}",
            f"识别到的地标：{'、'.join(result.landmarks) if result.landmarks else '未能确定'}",
            f"所在地区：{result.country or '未知'}"
            + (f" · {result.region}" if result.region else "")
            + (f" · {result.city}" if result.city else ""),
            f"置信度：{result.confidence}",
            f"图片内容：{result.description}",
        ]
        if coords:
            lines.append(f"估算坐标：{coords[0]:.4f}, {coords[1]:.4f}")
        lines.append("（如置信度为 low，请在回答中如实说明不确定，并请用户补充线索）")
        return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 辅助
# ---------------------------------------------------------------------- #
def _loads_lenient(text: str) -> dict[str, Any]:
    """尽量从模型输出中解析出 JSON 对象。"""
    if not text:
        return {}
    t = text.strip()
    # 去掉 markdown 代码块围栏
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    # 退一步：取第一个 { 到最后一个 } 之间的内容
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(t[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"null", "none", "unknown", "未知", "不确定"}:
        return None
    return s


def _conf(v: Any) -> str:
    s = str(v or "").strip().lower()
    return s if s in {"high", "medium", "low"} else "medium"
