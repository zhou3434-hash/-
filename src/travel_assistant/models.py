"""数据模型定义。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    """一条对话消息（内部统一表示）。"""

    role: Role
    content: str = ""
    #: 助手消息中的工具调用请求（OpenAI 兼容格式）
    tool_calls: list[dict[str, Any]] | None = None
    #: 工具消息对应的调用 id
    tool_call_id: str | None = None
    name: str | None = None
    #: 该消息携带的图片（base64 data URL），仅用于当轮，不入库
    images: list[str] | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    def to_api(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的消息字典。"""
        if self.role is Role.ASSISTANT and self.tool_calls:
            return {"role": "assistant", "content": self.content or None, "tool_calls": self.tool_calls}
        if self.role is Role.TOOL:
            return {"role": "tool", "content": self.content, "tool_call_id": self.tool_call_id}
        return {"role": self.role.value, "content": self.content}


class ToolResult(BaseModel):
    """工具执行结果。"""

    ok: bool = True
    data: Any = None
    error: str | None = None
    #: 给模型看的文本摘要
    summary: str = ""

    @classmethod
    def success(cls, data: Any, summary: str = "") -> "ToolResult":
        return cls(ok=True, data=data, summary=summary or str(data)[:2000])

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error, summary=f"工具执行失败：{error}")


class ToolSpec(BaseModel):
    """工具声明（用于生成 function calling 的 JSON Schema）。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Attraction(BaseModel):
    """景点。"""

    id: str
    name: str
    city: str
    country: str = "中国"
    category: str = "景点"
    description: str = ""
    ticket_cny: float = 0.0
    suggested_hours: float = 2.0
    rating: float = 4.5
    best_season: str = "四季皆宜"
    tags: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    tips: list[str] = Field(default_factory=list)

    def brief(self) -> str:
        price = "免费" if self.ticket_cny <= 0 else f"¥{self.ticket_cny:g}"
        return (
            f"{self.name}（{self.city}·{self.category}｜评分{self.rating}｜门票{price}｜"
            f"建议{self.suggested_hours:g}小时｜最佳{self.best_season}）：{self.description}"
        )


class BudgetBreakdown(BaseModel):
    """费用预算明细。"""

    city: str
    days: int
    people: int
    tier: str
    transport: float = 0.0
    hotel: float = 0.0
    food: float = 0.0
    tickets: float = 0.0
    local_transit: float = 0.0
    shopping: float = 0.0
    misc: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.transport + self.hotel + self.food + self.tickets
            + self.local_transit + self.shopping + self.misc,
            2,
        )

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["total"] = self.total
        d["per_person"] = round(self.total / max(self.people, 1), 2)
        d["per_person_per_day"] = round(self.total / max(self.people, 1) / max(self.days, 1), 2)
        return d


class VisionResult(BaseModel):
    """图片识别结果。"""

    is_travel_image: bool = True
    scene_type: str = "景点"
    landmarks: list[str] = Field(default_factory=list)
    city: str | None = None
    country: str | None = None
    region: str | None = None
    description: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    raw: str = ""


class TripPlan(BaseModel):
    """行程方案。"""

    city: str
    days: int
    pace: str = "标准"
    travelers: int = 1
    theme: str = "综合"
    daily: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
