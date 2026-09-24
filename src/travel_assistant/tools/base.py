"""工具基类。"""

from __future__ import annotations

import abc
import logging
from typing import Any

from ..models import ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class BaseTool(abc.ABC):
    """所有工具的基类。

    子类需实现 :meth:`spec` 与 :meth:`run`。
    """

    @abc.abstractmethod
    def spec(self) -> ToolSpec:
        """返回工具声明（名称、说明、参数 JSON Schema）。"""

    @abc.abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """执行工具。"""

    @property
    def name(self) -> str:
        return self.spec().name

    async def safe_run(self, args: dict[str, Any]) -> ToolResult:
        """带异常兜底的执行入口。

        工具抛异常不应中断整个智能体循环 —— 把错误作为结果交还给模型，
        让模型自行决定改写参数重试或换一条路。
        """
        try:
            return await self.run(**args)
        except TypeError as e:
            logger.warning("工具 %s 参数不匹配: %s", self.name, e)
            return ToolResult.failure(f"参数错误：{e}")
        except Exception as e:  # noqa: BLE001 - 需要兜住一切，保证循环不崩
            logger.exception("工具 %s 执行异常", self.name)
            return ToolResult.failure(f"{type(e).__name__}: {e}")
