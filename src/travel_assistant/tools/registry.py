"""工具注册表。"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..models import ToolResult, ToolSpec
from .attractions import AttractionDetailTool, NearbyAttractionsTool, SearchAttractionsTool
from .base import BaseTool
from .budget import EstimateBudgetTool
from .geocode import LookupCoordinatesTool
from .geo import LocateAreaTool
from .planner import PlanItineraryTool
from .weather import WeatherTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """管理所有可用工具，并生成 function calling 声明。"""

    def __init__(self, tools: Iterable[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.debug("注册工具：%s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def api_tools(self) -> list[dict[str, Any]]:
        """生成 OpenAI 兼容的工具声明列表。"""
        return [s.to_api() for s in self.specs()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult.failure(f"未注册的工具：{name}。可用工具：{', '.join(self.names())}")
        return await tool.safe_run(args or {})


def build_default_registry(client: object | None = None) -> ToolRegistry:
    """构建默认工具集。

    :param client: 可选的 DeepSeek 客户端，注入给需要模型知识的工具
        （``lookup_coordinates``）。
    """
    geo_tool = LookupCoordinatesTool()
    if client is not None:
        geo_tool.set_client(client)  # type: ignore[arg-type]
    return ToolRegistry([
        SearchAttractionsTool(),
        AttractionDetailTool(),
        NearbyAttractionsTool(),
        PlanItineraryTool(),
        EstimateBudgetTool(),
        WeatherTool(),
        LocateAreaTool(),
        geo_tool,
    ])
