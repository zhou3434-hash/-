"""费用预算工具。"""

from __future__ import annotations

from typing import Any

from ..models import BudgetBreakdown, ToolResult, ToolSpec
from .base import BaseTool
from .cities import CITIES, DEFAULT_ORIGIN, TIER_FACTORS
from .common import estimate_intercity, normalize_city

#: 门票日均基准（按人/天，用于没有逐项行程时的粗估）
TICKET_PER_DAY = 90.0

#: 购物与其他的人均占比提示
SHOPPING_RATIO = {"经济": 0.05, "舒适": 0.08, "高端": 0.15}


class EstimateBudgetTool(BaseTool):
    """旅行费用预算。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="estimate_budget",
            description=(
                "估算旅行总费用，细分交通、住宿、餐饮、门票、市内交通、购物。"
                "可指定人数、天数、消费档位与出发城市。回答「要花多少钱」「预算规划」时必须调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "目的地城市"},
                    "days": {"type": "integer", "description": "行程天数"},
                    "people": {"type": "integer", "description": "人数，默认 1"},
                    "tier": {
                        "type": "string",
                        "enum": ["经济", "舒适", "高端"],
                        "description": "消费档位，默认舒适",
                    },
                    "origin": {
                        "type": "string",
                        "description": "出发城市，用于估算往返大交通；缺省时按用户常住城市或北京处理",
                    },
                    "include_intercity": {
                        "type": "boolean",
                        "description": "是否计入城际往返交通，默认 true",
                    },
                    "ticket_total": {
                        "type": "number",
                        "description": "已知门票合计（来自行程规划），可选，填入后更精确",
                    },
                },
                "required": ["city", "days"],
            },
        )

    async def run(
        self,
        city: str,
        days: int,
        people: int = 1,
        tier: str = "舒适",
        origin: str | None = None,
        include_intercity: bool = True,
        ticket_total: float | None = None,
        **_: Any,
    ) -> ToolResult:
        norm = normalize_city(city)
        if norm not in CITIES:
            return ToolResult.failure(
                f"内置费用库暂无「{city}」数据。当前收录：{'、'.join(sorted(CITIES))}。"
                "可按同类城市口径给出粗略估算并说明依据。"
            )
        meta = CITIES[norm]
        days = max(1, min(int(days or 1), 60))
        people = max(1, int(people or 1))
        tier = tier if tier in TIER_FACTORS else "舒适"
        f = TIER_FACTORS[tier]

        hotel = meta["hotel"] * f["hotel"] * days * max(1, (people + 1) // 2)  # 按 2 人 1 间
        food = meta["food"] * f["food"] * days * people
        local_transit = meta["local_transit"] * f["local_transit"] * days * people
        misc = meta["misc"] * f["misc"] * days * people

        if ticket_total is not None and ticket_total > 0:
            tickets = float(ticket_total)
            ticket_source = "由行程规划结果带入"
        else:
            tickets = TICKET_PER_DAY * meta["ticket_factor"] * days * people
            ticket_source = f"按日均 ¥{TICKET_PER_DAY:g} × 城市系数 {meta['ticket_factor']} 粗估"

        transport = 0.0
        route_note = "未计入城际交通"
        if include_intercity:
            org = normalize_city(origin) if origin else DEFAULT_ORIGIN
            if org == norm:
                route_note = "出发地与目的地相同，未计入城际交通"
            else:
                est = estimate_intercity(org or DEFAULT_ORIGIN, norm)
                if est.get("ok"):
                    one = est["cost_air_cny"] if est["suggest_mode"] == "飞机" else est["cost_rail_cny"]
                    transport = one * 2 * people
                    route_note = (
                        f"往返 {est['origin']}→{est['destination']} 约 {est['distance_km']:g} 公里，"
                        f"建议{est['suggest_mode']}，单人单程 ¥{one:g}"
                    )
                else:
                    route_note = est.get("reason", "城际交通估算失败")

        shopping = (hotel + food + transit_total(local_transit, transport) + misc) * SHOPPING_RATIO[tier]

        bd = BudgetBreakdown(
            city=norm, days=days, people=people, tier=tier,
            transport=round(transport, 0), hotel=round(hotel, 0), food=round(food, 0),
            tickets=round(tickets, 0), local_transit=round(local_transit, 0),
            shopping=round(shopping, 0), misc=round(misc, 0),
        )
        payload = bd.to_dict()
        payload["route_note"] = route_note
        payload["ticket_source"] = ticket_source
        payload["tier_factors"] = f

        s = payload
        summary = "\n".join([
            f"【{norm}】{days} 天 · {people} 人 · {tier}档 费用估算",
            f"  城际往返交通：¥{s['transport']:g}　（{route_note}）",
            f"  住宿：¥{s['hotel']:g}（{days}晚，2人1间口径）",
            f"  餐饮：¥{s['food']:g}",
            f"  门票：¥{s['tickets']:g}（{ticket_source}）",
            f"  市内交通：¥{s['local_transit']:g}",
            f"  购物及其他：¥{s['shopping'] + s['misc']:g}",
            f"  ────────────────",
            f"  总计：¥{s['total']:g}　（人均 ¥{s['per_person']:g}，人均日均 ¥{s['per_person_per_day']:g}）",
            "  提示：以上为规划参考值，旺季机票与酒店可能上浮 30%-80%。",
        ])
        return ToolResult.success(payload, summary=summary)


def transit_total(local: float, intercity: float) -> float:
    return local + intercity
