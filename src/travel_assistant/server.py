"""FastAPI 服务：为「旅行出行助手」提供网页界面与 API。

启动::

    .venv\\Scripts\\python.exe -m travel_assistant.server
    # 或
    .venv\\Scripts\\python.exe -m uvicorn travel_assistant.server:app --reload

然后浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agent import TravelAssistant
from .config import PROJECT_ROOT, get_settings
from .memory import PROFILE_KEYS, MemoryStore
from .tools.attractions_data import ATTRACTIONS, BY_CITY

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()
app = FastAPI(title="旅行出行助手", version="0.1.0", description="基于 DeepSeek 的旅游规划智能体")

#: 已加载的智能体（按会话 id 缓存，保证同一会话的记忆连续）
_agents: dict[str, TravelAssistant] = {}

WEB_DIR = PROJECT_ROOT / "web"
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"


def _get_agent(session_id: str | None) -> TravelAssistant:
    sid = session_id or "web-default"
    agent = _agents.get(sid)
    if agent is None:
        agent = TravelAssistant(settings, session_id=sid)
        _agents[sid] = agent
    return agent


# ---------------------------------------------------------------------- #
# 页面
# ---------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = WEB_DIR / "index.html"
    if not html.exists():
        return HTMLResponse("<h1>缺少 web/index.html</h1>", status_code=500)
    return HTMLResponse(html.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model": settings.deepseek_model,
        "key_configured": bool(settings.deepseek_api_key),
        "attractions": len(ATTRACTIONS),
        "cities": sorted(BY_CITY.keys()),
        "tools": _get_agent(None).registry.names(),
    }


# ---------------------------------------------------------------------- #
# 对话（SSE 流式）
# ---------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    message: str = ""
    session_id: str | None = None
    hint: str = ""
    image_names: list[str] = []


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """流式对话。图片需先用 /api/upload 上传，再传回文件名。"""
    agent = _get_agent(req.session_id)

    async def gen():
        images: list[Path] = []
        for name in req.image_names:
            p = (UPLOAD_DIR / name).resolve()
            if UPLOAD_DIR.resolve() in p.parents and p.exists():
                images.append(p)

        # 首轮对话时，用用户第一句话自动命名会话，便于侧栏区分
        try:
            if agent.memory.count_messages(agent.session_id) == 0 and req.message.strip():
                agent.memory.set_session_title(agent.session_id, req.message.strip().replace("\n", " ")[:24])
            elif images and agent.memory.count_messages(agent.session_id) == 0:
                agent.memory.set_session_title(agent.session_id, "图片识别")
        except Exception:  # noqa: BLE001
            logger.debug("自动命名会话失败", exc_info=True)

        try:
            async for ev in agent.stream(req.message, image_paths=images, image_hint=req.hint):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.exception("对话处理异常")
            err = {"type": "error", "message": f"{type(e).__name__}: {e}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传图片，返回文件名（供 /api/chat 引用）。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "img.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(400, f"不支持的图片格式：{suffix}")
    data = await file.read()
    limit = settings.vision_max_image_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(400, f"图片超过 {settings.vision_max_image_mb} MB 限制")
    name = f"{uuid.uuid4().hex[:12]}{suffix}"
    (UPLOAD_DIR / name).write_bytes(data)
    return {
        "name": name,
        "size": len(data),
        "preview": "data:image/jpeg;base64," + base64.b64encode(data).decode()[:20000],
    }


# ---------------------------------------------------------------------- #
# 记忆与数据
# ---------------------------------------------------------------------- #
@app.get("/api/memory")
async def get_memory(session_id: str | None = None) -> dict[str, Any]:
    agent = _get_agent(session_id)
    prof = agent.memory.get_profile()
    return {
        "profile": prof,
        "labels": PROFILE_KEYS,
        "session_id": agent.session_id,
        "message_count": agent.memory.count_messages(agent.session_id),
    }


@app.delete("/api/memory")
async def clear_memory(key: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    agent = _get_agent(session_id)
    if key:
        agent.memory.delete_profile(key)
    else:
        agent.memory.clear_profile()
    return {"ok": True, "profile": agent.memory.get_profile()}


@app.get("/api/history")
async def history(session_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    agent = _get_agent(session_id)
    msgs = agent.memory.get_history(agent.session_id, limit=limit)
    return {
        "session_id": agent.session_id,
        "messages": [
            {"role": m.role.value, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in msgs
            if m.role.value in ("user", "assistant") and m.content
        ],
    }


@app.delete("/api/session")
async def reset_session(session_id: str | None = None) -> dict[str, Any]:
    """开始新会话（保留长期记忆）。"""
    agent = _get_agent(session_id)
    new_id = agent.memory.ensure_session(None, "新对话")
    agent.session_id = new_id
    return {"ok": True, "session_id": new_id}


@app.get("/api/sessions")
async def sessions(session_id: str | None = None, limit: int = 5) -> dict[str, Any]:
    """最近的会话列表（网页侧栏「历史对话」用），最多 limit 条。"""
    agent = _get_agent(session_id)
    rows = agent.memory.list_sessions_sorted(limit=max(1, min(int(limit or 5), 50)))
    return {"sessions": rows, "current": agent.session_id}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str, session_id: str | None = None) -> dict[str, Any]:
    """删除一个会话及其消息（长期记忆不受影响）。"""
    agent = _get_agent(session_id)
    agent.memory.delete_session(sid)
    _agents.pop(sid, None)
    return {"ok": True, "deleted": sid}


@app.post("/api/session/new")
async def new_session(session_id: str | None = None, keep: int = 5) -> dict[str, Any]:
    """新建会话，并且只保留最近 keep 个会话。

    被挤掉的旧会话连同消息一起删除；**长期记忆（用户画像）不受影响**。
    """
    agent = _get_agent(session_id)
    new_id = agent.memory.ensure_session(None, "新对话")
    removed = agent.memory.trim_sessions(keep=max(1, min(int(keep or 5), 20)))
    for sid in removed:
        _agents.pop(sid, None)
    agent.session_id = new_id
    return {"ok": True, "session_id": new_id, "removed": removed}


@app.patch("/api/sessions/{sid}")
async def rename_session(sid: str, title: str, session_id: str | None = None) -> dict[str, Any]:
    """重命名会话。"""
    agent = _get_agent(session_id)
    agent.memory.set_session_title(sid, title)
    return {"ok": True, "session_id": sid, "title": title[:60]}


@app.get("/api/trips")
async def trips(session_id: str | None = None) -> dict[str, Any]:
    agent = _get_agent(session_id)
    return {"trips": agent.memory.list_trips()}


@app.get("/api/cities")
async def cities() -> dict[str, Any]:
    return {
        "total": len(ATTRACTIONS),
        "cities": [
            {
                "city": c,
                "count": len(items),
                "attractions": [a["name"] for a in items],
            }
            for c, items in sorted(BY_CITY.items(), key=lambda x: -len(x[1]))
        ],
    }


@app.on_event("shutdown")
async def _shutdown() -> None:
    for a in _agents.values():
        try:
            await a.aclose()
        except Exception:  # noqa: BLE001
            pass
    _agents.clear()


def main() -> None:
    import uvicorn

    host = "127.0.0.1"
    port = 8000
    print(f"\n  旅行出行助手已启动 →  http://{host}:{port}\n  按 Ctrl+C 停止\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
