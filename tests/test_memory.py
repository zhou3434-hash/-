"""记忆系统测试（使用临时数据库，不触碰真实记忆）。"""

from __future__ import annotations

import pytest

from travel_assistant.config import Settings
from travel_assistant.memory import MemoryStore
from travel_assistant.models import Message, Role


@pytest.fixture
def store(tmp_path):
    s = Settings(
        deepseek_api_key="sk-test",
        memory_db_path=str(tmp_path / "m.sqlite3"),
        memory_short_term_turns=3,
    )
    st = MemoryStore(s)
    yield st
    st.close()


def _msg(store, sid, role, text, **kw):
    store.add_message(sid, Message(role=role, content=text, **kw))


# ---------------------------------------------------------------------- #
# 会话与消息
# ---------------------------------------------------------------------- #
def test_session_lifecycle(store):
    sid = store.ensure_session()
    assert sid
    # 幂等
    assert store.ensure_session(sid) == sid
    assert any(s["id"] == sid for s in store.list_sessions())


def test_message_roundtrip_preserves_order(store):
    sid = store.ensure_session()
    for i in range(5):
        _msg(store, sid, Role.USER, f"问题{i}")
        _msg(store, sid, Role.ASSISTANT, f"回答{i}")
    hist = store.get_history(sid)
    assert len(hist) == 10
    assert hist[0].content == "问题0"
    assert hist[-1].content == "回答4"


def test_count_and_delete_session(store):
    sid = store.ensure_session()
    _msg(store, sid, Role.USER, "hi")
    assert store.count_messages(sid) == 1
    store.delete_session(sid)
    assert store.count_messages(sid) == 0
    assert not any(s["id"] == sid for s in store.list_sessions())


def test_tool_call_message_persisted(store):
    sid = store.ensure_session()
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "search_attractions", "arguments": '{"city":"杭州"}'}}]
    _msg(store, sid, Role.ASSISTANT, "", tool_calls=calls)
    _msg(store, sid, Role.TOOL, "结果", tool_call_id="c1", name="search_attractions")
    hist = store.get_history(sid)
    assert hist[0].tool_calls == calls
    assert hist[1].tool_call_id == "c1"
    # 转为 API 格式时字段正确
    api = hist[0].to_api()
    assert api["role"] == "assistant" and api["tool_calls"]
    api2 = hist[1].to_api()
    assert api2["role"] == "tool" and api2["tool_call_id"] == "c1"


# ---------------------------------------------------------------------- #
# 短期窗口
# ---------------------------------------------------------------------- #
def test_short_term_window_limits_turns(store):
    """memory_short_term_turns=3，应只取最近 3 个用户轮次（含其后的助手回复）。"""
    sid = store.ensure_session()
    for i in range(6):
        _msg(store, sid, Role.USER, f"问题{i}")
        _msg(store, sid, Role.ASSISTANT, f"回答{i}")
    ctx = store.get_context_messages(sid)
    users = [m for m in ctx if m.role is Role.USER]
    assert len(users) == 3
    assert users[0].content == "问题3"
    assert ctx[-1].content == "回答5"


def test_context_does_not_start_with_orphan_tool(store):
    """窗口首条不能是 tool 消息，否则 API 会因缺少配对的 assistant 而报 400。"""
    sid = store.ensure_session()
    _msg(store, sid, Role.USER, "问题0")
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "search_attractions", "arguments": "{}"}}]
    _msg(store, sid, Role.ASSISTANT, "", tool_calls=calls)
    _msg(store, sid, Role.TOOL, "结果", tool_call_id="c1", name="search_attractions")
    _msg(store, sid, Role.ASSISTANT, "回答0")
    for i in range(1, 5):
        _msg(store, sid, Role.USER, f"问题{i}")
        _msg(store, sid, Role.ASSISTANT, f"回答{i}")
    ctx = store.get_context_messages(sid)
    assert ctx[0].role is not Role.TOOL, "窗口不能以孤立的工具消息开头"


def test_full_history_not_truncated_in_db(store):
    """窗口会截断，但数据库必须保留全部历史 —— 这是「不忘记上文」的底线。"""
    sid = store.ensure_session()
    for i in range(20):
        _msg(store, sid, Role.USER, f"问题{i}")
        _msg(store, sid, Role.ASSISTANT, f"回答{i}")
    assert store.count_messages(sid) == 40
    assert len(store.get_context_messages(sid)) < 40


# ---------------------------------------------------------------------- #
# 长期记忆
# ---------------------------------------------------------------------- #
def test_profile_upsert_and_update(store):
    assert store.upsert_profile("home_city", "上海")
    # 相同值不重复写入
    assert not store.upsert_profile("home_city", "上海")
    # 新值覆盖
    assert store.upsert_profile("home_city", "杭州")
    assert store.get_profile()["home_city"] == "杭州"


def test_profile_rejects_unknown_key(store):
    """白名单机制：防止模型往画像里塞任意键。"""
    assert not store.upsert_profile("evil_key", "x")
    assert "evil_key" not in store.get_profile()


def test_profile_rejects_empty_value(store):
    assert not store.upsert_profile("home_city", "   ")
    assert "home_city" not in store.get_profile()


def test_profile_text_renders_chinese_labels(store):
    store.upsert_profile("home_city", "上海")
    store.upsert_profile("dietary", "海鲜过敏")
    text = store.profile_text()
    assert "常住城市：上海" in text
    assert "饮食偏好与忌口：海鲜过敏" in text


def test_profile_text_empty(store):
    assert "暂无长期记忆" in store.profile_text()


def test_profile_delete_and_clear(store):
    store.upsert_profile("home_city", "上海")
    store.upsert_profile("budget_total", "5000")
    store.delete_profile("home_city")
    assert "home_city" not in store.get_profile()
    store.clear_profile()
    assert store.get_profile() == {}


def test_profile_persists_across_instances(tmp_path):
    """长期记忆必须跨进程/跨实例保留 —— 这是用户最关心的「记住我」。"""
    path = str(tmp_path / "persist.sqlite3")
    s = Settings(deepseek_api_key="sk-test", memory_db_path=path)
    a = MemoryStore(s)
    a.upsert_profile("home_city", "成都")
    a.close()

    b = MemoryStore(Settings(deepseek_api_key="sk-test", memory_db_path=path))
    assert b.get_profile()["home_city"] == "成都"
    b.close()


def test_save_and_list_trips(store):
    sid = store.ensure_session()
    store.save_trip(session_id=sid, city="杭州", days=3, people=2, tier="舒适",
                    plan={"x": 1}, budget={"total": 5000})
    trips = store.list_trips()
    assert trips and trips[0]["city"] == "杭州"
    assert trips[0]["days"] == 3


# ---------------------------------------------------------------------- #
# 会话列表与 5 个上限（网页侧栏用）
# ---------------------------------------------------------------------- #
def test_list_sessions_sorted_has_preview(store):
    sid = store.ensure_session()
    _msg(store, sid, Role.USER, "帮我规划杭州3天")
    _msg(store, sid, Role.ASSISTANT, "好的")
    rows = store.list_sessions_sorted(limit=5)
    assert rows
    top = rows[0]
    assert top["id"] == sid
    assert top["msg_count"] == 2
    assert "杭州" in top["preview"]


def test_list_sessions_sorted_preview_truncated(store):
    sid = store.ensure_session()
    _msg(store, sid, Role.USER, "杭" * 100)
    rows = store.list_sessions_sorted(limit=5)
    assert len(rows[0]["preview"]) <= 41  # 40 + 省略号


def test_trim_sessions_keeps_only_limit(store):
    ids = [store.ensure_session(None, f"会话{i}") for i in range(8)]
    removed = store.trim_sessions(keep=5)
    assert len(removed) == 3
    assert len(store.list_sessions(limit=50)) == 5
    # 保留的应是最新的 5 个
    kept = {s["id"] for s in store.list_sessions_sorted(limit=5)}
    assert set(ids[-5:]) == kept


def test_trim_sessions_deletes_messages_too(store):
    ids = [store.ensure_session() for _ in range(7)]
    for sid in ids:
        _msg(store, sid, Role.USER, "内容")
    store.trim_sessions(keep=5)
    for sid in ids[:2]:
        assert store.count_messages(sid) == 0


def test_trim_sessions_keeps_long_term_memory(store):
    """关键：清理旧会话不能清掉用户画像 —— 用户要求「记忆依旧存储」。"""
    store.upsert_profile("home_city", "上海")
    store.upsert_profile("dietary", "海鲜过敏")
    for _ in range(9):
        store.ensure_session()
    store.trim_sessions(keep=5)
    prof = store.get_profile()
    assert prof.get("home_city") == "上海"
    assert prof.get("dietary") == "海鲜过敏"


def test_trim_sessions_with_fewer_than_limit(store):
    store.ensure_session()
    store.ensure_session()
    assert store.trim_sessions(keep=5) == []
    assert len(store.list_sessions(limit=50)) == 2


def test_set_session_title(store):
    sid = store.ensure_session()
    store.set_session_title(sid, "杭州三日游")
    rows = store.list_sessions_sorted(limit=5)
    assert rows[0]["title"] == "杭州三日游"


def test_set_session_title_truncates(store):
    sid = store.ensure_session()
    store.set_session_title(sid, "标" * 200)
    assert len(store.list_sessions_sorted(limit=5)[0]["title"]) == 60
