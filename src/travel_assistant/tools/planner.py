"""行程规划工具。

算法
----
1. 从内置景点库取出候选（可按主题标签筛选，并保证必去景点入选）；
2. 按「地理聚类 + 评分」把候选分配到每一天，控制单日总时长不超过步调上限；
3. 同一天内按最近邻顺序串联，减少来回折返；
4. 输出每日时间轴、门票合计、通勤提示与注意事项。
"""

from __future__ import annotations

from typing import Any

from ..models import ToolResult, ToolSpec
from .attractions_data import BY_CITY
from .base import BaseTool
from .cities import CITIES
from .common import haversine_km, normalize_city

#: 步调 -> 单日游玩时长上限（小时）
PACE_HOURS = {"轻松": 5.0, "标准": 7.5, "紧凑": 10.0}

#: 主题 -> 优先标签
THEME_TAGS = {
    "综合": [],
    "亲子": ["亲子", "动物园", "主题乐园", "湿地"],
    "摄影": ["摄影", "夜景", "自然风光"],
    "历史人文": ["世界遗产", "历史古迹", "博物馆", "古寺", "宗教人文", "古镇"],
    "自然风光": ["自然风光", "海岛", "山", "湿地", "徒步"],
    "美食": ["美食", "夜市", "小吃"],
    "休闲度假": ["度假", "海滩", "温泉"],
    "省钱": ["免费"],
    "夜景": ["夜景", "夜游"],
}


class PlanItineraryTool(BaseTool):
    """生成多日行程方案。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="plan_itinerary",
            description=(
                "为指定城市生成逐日行程方案（含时间轴、门票合计、动线建议）。"
                "调用前建议先用 search_attractions 了解该城市景点。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "目的地城市"},
                    "days": {"type": "integer", "description": "行程天数，1-15"},
                    "pace": {
                        "type": "string",
                        "enum": ["轻松", "标准", "紧凑"],
                        "description": "节奏，默认标准",
                    },
                    "theme": {
                        "type": "string",
                        "enum": list(THEME_TAGS.keys()),
                        "description": "主题偏好，默认综合",
                    },
                    "travelers": {"type": "integer", "description": "出行人数，默认 1"},
                    "must_visit": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "必去景点名列表，会被优先安排",
                    },
                    "start_hour": {"type": "number", "description": "每日出发时间，默认 9 点"},
                    "elderly": {
                        "type": "boolean",
                        "description": "是否有老人同行（有则在预算内进一步压缩单日强度并减少爬坡景点）",
                    },
                    "children": {
                        "type": "boolean",
                        "description": "是否有儿童同行（有则避免过长徒步与夜间行程）",
                    },
                },
                "required": ["city", "days"],
            },
        )

    async def run(
        self,
        city: str,
        days: int,
        pace: str = "标准",
        theme: str = "综合",
        travelers: int = 1,
        must_visit: list[str] | None = None,
        start_hour: float = 9.0,
        elderly: bool = False,
        children: bool = False,
        **_: Any,
    ) -> ToolResult:
        norm = normalize_city(city)
        pool = list(BY_CITY.get(norm or "", []))
        if not pool:
            return ToolResult.failure(
                f"内置景点库暂无「{city}」数据，无法生成精确行程。当前收录：{'、'.join(sorted(BY_CITY))}"
            )

        days = max(1, min(int(days or 1), 15))
        pace = pace if pace in PACE_HOURS else "标准"
        day_budget = PACE_HOURS[pace]
        travelers = max(1, int(travelers or 1))

        # 同行人强度修正
        adjust_notes: list[str] = []
        if elderly and day_budget > 5.5:
            day_budget = 5.5
            adjust_notes.append("有老人同行，已将单日游玩时长上限压缩到约 5.5 小时")
        if children and day_budget > 6.5:
            day_budget = 6.5
            adjust_notes.append("有儿童同行，已压缩单日时长并减少夜间安排")

        must_names = [m.strip() for m in (must_visit or []) if m and m.strip()]
        chosen: list[dict[str, Any]] = []
        for name in must_names:
            for a in pool:
                if (name in a["name"] or a["name"] in name) and a not in chosen:
                    chosen.append(a)
                    break

        theme_tags = set(THEME_TAGS.get(theme, []))
        rest = [a for a in pool if a not in chosen]
        if theme_tags:
            preferred = [a for a in rest if theme_tags & set(a["tags"])]
            others = [a for a in rest if a not in preferred]
            preferred.sort(key=lambda a: a["rating"], reverse=True)
            others.sort(key=lambda a: a["rating"], reverse=True)
            ordered = preferred + others
        else:
            ordered = sorted(rest, key=lambda a: a["rating"], reverse=True)

        # 按天装箱：尽量让每天的主题/地理位置接近
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(days)]
        loads = [0.0] * days
        # 先放必去（各自分配到当前最空的一天）
        for a in chosen:
            i = loads.index(min(loads))
            buckets[i].append(a)
            loads[i] += a["suggested_hours"]

        cursor = 0
        for a in ordered:
            h = a["suggested_hours"]
            placed = False
            # 优先放进已有内容且「顺路」的一天
            best_i, best_score = -1, None
            for i in range(days):
                if loads[i] + h > day_budget and buckets[i]:
                    continue
                if loads[i] + h > day_budget * 1.35:
                    continue
                if not buckets[i]:
                    score = loads[i]
                else:
                    anchor = buckets[i][-1]
                    if a["lat"] and anchor["lat"]:
                        score = haversine_km(a["lat"], a["lon"], anchor["lat"], anchor["lon"])
                    else:
                        score = 0.0
                if best_score is None or score < best_score:
                    best_score, best_i = score, i
            if best_i >= 0:
                buckets[best_i].append(a)
                loads[best_i] += h
                placed = True
            if not placed and all(loads[i] + h > day_budget for i in range(days)):
                # 所有天都满了
                if cursor >= days and len(buckets[-1]) >= 4:
                    continue
            if not placed:
                i = loads.index(min(loads))
                if len(buckets[i]) < 4:
                    buckets[i].append(a)
                    loads[i] += h

        # 保证每天至少有一个景点（若池子够大）
        for i in range(days):
            if not buckets[i] and ordered:
                a = ordered.pop(0)
                buckets[i].append(a)
                loads[i] += a["suggested_hours"]

        # 负载再平衡：贪心装箱容易把某天塞爆、某天很空，
        # 这里把超载日里「边际损失最小」的景点挪到最空的一天。
        _rebalance(buckets, loads, day_budget, protected=set(id(a) for a in chosen))

        # 日内排序：最近邻
        result_days: list[dict[str, Any]] = []
        total_ticket = 0.0
        for i, items in enumerate(buckets, start=1):
            if not items:
                result_days.append({"day": i, "items": [], "hours": 0, "tickets": 0, "note": "自由活动/机动日"})
                continue
            ordered_items = _nearest_neighbor(items)
            t = start_hour
            timeline = []
            day_ticket = 0.0
            for a in ordered_items:
                timeline.append({
                    "time": f"{int(t):02d}:{int((t % 1) * 60):02d}",
                    "name": a["name"],
                    "category": a["category"],
                    "hours": a["suggested_hours"],
                    "ticket_cny": a["ticket_cny"],
                    "tips": a["tips"][:1],
                })
                t += a["suggested_hours"] + 0.5  # 含通勤
                day_ticket += a["ticket_cny"]
            total_ticket += day_ticket
            note = _day_note(ordered_items)
            result_days.append({
                "day": i,
                "items": timeline,
                "hours": round(sum(a["suggested_hours"] for a in ordered_items), 1),
                "tickets_cny": round(day_ticket, 0),
                "note": note,
            })

        meta = CITIES.get(norm or "", {})
        notes = [
            f"最佳出行季节：{meta.get('best_season', '四季皆宜')}",
            "门票合计为单人参考价，学生/老人多有优惠",
            f"节奏「{pace}」按单日约 {day_budget:g} 小时游玩时长排布，含通勤余量",
        ]
        notes = adjust_notes + notes
        if elderly:
            notes.append("提示：灵隐寺、青城山、岳麓山等有台阶，建议量力而行或使用索道/电瓶车代步")
        if children:
            notes.append("提示：主题乐园与动物园类项目排队较久，建议早入园并备好零食饮水")
        if days > len(pool) / 1.5:
            notes.append("天数相对景点数量偏多，已安排机动日，可考虑加入周边一日游")

        summary_lines = [
            f"【{norm}】{days} 天行程（{pace}节奏｜{theme}主题｜{travelers} 人）",
        ]
        for d in result_days:
            if not d["items"]:
                summary_lines.append(f"  Day {d['day']}：{d['note']}")
                continue
            names = " → ".join(f"{it['time']} {it['name']}" for it in d["items"])
            summary_lines.append(f"  Day {d['day']}（{d['hours']:g}h，门票¥{d['tickets_cny']:g}）：{names}")
        summary_lines.append(f"  门票合计（单人）：¥{total_ticket:g}　共 {travelers} 人约 ¥{total_ticket * travelers:g}")

        payload = {
            "city": norm,
            "days": days,
            "pace": pace,
            "theme": theme,
            "travelers": travelers,
            "daily": result_days,
            "total_ticket_cny": round(total_ticket, 0),
            "total_ticket_all_cny": round(total_ticket * travelers, 0),
            "notes": notes,
        }
        return ToolResult.success(payload, summary="\n".join(summary_lines))


def _rebalance(
    buckets: list[list[dict[str, Any]]],
    loads: list[float],
    day_budget: float,
    *,
    protected: set[int] | None = None,
    max_moves: int = 12,
) -> None:
    """把超载日的景点挪到较空的日子，使每日时长尽量贴近预算。

    被用户点名「必去」的景点不动（protected 以 id() 标识）。
    """
    protected = protected or set()
    for _ in range(max_moves):
        over = [i for i, l in enumerate(loads) if l > day_budget]
        if not over:
            break
        moved = False
        # 超载最严重的一天先处理
        i = max(over, key=lambda x: loads[x])
        # 候选：最空闲的一天，且移入后不超过预算
        cand_days = sorted(
            (j for j in range(len(buckets)) if j != i),
            key=lambda j: loads[j],
        )
        # 选可移动、时长最大的那个景点（减少超载最有效）
        movable = [a for a in buckets[i] if id(a) not in protected]
        if not movable:
            # 全是必去景点，无法调整，直接跳出避免死循环
            break
        # 若没有任何一天有余量接收，再试也没用
        if not any(loads[j] < day_budget for j in cand_days):
            break
        movable.sort(key=lambda a: a["suggested_hours"], reverse=True)
        for a in movable:
            for j in cand_days:
                if loads[j] + a["suggested_hours"] <= day_budget and len(buckets[j]) < 4:
                    buckets[i].remove(a)
                    loads[i] -= a["suggested_hours"]
                    buckets[j].append(a)
                    loads[j] += a["suggested_hours"]
                    moved = True
                    break
            if moved:
                break
        if not moved:
            break


def _nearest_neighbor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """最近邻排序：从评分最高者出发依次取最近的下一个。"""
    if len(items) <= 1:
        return list(items)
    remaining = sorted(items, key=lambda a: a["rating"], reverse=True)
    route = [remaining.pop(0)]
    while remaining:
        last = route[-1]
        if last["lat"] is None:
            nxt = remaining.pop(0)
        else:
            nxt = min(
                remaining,
                key=lambda a: (
                    haversine_km(last["lat"], last["lon"], a["lat"], a["lon"])
                    if a["lat"] is not None
                    else 9999.0
                ),
            )
            remaining.remove(nxt)
        route.append(nxt)
    return route


def _day_note(items: list[dict[str, Any]]) -> str:
    cats = [a["category"] for a in items]
    if any("夜" in "".join(a["tags"]) for a in items):
        return "含夜游项目，注意末班交通时间"
    if any(c in ("自然风光", "海岛风光", "海滨度假") for c in cats):
        return "户外为主，请关注天气并做好防晒/防雨准备"
    if any(c in ("博物馆", "历史古迹", "宗教人文") for c in cats):
        return "人文为主，建议提前预约并预留排队时间"
    return "市区活动，交通便利"
