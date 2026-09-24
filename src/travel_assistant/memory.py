"""记忆系统。

两层记忆：

* **短期记忆** —— 当前会话的完整对话历史（持久化在 SQLite，进程重启不丢）。
  注入模型上下文时按 `MEMORY_SHORT_TERM_TURNS` 截取最近 N 轮，
  避免上下文无限增长导致费用与延迟失控。
* **长期记忆** —— 从对话中抽取的用户画像（常住城市、预算档位、口味偏好、
  忌口、同行人、证件/签证信息等），跨会话保留，每轮自动注入系统提示。
  这是「不会说了下句忘了上句」的根本保障。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .models import Message, Role

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tool_calls  TEXT,
    tool_call_id TEXT,
    name        TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS profile (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    confidence  REAL DEFAULT 0.8,
    source      TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    city        TEXT,
    days        INTEGER,
    people      INTEGER,
    tier        TEXT,
    plan_json   TEXT,
    budget_json TEXT,
    created_at  TEXT NOT NULL
);
"""

#: 允许写入用户画像的字段白名单（防止模型乱塞键）
PROFILE_KEYS = {
    "home_city": "常住城市",
    "budget_tier": "预算档位（经济/舒适/高端）",
    "budget_total": "总预算",
    "travel_style": "出行风格（休闲/特种兵/亲子/摄影等）",
    "companions": "同行人情况",
    "dietary": "饮食偏好与忌口",
    "taboo": "禁忌与不喜欢",
    "interests": "兴趣偏好",
    "visited": "去过的目的地",
    "wishlist": "想去的目的地",
    "age_group": "年龄段",
    "mobility": "体力/行动力限制",
    "preferred_season": "偏好出行季节",
    "transport_pref": "交通偏好",
    "hotel_pref": "住宿偏好",
    "visa_note": "签证/证件情况",
    "name": "称呼",
}


def _now() -> str:
    """毫秒精度时间戳（ISO 格式，字符串排序即时间排序）。

    为什么不用秒级：同一秒内连续创建多个会话时，秒级时间戳完全相同，
    按 updated_at 排序结果不稳定，会导致「只保留最近 N 个会话」删错对象。
    """
    return datetime.now().isoformat(timespec="milliseconds")


class MemoryStore:
    """SQLite 记忆存储（线程安全）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.path: Path = self.settings.memory_db_file
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 会话
    # ------------------------------------------------------------------ #
    def ensure_session(self, session_id: str | None = None, title: str | None = None) -> str:
        sid = session_id or f"s-{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._lock:
            row = self._conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
                    (sid, title or "新对话", now, now),
                )
            else:
                self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
            self._conn.commit()
        return sid

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_session_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET title=? WHERE id=?", (title[:60], session_id))
            self._conn.commit()

    def list_sessions_sorted(self, limit: int = 5) -> list[dict[str, Any]]:
        """最近的会话列表，附带消息数与最后一条用户消息摘要（侧栏用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.title, s.created_at, s.updated_at,"
                "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS msg_count,"
                "       (SELECT m.content FROM messages m WHERE m.session_id = s.id"
                "          AND m.role = 'user' AND m.content <> '' ORDER BY m.id DESC LIMIT 1) AS last_user"
                " FROM sessions s ORDER BY s.updated_at DESC, s.rowid DESC LIMIT ?",
                (max(1, int(limit or 5)),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            preview = (r["last_user"] or "").strip().replace("\n", " ")
            if len(preview) > 40:
                preview = preview[:40] + "…"
            out.append({
                "id": r["id"],
                "title": r["title"] or "新对话",
                "msg_count": int(r["msg_count"] or 0),
                "preview": preview,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return out

    def trim_sessions(self, keep: int = 5) -> list[str]:
        """只保留最近 keep 个会话，删除更早的（含消息）。返回被删除的会话 id。

        注意：只删会话与消息，**长期记忆（profile）不受影响**。
        """
        keep = max(1, int(keep or 5))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM sessions ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                (keep,),
            ).fetchall()
        removed = [r["id"] for r in rows]
        for sid in removed:
            self.delete_session(sid)
        return removed

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 消息
    # ------------------------------------------------------------------ #
    def add_message(self, session_id: str, message: Message, *, persist_images: bool = False) -> None:
        """写入一条消息。图片默认不入库（体积大）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id, name, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    message.role.value,
                    message.content or "",
                    json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls else None,
                    message.tool_call_id,
                    message.name,
                    message.created_at.isoformat(timespec="seconds"),
                ),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (_now(), session_id),
            )
            self._conn.commit()

    def get_history(self, session_id: str, limit: int | None = None) -> list[Message]:
        """按时间正序取会话历史。limit 为「消息条数」上限。"""
        sql = (
            "SELECT role, content, tool_calls, tool_call_id, name, created_at "
            "FROM messages WHERE session_id=? ORDER BY id DESC"
        )
        params: tuple[Any, ...] = (session_id,)
        if limit:
            sql += " LIMIT ?"
            params = (session_id, limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out: list[Message] = []
        for r in reversed(rows):
            out.append(
                Message(
                    role=Role(r["role"]),
                    content=r["content"] or "",
                    tool_calls=json.loads(r["tool_calls"]) if r["tool_calls"] else None,
                    tool_call_id=r["tool_call_id"],
                    name=r["name"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
            )
        return out

    def get_context_messages(self, session_id: str) -> list[Message]:
        """取用于注入模型上下文的近期消息。

        以「用户轮次」为单位截取最近 N 轮，并保证不切断
        assistant(tool_calls) 与 tool 结果之间的配对关系。
        """
        turns = max(self.settings.memory_short_term_turns, 1)
        all_msgs = self.get_history(session_id)
        if not all_msgs:
            return []

        # 从后往前数第 turns 个 user 消息的位置
        user_idx = [i for i, m in enumerate(all_msgs) if m.role is Role.USER]
        start = user_idx[-turns] if len(user_idx) > turns else 0

        window = all_msgs[start:]
        # 若窗口首条是 tool 消息或带工具调用的 assistant，向前补齐，
        # 避免出现「孤立的工具结果」被 API 拒绝（必须成对出现）
        while window:
            first = window[0]
            is_orphan = first.role is Role.TOOL or (
                first.role is Role.ASSISTANT and bool(first.tool_calls)
            )
            if not is_orphan or start == 0:
                break
            start -= 1
            window = all_msgs[start:]
        return window

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------ #
    # 长期记忆：用户画像
    # ------------------------------------------------------------------ #
    def upsert_profile(self, key: str, value: str, *, confidence: float = 0.8, source: str = "") -> bool:
        """写入/更新画像字段。仅接受白名单键；内容相同则跳过。"""
        key = (key or "").strip()
        value = (value or "").strip()
        if key not in PROFILE_KEYS or not value:
            return False
        now = _now()
        with self._lock:
            row = self._conn.execute("SELECT value FROM profile WHERE key=?", (key,)).fetchone()
            if row and row["value"] == value:
                return False
            self._conn.execute(
                "INSERT INTO profile(key, value, confidence, source, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, confidence=excluded.confidence, "
                "source=excluded.source, updated_at=excluded.updated_at",
                (key, value, confidence, source, now),
            )
            self._conn.commit()
        return True

    def get_profile(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM profile ORDER BY updated_at DESC"
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def profile_text(self) -> str:
        """渲染为中文提示文本。"""
        prof = self.get_profile()
        if not prof:
            return "（暂无长期记忆，这是首次对话或尚未了解到用户偏好）"
        lines = []
        for k, v in prof.items():
            lines.append(f"- {PROFILE_KEYS.get(k, k)}：{v}")
        return "\n".join(lines)

    def delete_profile(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM profile WHERE key=?", (key,))
            self._conn.commit()

    def clear_profile(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM profile")
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 行程归档
    # ------------------------------------------------------------------ #
    def save_trip(
        self,
        *,
        session_id: str | None,
        city: str,
        days: int,
        people: int,
        tier: str,
        plan: Any = None,
        budget: Any = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO trips(session_id, city, days, people, tier, plan_json, budget_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    session_id, city, days, people, tier,
                    json.dumps(plan, ensure_ascii=False) if plan is not None else None,
                    json.dumps(budget, ensure_ascii=False) if budget is not None else None,
                    _now(),
                ),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def list_trips(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, city, days, people, tier, created_at FROM trips "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
