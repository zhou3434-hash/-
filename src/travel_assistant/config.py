"""全局配置：从 .env 读取，集中管理。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（本文件位于 src/travel_assistant/config.py）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置。字段名对应 .env 中的变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- DeepSeek ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # 关键：deepseek-flash 会先进行思维链推理（计入 reasoning_tokens），
    # 若 max_tokens 过小，token 会被思维链吃光，正文变成空字符串。
    # 因此默认给足预算，并在客户端做「空回复自动扩容重试」。
    deepseek_max_tokens: int = 4000
    deepseek_temperature: float = 1.0

    # ---- 记忆 ----
    memory_short_term_turns: int = 20
    memory_db_path: str = "data/memory.sqlite3"

    # ---- 视觉 ----
    vision_max_image_mb: int = 10
    vision_detail: str = "low"

    # ---- 缓存 ----
    cache_dir: str = "data/cache"

    # ---- 运行时 ----
    request_timeout: float = 180.0
    max_tool_rounds: int = 8

    @property
    def memory_db_file(self) -> Path:
        p = Path(self.memory_db_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def cache_path(self) -> Path:
        p = Path(self.cache_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def ensure_dirs(self) -> None:
        self.memory_db_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)

    def validate_ready(self) -> None:
        """启动前校验必需配置。"""
        if not self.deepseek_api_key or not self.deepseek_api_key.startswith("sk-"):
            raise RuntimeError(
                "未配置 DEEPSEEK_API_KEY。请在项目根目录的 .env 中填写：\n"
                "  DEEPSEEK_API_KEY=sk-xxxxxxxx\n"
                "（可从 .env.example 复制一份开始）"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
