"""天气查询工具（Open-Meteo 免费 API，无需密钥）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..config import get_settings
from ..models import ToolResult, ToolSpec
from .base import BaseTool
from .cities import CITIES
from .common import normalize_city

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

#: WMO 天气代码 -> 中文描述
WMO: dict[int, str] = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}

#: 出行建议触发条件
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}


class WeatherTool(BaseTool):
    """查询目的地天气与出行建议。"""

    def __init__(self) -> None:
        # 地名 → 坐标缓存。实测一次对话里模型可能对同一地点重复调用本工具，
        # 而没有内置坐标时每次都要走一次地理编码；缓存可避免重复外呼。
        self._geo_cache: dict[str, dict[str, Any] | None] = {}

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_weather",
            description=(
                "查询目的地未来若干天的天气预报（含气温、降水概率、天气状况），"
                "并给出对行程的影响建议。规划行程与提醒出行准备时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"},
                    "days": {"type": "integer", "description": "预报天数 1-16，默认 5"},
                    "lat": {"type": "number", "description": "纬度（有坐标时优先，如图片识别出的地点）"},
                    "lon": {"type": "number", "description": "经度"},
                },
                "required": [],
            },
        )

    async def run(
        self,
        city: str | None = None,
        days: int = 5,
        lat: float | None = None,
        lon: float | None = None,
        **_: Any,
    ) -> ToolResult:
        name = normalize_city(city) if city else None
        if (lat is None or lon is None) and name in CITIES:
            meta = CITIES[name]
            lat, lon = float(meta["lat"]), float(meta["lon"])
        # 内置库没有该城市时，用在线地理编码兜底，避免直接失败
        if (lat is None or lon is None) and name:
            if name in self._geo_cache:
                hit = self._geo_cache[name]
            else:
                try:
                    hit = await asyncio.to_thread(self._geocode, name)
                except Exception:  # noqa: BLE001
                    hit = None
                self._geo_cache[name] = hit
            if hit:
                lat, lon = hit["lat"], hit["lon"]
                name = hit.get("name") or name
        if lat is None or lon is None:
            return ToolResult.failure(
                f"无法定位「{city or '该地点'}」的坐标。"
                f"若它是内置收录城市（{'、'.join(sorted(CITIES))}）请直接用城市名；"
                "否则请先调用 `lookup_coordinates` 取得经纬度，再用 lat/lon 调用本工具。"
            )

        days = max(1, min(int(days or 5), 16))
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": days,
        }
        try:
            data = await asyncio.to_thread(self._fetch, params)
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"天气服务请求失败：{type(e).__name__}: {e}")

        cur = data.get("current") or {}
        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        rows: list[dict[str, Any]] = []
        for i, d in enumerate(dates):
            code = (daily.get("weather_code") or [None])[i]
            rows.append({
                "date": d,
                "weather": WMO.get(code, f"代码{code}"),
                "code": code,
                "temp_max": (daily.get("temperature_2m_max") or [None])[i],
                "temp_min": (daily.get("temperature_2m_min") or [None])[i],
                "precip_prob": (daily.get("precipitation_probability_max") or [None])[i],
                "precip_mm": (daily.get("precipitation_sum") or [None])[i],
                "wind_max": (daily.get("wind_speed_10m_max") or [None])[i],
            })

        label = name or f"({lat:.3f},{lon:.3f})"
        advise = _advise(rows)
        cur_code = cur.get("weather_code")
        summary_lines = [
            f"【{label}】天气（数据源 Open-Meteo，时区 {data.get('timezone','auto')}）",
            f"  当前：{WMO.get(cur_code, cur_code)}　{cur.get('temperature_2m')}°C　"
            f"湿度 {cur.get('relative_humidity_2m')}%　风速 {cur.get('wind_speed_10m')} km/h",
        ]
        for r in rows:
            summary_lines.append(
                f"  {r['date']}：{r['weather']}　{r['temp_min']}~{r['temp_max']}°C　"
                f"降水概率 {r['precip_prob']}%　降水 {r['precip_mm']}mm"
            )
        if advise:
            summary_lines.append("出行建议：")
            summary_lines.extend(f"  - {a}" for a in advise)

        return ToolResult.success(
            {"city": name, "lat": lat, "lon": lon, "current": cur, "daily": rows, "advice": advise},
            summary="\n".join(summary_lines),
        )

    @staticmethod
    def _fetch(params: dict[str, Any]) -> dict[str, Any]:
        # 沙箱环境下 httpx 走系统证书链正常；设置 UA 更礼貌
        with httpx.Client(timeout=20.0, headers={"User-Agent": "travel-assistant/0.1"}) as c:
            r = c.get(FORECAST_URL, params=params)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _geocode(name: str) -> dict[str, Any] | None:
        """在线地理编码：把城市名转为坐标（用于内置库未收录的地点）。"""
        with httpx.Client(timeout=20.0, headers={"User-Agent": "travel-assistant/0.1"}) as c:
            r = c.get(GEOCODE_URL, params={"name": name, "count": 1, "language": "zh"})
            r.raise_for_status()
            d = r.json()
        results = d.get("results") or []
        if not results:
            return None
        top = results[0]
        admin = top.get("admin1")
        label = top.get("name") or name
        if admin and admin != label:
            label = f"{label}（{admin}）"
        return {"name": label, "lat": top.get("latitude"), "lon": top.get("longitude")}


def _advise(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    out: list[str] = []
    rainy = [r for r in rows if (r.get("code") in RAIN_CODES) or ((r.get("precip_prob") or 0) >= 60)]
    snowy = [r for r in rows if r.get("code") in SNOW_CODES]
    hot = [r for r in rows if (r.get("temp_max") or 0) >= 33]
    cold = [r for r in rows if (r.get("temp_min") or 99) <= 0]
    windy = [r for r in rows if (r.get("wind_max") or 0) >= 39]

    if rainy:
        out.append(f"{'、'.join(r['date'] for r in rainy)} 有降水，建议把户外/登山行程换成博物馆、古建等室内项目，并带雨具")
    if snowy:
        out.append("有降雪，注意路面湿滑与保暖，山区行程需确认道路通行")
    if hot:
        out.append("气温偏高，正午减少户外暴晒，随身补水，户外行程尽量安排在上午与傍晚")
    if cold:
        out.append("最低温接近或低于 0°C，需备厚外套与保暖内搭")
    if windy:
        out.append("风力较大，索道、游船、海岛项目可能临时停运，建议预留调整空间")
    if not out:
        out.append("天气整体适宜出行，按计划安排即可")
    return out


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
