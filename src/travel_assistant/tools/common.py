"""通用工具函数。"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .cities import CITY_ALIASES, CITIES


def normalize_city(name: str | None) -> str | None:
    """把城市名归一化到内置城市表。

    支持：全称、别名（魔都/蓉城等）、英文小写、带「市」后缀、首尾空白。
    匹配不到时返回原字符串（去掉空白），以便模型自行处理未收录城市。
    """
    if not name:
        return None
    raw = str(name).strip()
    if not raw:
        return None
    # 全角转半角 + 去空白
    raw = unicodedata.normalize("NFKC", raw).strip()
    if raw in CITIES:
        return raw
    low = raw.lower()
    if low in CITY_ALIASES:
        return CITY_ALIASES[low]
    if raw in CITY_ALIASES:
        return CITY_ALIASES[raw]
    # 去掉「市/地区/自治区」等后缀再试
    stripped = re.sub(r"(市|地区|特别行政区|自治州|省)$", "", raw)
    if stripped in CITIES:
        return stripped
    if stripped in CITY_ALIASES:
        return CITY_ALIASES[stripped]
    # 包含关系（如「杭州市西湖区」）
    for c in CITIES:
        if c in raw:
            return c
    return stripped or raw


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（公里）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def city_coords(city: str) -> tuple[float, float] | None:
    c = CITIES.get(city)
    if not c:
        return None
    return float(c["lat"]), float(c["lon"])


def estimate_intercity(
    origin: str, destination: str, *, mode: str = "auto"
) -> dict[str, Any]:
    """估算城市间交通费用与建议方式（单人单程）。"""
    from .cities import INTERCITY

    o, d = city_coords(origin), city_coords(destination)
    if not o or not d:
        return {"ok": False, "reason": f"缺少「{origin}」或「{destination}」的坐标数据，无法估算"}

    km = haversine_km(o[0], o[1], d[0], d[1])
    air = max(INTERCITY["air_min"], km * INTERCITY["air_per_km"])
    rail = max(INTERCITY["rail_min"], km * INTERCITY["rail_per_km"])

    if mode == "air":
        suggest = "飞机"
    elif mode == "rail":
        suggest = "高铁"
    else:
        suggest = "高铁" if km <= INTERCITY["rail_pref_km"] else "飞机"

    return {
        "ok": True,
        "origin": origin,
        "destination": destination,
        "distance_km": km,
        "suggest_mode": suggest,
        "cost_air_cny": round(air, 0),
        "cost_rail_cny": round(rail, 0),
        "note": (
            "估算为单人单程参考价，未含机场/车站接驳与旺季浮动"
            + ("" if km <= INTERCITY["rail_max_km"] else "；距离较远，高铁耗时较长")
        ),
    }
