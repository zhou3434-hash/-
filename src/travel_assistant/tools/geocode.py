"""坐标反查工具（模型知识兜底）。

背景
----
本机实测：

* Open-Meteo 的地理编码**不支持中文查询**（`九寨沟` 返回 0 结果，`Huangshan` 正常）；
* OSM Nominatim 在本机**完全无法连接**（连接超时，国内网络常见）。

因此对于既不在内置城市库、又只有中文名的地点，用模型自身的地理知识反查坐标，
再交给 ``get_weather`` / ``nearby_attractions`` 使用。

**可靠性说明**：这是模型知识推断，不是权威地理编码，存在出错可能。
返回值中用 ``source`` 标明来源，并要求智能体在回答里说明不确定性。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import DeepSeekClient
from ..models import ToolResult, ToolSpec
from .base import BaseTool
from .cities import CITIES
from .common import haversine_km, normalize_city

logger = logging.getLogger(__name__)

PROMPT = """请给出下面地点的经纬度坐标。

地点：{name}

要求：
- 定位到该地点本身（景区/城市中心均可），不要给到省会或邻市；
- 若你不确定该地点在哪里，把 known 设为 false，不要猜；
- 只输出 JSON，不要解释。

JSON 结构：
{{"known": true, "lat": 33.2600, "lon": 103.9200, "city": "九寨沟", "province": "四川", "country": "中国", "confidence": "high", "note": "简短说明"}}"""


class LookupCoordinatesTool(BaseTool):
    """用模型知识把地名转成坐标。"""

    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self._client = client

    def set_client(self, client: DeepSeekClient) -> None:
        self._client = client

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lookup_coordinates",
            description=(
                "把地名/景点名转换为经纬度坐标。用于 get_weather 或 nearby_attractions 报错说"
                "「无法定位」时的补充手段，例如中文小地名（九寨沟、稻城亚丁、婺源）。"
                "注意：本工具结果是依据模型知识推断，不是权威地理编码，"
                "回答用户时需说明坐标是估算值。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "地名或景点名，例如「九寨沟」"},
                },
                "required": ["name"],
            },
        )

    async def run(self, name: str, **_: Any) -> ToolResult:
        if not name or not str(name).strip():
            return ToolResult.failure("需要提供地名")
        raw_name = str(name).strip()

        # 先看内置库
        norm = normalize_city(raw_name)
        if norm in CITIES:
            meta = CITIES[norm]
            return ToolResult.success(
                {
                    "known": True, "name": norm, "lat": meta["lat"], "lon": meta["lon"],
                    "source": "内置城市库", "confidence": "high",
                },
                summary=f"「{norm}」在内置城市库中，坐标 {meta['lat']}, {meta['lon']}（来源：内置数据，可靠）",
            )

        if self._client is None:
            return ToolResult.failure("坐标反查需要模型客户端，但当前未注入")

        try:
            text = await self._client.complete(
                PROMPT.format(name=raw_name), json_mode=True, max_tokens=4000, temperature=1.0
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"坐标推断调用失败：{type(e).__name__}: {e}")

        data = _loads(text)
        if not data or not data.get("known"):
            return ToolResult.failure(
                f"无法确定「{raw_name}」的位置。请让用户补充所在省份或城市，"
                "或改用已知的城市（如最近的大城市）查询天气。"
            )

        lat, lon = data.get("lat"), data.get("lon")
        if not _valid(lat, lon):
            return ToolResult.failure(f"「{raw_name}」返回的坐标不合法：lat={lat}, lon={lon}")

        city = data.get("city") or raw_name
        nearest = _nearest(float(lat), float(lon))
        conf = str(data.get("confidence") or "medium").lower()
        lines = [
            f"「{raw_name}」坐标（模型知识推断）：{float(lat):.4f}, {float(lon):.4f}",
            f"位于：{data.get('country') or '未知'}"
            + (f" · {data.get('province')}" if data.get("province") else "")
            + (f" · {city}" if city else ""),
            f"置信度：{conf}　来源：模型知识（非权威地理编码，可能偏差）",
        ]
        if nearest:
            lines.append(
                f"参考：距内置城市【{nearest['city']}】直线约 {nearest['distance_km']:g} 公里"
            )
        if data.get("note"):
            lines.append(f"说明：{data['note']}")
        lines.append("→ 可直接把该 lat/lon 传给 get_weather 或 nearby_attractions")
        lines.append("→ 回答用户时必须说明坐标是估算值")

        return ToolResult.success(
            {
                "known": True,
                "name": raw_name,
                "lat": float(lat),
                "lon": float(lon),
                "city": city,
                "province": data.get("province"),
                "country": data.get("country"),
                "confidence": conf,
                "source": "模型知识推断",
                "nearest_builtin_city": nearest["city"] if nearest else None,
                "nearest_distance_km": nearest["distance_km"] if nearest else None,
            },
            summary="\n".join(lines),
        )


def _valid(lat: Any, lon: Any) -> bool:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= la <= 90 and -180 <= lo <= 180 and not (la == 0 and lo == 0)


def _nearest(lat: float, lon: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for city, meta in CITIES.items():
        d = haversine_km(lat, lon, float(meta["lat"]), float(meta["lon"]))
        if best is None or d < best["distance_km"]:
            best = {"city": city, "distance_km": d}
    return best


def _loads(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(t[s:e + 1])
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}
