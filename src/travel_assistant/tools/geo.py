"""地理位置工具：坐标 → 地区/城市（Open-Meteo Geocoding，免费无密钥）。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..models import ToolResult, ToolSpec
from .base import BaseTool
from .cities import CITIES
from .common import haversine_km, normalize_city

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


class LocateAreaTool(BaseTool):
    """由经纬度反查所在城市/国家。"""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="locate_area",
            description=(
                "根据经纬度查询所在地的城市、省份与国家名称，并指出附近是否有内置景点数据。"
                "图片识别出拍摄坐标后，用本工具确认具体地区。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "纬度"},
                    "lon": {"type": "number", "description": "经度"},
                },
                "required": ["lat", "lon"],
            },
        )

    async def run(self, lat: float, lon: float, **_: Any) -> ToolResult:
        try:
            data = await asyncio.to_thread(self._fetch, float(lat), float(lon))
        except Exception as e:  # noqa: BLE001
            data = None
            err = f"{type(e).__name__}: {e}"

        results = (data or {}).get("results") or []
        if not results:
            # 退回内置城市就近匹配
            near = _nearest_builtin(float(lat), float(lon))
            if near:
                return ToolResult.success(
                    {
                        "resolved": False,
                        "nearest_builtin_city": near["city"],
                        "distance_km": near["distance_km"],
                        "note": "在线反查无结果，已按内置坐标就近匹配",
                    },
                    summary=(
                        f"在线反查无结果；按坐标就近匹配到内置城市【{near['city']}】"
                        f"（直线约 {near['distance_km']:g} 公里）。"
                    ),
                )
            detail = f"（{err}）" if data is None else ""
            return ToolResult.failure(f"无法解析坐标 ({lat}, {lon}) 所在地区{detail}")

        top = results[0]
        city = top.get("name")
        admin1 = top.get("admin1")
        country = top.get("country")
        norm = normalize_city(city)

        near = _nearest_builtin(float(lat), float(lon))
        has_builtin = norm in CITIES
        summary_lines = [
            f"坐标 ({lat:.4f}, {lon:.4f}) 位于：{country or '未知国家'}"
            + (f" · {admin1}" if admin1 else "")
            + (f" · {city}" if city else ""),
            f"人口：{top.get('population', '未知')}｜时区：{top.get('timezone', '未知')}",
        ]
        if has_builtin:
            summary_lines.append(f"内置数据库已收录【{norm}】，可直接查询景点与预算")
        elif near:
            summary_lines.append(
                f"内置数据库未单独收录该城市，最接近的是【{near['city']}】"
                f"（约 {near['distance_km']:g} 公里），可作为行程参考"
            )

        return ToolResult.success(
            {
                "resolved": True,
                "city": city,
                "normalized_city": norm,
                "admin1": admin1,
                "country": country,
                "timezone": top.get("timezone"),
                "population": top.get("population"),
                "lat": top.get("latitude"),
                "lon": top.get("longitude"),
                "in_builtin_db": has_builtin,
                "nearest_builtin_city": near["city"] if near else None,
                "nearest_distance_km": near["distance_km"] if near else None,
            },
            summary="\n".join(summary_lines),
        )

    @staticmethod
    def _fetch(lat: float, lon: float) -> dict[str, Any]:
        """Open-Meteo 的 geocoding 接口用 name 参数；这里用坐标做近似反查。

        该接口没有严格的反向地理编码，因此先用坐标做「最近城市」检索：
        通过 name 搜索常见城市名不可行，故改用 BigDataCloud 的免费反查接口，
        失败时回退到内置坐标就近匹配（见 run 中的 _nearest_builtin）。
        """
        url = "https://api.bigdatacloud.net/data/reverse-geocode-client"
        with httpx.Client(timeout=20.0, headers={"User-Agent": "travel-assistant/0.1"}) as c:
            r = c.get(url, params={"latitude": lat, "longitude": lon, "localityLanguage": "zh"})
            r.raise_for_status()
            d = r.json()
        info = d.get("localityInfo") or {}
        informative = info.get("informative") or []
        admin = d.get("principalSubdivision")
        country = d.get("countryName")
        for item in informative:
            if item.get("order") == 4 and item.get("name"):
                admin = admin or item["name"]
            if item.get("order") == 2 and item.get("name"):
                country = country or item["name"]
        return {
            "results": [{
                "name": d.get("city") or d.get("locality") or admin,
                "admin1": admin,
                "country": country,
                "latitude": lat,
                "longitude": lon,
                "timezone": d.get("timeZone", {}).get("ianaId") if isinstance(d.get("timeZone"), dict) else d.get("timeZone"),
            }]
        }


def _nearest_builtin(lat: float, lon: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for city, meta in CITIES.items():
        d = haversine_km(lat, lon, float(meta["lat"]), float(meta["lon"]))
        if best is None or d < best["distance_km"]:
            best = {"city": city, "distance_km": d}
    return best
