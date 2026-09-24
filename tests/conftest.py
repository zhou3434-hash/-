"""pytest 全局配置。

环境适配说明
------------
本机沙箱不允许写入 `%TEMP%`（`C:\\Users\\...\\AppData\\Local\\Temp\\...`），
而 pytest 的 ``tmp_path`` / ``tmp_path_factory`` 默认就建在那里，
会导致所有使用临时目录的测试以 ``PermissionError`` 报错。

因此这里把临时根目录重定向到项目内的 ``.pytest-tmp``，
并把 pytest 自身的缓存插件关掉（``.pytest_cache`` 写入同样会被拒绝）。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

_TMP_ROOT = Path(__file__).resolve().parent / ".pytest-tmp"


@pytest.fixture(scope="session")
def tmp_path_factory():
    """覆盖 pytest 内置工厂，把临时目录放到工作区内。"""
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    class _Factory:
        def mktemp(self, basename: str = "tmp", numbered: bool = True) -> Path:
            name = f"{basename}-{uuid.uuid4().hex[:8]}" if numbered else basename
            p = _TMP_ROOT / name
            p.mkdir(parents=True, exist_ok=True)
            created.append(p)
            return p

        def getbasetemp(self) -> Path:
            return _TMP_ROOT

    yield _Factory()

    # 尽力清理（沙箱下可能失败，忽略即可）
    for p in created:
        shutil.rmtree(p, ignore_errors=True)


@pytest.fixture
def tmp_path(tmp_path_factory):
    """覆盖内置 tmp_path，返回工作区内的临时目录。"""
    return tmp_path_factory.mktemp("case")
