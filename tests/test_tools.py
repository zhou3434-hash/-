"""工具层单元测试（不调用模型 API）。"""

from __future__ import annotations

import pytest

from travel_assistant.tools import build_default_registry
from travel_assistant.tools.attractions_data import ATTRACTIONS, BY_CITY, BY_ID
from travel_assistant.tools.common import estimate_intercity, haversine_km, normalize_city


@pytest.fixture
def reg():
    return build_default_registry()


# ---------------------------------------------------------------------- #
# 数据完整性
# ---------------------------------------------------------------------- #
def test_attractions_loaded():
    assert len(ATTRACTIONS) >= 60
    assert len(BY_CITY) >= 15


def test_attraction_ids_unique():
    ids = [a["id"] for a in ATTRACTIONS]
    assert len(ids) == len(set(ids)), "景点 id 必须唯一"


def test_all_attractions_have_coords():
    """坐标是「周边景点」与识图定位的基础，不允许缺失。"""
    missing = [a["name"] for a in ATTRACTIONS if a["lat"] is None or a["lon"] is None]
    assert not missing, f"以下景点缺少坐标：{missing}"


def test_all_attractions_have_tips_and_desc():
    bad = [a["name"] for a in ATTRACTIONS if not a["description"] or not a["tips"]]
    assert not bad, f"以下景点缺少简介或贴士：{bad}"


def test_attraction_city_in_cities_table():
    from travel_assistant.tools.cities import CITIES

    unknown = {a["city"] for a in ATTRACTIONS} - set(CITIES)
    assert not unknown, f"景点所属城市不在城市表中：{unknown}"


# ---------------------------------------------------------------------- #
# 城市名归一化
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("杭州", "杭州"),
        ("杭州市", "杭州"),
        ("魔都", "上海"),
        ("蓉城", "成都"),
        ("金陵", "南京"),
        ("青城山", "都江堰"),
        ("宏村", "黄山"),
        (" 杭州 ", "杭州"),
        ("Tokyo", "东京"),
    ],
)
def test_normalize_city(raw, expected):
    assert normalize_city(raw) == expected


def test_distance_sanity():
    d = haversine_km(39.9042, 116.4074, 31.2304, 121.4737)  # 北京-上海
    assert 1000 < d < 1200


def test_intercity_estimate():
    r = estimate_intercity("北京", "三亚")
    assert r["ok"]
    assert r["suggest_mode"] == "飞机"
    assert r["cost_air_cny"] > 0

    r2 = estimate_intercity("上海", "杭州")
    assert r2["ok"]
    assert r2["suggest_mode"] == "高铁"


# ---------------------------------------------------------------------- #
# 各工具行为
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_attractions(reg):
    r = await reg.execute("search_attractions", {"city": "杭州", "limit": 3})
    assert r.ok
    assert r.data["count"] == 5
    assert len(r.data["items"]) == 3


@pytest.mark.asyncio
async def test_search_unknown_city_returns_hint(reg):
    """未收录城市不能报错，而要给出可用城市提示，让模型能自行补救。"""
    r = await reg.execute("search_attractions", {"city": "火星"})
    assert r.ok
    assert r.data["count"] == 0
    assert r.data["available_cities"]
    assert "暂无" in r.summary


@pytest.mark.asyncio
async def test_search_with_tag_filter(reg):
    r = await reg.execute("search_attractions", {"city": "北京", "tags": ["免费"], "limit": 20})
    assert r.ok
    assert all("免费" in a["tags"] for a in r.data["items"])


@pytest.mark.asyncio
async def test_attraction_detail(reg):
    r = await reg.execute("attraction_detail", {"name": "西湖"})
    assert r.ok
    assert r.data["id"] == "hz-xihu"
    assert "免费" in r.summary


@pytest.mark.asyncio
async def test_attraction_detail_missing(reg):
    r = await reg.execute("attraction_detail", {"name": "不存在的地方"})
    assert not r.ok


@pytest.mark.asyncio
async def test_nearby_attractions_by_coords(reg):
    """埃菲尔铁塔 3 公里内的景点应合理（卢浮宫/圣母院/蒙马特）。"""
    r = await reg.execute(
        "nearby_attractions",
        {"lat": 48.8584, "lon": 2.2945, "radius_km": 5, "exclude": "埃菲尔铁塔"},
    )
    assert r.ok
    names = [a["name"] for a in r.data["items"]]
    assert any("卢浮宫" in n for n in names)
    assert all("埃菲尔铁塔" not in n for n in names)


@pytest.mark.asyncio
async def test_nearby_attractions_exclude_works(reg):
    r = await reg.execute("nearby_attractions", {"city": "杭州", "exclude": "西湖", "limit": 10})
    assert r.ok
    assert all(not n["name"].startswith("西湖") for n in r.data["items"])


@pytest.mark.asyncio
async def test_plan_itinerary_structure(reg):
    r = await reg.execute("plan_itinerary", {"city": "成都", "days": 3})
    assert r.ok
    assert r.data["days"] == 3
    assert len(r.data["daily"]) == 3
    day1 = r.data["daily"][0]
    assert day1["items"], "第一天不应为空"
    for it in day1["items"]:
        assert ":" in it["time"]


@pytest.mark.asyncio
async def test_plan_itinerary_must_visit_included(reg):
    r = await reg.execute(
        "plan_itinerary", {"city": "杭州", "days": 2, "must_visit": ["宋城·千古情"]}
    )
    assert r.ok
    all_names = [it["name"] for d in r.data["daily"] for it in d["items"]]
    assert "宋城·千古情" in all_names


@pytest.mark.asyncio
async def test_plan_itinerary_elderly_reduces_load(reg):
    normal = await reg.execute("plan_itinerary", {"city": "杭州", "days": 2, "pace": "标准"})
    elder = await reg.execute(
        "plan_itinerary", {"city": "杭州", "days": 2, "pace": "标准", "elderly": True}
    )
    assert normal.ok and elder.ok
    max_normal = max(d["hours"] for d in normal.data["daily"])
    max_elder = max(d["hours"] for d in elder.data["daily"])
    assert max_elder <= max_normal, "有老人同行时单日强度不应更高"
    assert any("老人" in n for n in elder.data["notes"])


@pytest.mark.asyncio
async def test_plan_itinerary_unknown_city_fails_clearly(reg):
    r = await reg.execute("plan_itinerary", {"city": "瓦坎达", "days": 2})
    assert not r.ok
    assert "暂无" in (r.error or "")


@pytest.mark.asyncio
async def test_estimate_budget_totals(reg):
    r = await reg.execute(
        "estimate_budget",
        {"city": "杭州", "days": 3, "people": 2, "tier": "舒适", "origin": "上海"},
    )
    assert r.ok
    d = r.data
    assert d["total"] > 0
    assert abs(d["per_person"] - d["total"] / 2) < 1
    # 各项之和应等于总计
    parts = d["transport"] + d["hotel"] + d["food"] + d["tickets"] + d["local_transit"] + d["shopping"] + d["misc"]
    assert abs(parts - d["total"]) < 1


@pytest.mark.asyncio
async def test_estimate_budget_tier_ordering(reg):
    cheap = await reg.execute("estimate_budget", {"city": "杭州", "days": 3, "tier": "经济"})
    lux = await reg.execute("estimate_budget", {"city": "杭州", "days": 3, "tier": "高端"})
    assert cheap.ok and lux.ok
    assert cheap.data["total"] < lux.data["total"]


@pytest.mark.asyncio
async def test_estimate_budget_uses_ticket_total(reg):
    r = await reg.execute(
        "estimate_budget", {"city": "杭州", "days": 3, "ticket_total": 999}
    )
    assert r.ok
    assert r.data["tickets"] == 999
    assert "行程规划" in r.data["ticket_source"]


@pytest.mark.asyncio
async def test_estimate_budget_same_origin_no_intercity(reg):
    r = await reg.execute("estimate_budget", {"city": "杭州", "days": 2, "origin": "杭州"})
    assert r.ok
    assert r.data["transport"] == 0


@pytest.mark.asyncio
async def test_unknown_tool_returns_failure(reg):
    r = await reg.execute("no_such_tool", {})
    assert not r.ok
    assert "未注册" in (r.error or "")


@pytest.mark.asyncio
async def test_tool_spec_schema_valid(reg):
    """工具声明必须能生成合法的 OpenAI 函数调用结构。"""
    for t in reg.api_tools():
        assert t["type"] == "function"
        fn = t["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        for k, v in fn["parameters"].get("properties", {}).items():
            assert "type" in v, f"{fn['name']}.{k} 缺少 type"
