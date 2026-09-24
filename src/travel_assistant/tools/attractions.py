"""景点检索工具。"""

from __future__ import annotations

from typing import Any

from ..models import ToolResult, ToolSpec
from .attractions_data import ATTRACTIONS, BY_CITY, BY_ID
from .base import BaseTool
from .cities import CITIES
from .common import haversine_km, normalize_city


class SearchAttractionsTool(BaseTool):
    """按城市 / 关键词 / 标签检索景点。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_attractions",
            description=(
                "检索景点。可按城市、关键词、标签（如「亲子」「免费」「夜景」「世界遗产」）筛选，"
                "并按评分或游玩时长排序。规划行程、回答「有什么好玩」时必须先调用本工具获取真实数据，"
                "不要凭记忆编造景点信息。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如「杭州」「巴黎」"},
                    "keyword": {"type": "string", "description": "名称或描述关键词，可选"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签过滤，如 ['亲子','免费']，可选",
                    },
                    "max_ticket": {"type": "number", "description": "门票上限（元），可选"},
                    "sort_by": {
                        "type": "string",
                        "enum": ["rating", "hours", "ticket"],
                        "description": "排序字段，默认按评分",
                    },
                    "limit": {"type": "integer", "description": "返回条数，默认 8，最大 20"},
                },
                "required": ["city"],
            },
        )

    async def run(
        self,
        city: str,
        keyword: str | None = None,
        tags: list[str] | None = None,
        max_ticket: float | None = None,
        sort_by: str = "rating",
        limit: int = 8,
        **_: Any,
    ) -> ToolResult:
        norm = normalize_city(city)
        items = list(BY_CITY.get(norm or "", []))

        if not items:
            available = "、".join(sorted(BY_CITY.keys()))
            return ToolResult.success(
                {"city": norm, "count": 0, "items": [], "available_cities": sorted(BY_CITY.keys())},
                summary=(
                    f"内置景点库暂无「{city}」的数据。当前收录城市：{available}。"
                    "你可以基于常识继续为用户规划，但需说明这些是通用建议而非本地库数据。"
                ),
            )

        if keyword:
            kw = keyword.strip()
            items = [
                a for a in items
                if kw in a["name"] or kw in a["description"] or any(kw in t for t in a["tags"])
            ]
        if tags:
            want = {t.strip() for t in tags if t and t.strip()}
            items = [a for a in items if want & set(a["tags"])]
        if max_ticket is not None:
            items = [a for a in items if a["ticket_cny"] <= float(max_ticket)]

        key = {"rating": "rating", "hours": "suggested_hours", "ticket": "ticket_cny"}.get(
            sort_by, "rating"
        )
        reverse = sort_by != "ticket"
        items.sort(key=lambda a: a[key], reverse=reverse)

        limit = max(1, min(int(limit or 8), 20))
        picked = items[:limit]
        city_meta = CITIES.get(norm or "", {})

        summary_lines = [f"【{norm}】共匹配 {len(items)} 个景点，返回前 {len(picked)} 个："]
        for a in picked:
            summary_lines.append("  " + _brief(a))
        if city_meta:
            summary_lines.append(
                f"城市特色：{city_meta.get('highlights','')}；最佳季节：{city_meta.get('best_season','')}"
            )

        return ToolResult.success(
            {
                "city": norm,
                "count": len(items),
                "city_meta": {
                    "highlights": city_meta.get("highlights", ""),
                    "best_season": city_meta.get("best_season", ""),
                    "country": city_meta.get("country", "中国"),
                },
                "items": picked,
            },
            summary="\n".join(summary_lines),
        )


class AttractionDetailTool(BaseTool):
    """查询单个景点的详情与游玩贴士。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="attraction_detail",
            description="查询某个景点的详细信息、门票、建议游玩时长与实用贴士。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "景点名称，支持模糊匹配"},
                    "id": {"type": "string", "description": "景点 id（优先），如 bj-gugong"},
                },
                "required": [],
            },
        )

    async def run(self, name: str | None = None, id: str | None = None, **_: Any) -> ToolResult:
        item: dict[str, Any] | None = None
        if id and id in BY_ID:
            item = BY_ID[id]
        elif name:
            kw = name.strip()
            for a in ATTRACTIONS:
                if a["name"] == kw:
                    item = a
                    break
            if item is None:
                for a in ATTRACTIONS:
                    if kw in a["name"] or a["name"] in kw:
                        item = a
                        break
        if item is None:
            return ToolResult.failure(f"景点库中未找到「{name or id}」")

        lines = [
            f"{item['name']}（{item['city']}·{item['category']}）",
            f"评分 {item['rating']}｜门票 {'免费' if item['ticket_cny'] <= 0 else '¥%g' % item['ticket_cny']}"
            f"｜建议 {item['suggested_hours']:g} 小时｜最佳季节 {item['best_season']}",
            f"简介：{item['description']}",
            f"标签：{'、'.join(item['tags'])}",
        ]
        if item["tips"]:
            lines.append("贴士：")
            lines.extend(f"  - {t}" for t in item["tips"])
        return ToolResult.success(item, summary="\n".join(lines))


class NearbyAttractionsTool(BaseTool):
    """按坐标查找周边景点（用于「这是哪，附近还能玩什么」）。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="nearby_attractions",
            description=(
                "根据经纬度或城市查找附近景点，按距离排序。"
                "识别出图片拍摄地点后，用它来推荐周边可一并游玩的地方。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "纬度"},
                    "lon": {"type": "number", "description": "经度"},
                    "city": {"type": "string", "description": "城市名（无坐标时使用）"},
                    "radius_km": {"type": "number", "description": "搜索半径，默认 30 公里"},
                    "exclude": {"type": "string", "description": "要排除的景点名（通常是已识别出的那个）"},
                    "limit": {"type": "integer", "description": "返回条数，默认 6"},
                },
                "required": [],
            },
        )

    async def run(
        self,
        lat: float | None = None,
        lon: float | None = None,
        city: str | None = None,
        radius_km: float = 30.0,
        exclude: str | None = None,
        limit: int = 6,
        **_: Any,
    ) -> ToolResult:
        norm_city = normalize_city(city) if city else None

        if lat is None or lon is None:
            if not norm_city or norm_city not in CITIES:
                return ToolResult.failure("需要提供坐标（lat/lon）或一个内置收录的城市名")
            meta = CITIES[norm_city]
            lat, lon = float(meta["lat"]), float(meta["lon"])

        scored: list[tuple[float, dict[str, Any]]] = []
        for a in ATTRACTIONS:
            if a["lat"] is None or a["lon"] is None:
                continue
            if exclude and (exclude in a["name"] or a["name"] in exclude):
                continue
            d = haversine_km(float(lat), float(lon), float(a["lat"]), float(a["lon"]))
            if d <= radius_km:
                scored.append((d, a))

        scored.sort(key=lambda x: x[0])
        limit = max(1, min(int(limit or 6), 20))
        picked = [dict(a, distance_km=d) for d, a in scored[:limit]]

        if not picked:
            return ToolResult.success(
                {"count": 0, "items": []},
                summary=f"该位置 {radius_km:g} 公里内没有内置景点数据，可放宽半径或换城市检索。",
            )

        lines = [f"附近 {radius_km:g} 公里内共 {len(scored)} 个景点，最近 {len(picked)} 个："]
        lines.extend(
            f"  - {a['name']}（{a['city']}·{a['category']}，约 {a['distance_km']:g} 公里，评分 {a['rating']}）"
            for a in picked
        )
        return ToolResult.success({"count": len(scored), "items": picked}, summary="\n".join(lines))


def _brief(a: dict[str, Any]) -> str:
    price = "免费" if a["ticket_cny"] <= 0 else f"¥{a['ticket_cny']:g}"
    return (
        f"{a['name']}（{a['category']}｜评分{a['rating']}｜门票{price}｜"
        f"建议{a['suggested_hours']:g}小时｜最佳{a['best_season']}）：{a['description']}"
    )
