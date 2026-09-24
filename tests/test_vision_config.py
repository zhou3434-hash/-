"""视觉模块与配置的单元测试（不调用模型 API）。"""

from __future__ import annotations

import base64

import pytest

from travel_assistant.config import Settings
from travel_assistant.models import VisionResult
from travel_assistant.vision import VisionAnalyzer, _conf, _loads_lenient, _opt_str


# ---------------------------------------------------------------------- #
# 图片编码
# ---------------------------------------------------------------------- #
def _png_bytes() -> bytes:
    # 1x1 透明 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+3d"
        "0AAAAASUVORK5CYII="
    )


def test_encode_bytes_produces_data_url():
    url = VisionAnalyzer.encode_bytes(_png_bytes(), "image/png")
    assert url.startswith("data:image/png;base64,")
    assert len(url) > 40


def test_encode_bytes_rejects_unsupported_mime():
    """不支持的 MIME 应回退到 jpeg，而不是产出非法 data URL。"""
    url = VisionAnalyzer.encode_bytes(b"abc", "application/pdf")
    assert url.startswith("data:image/jpeg;base64,")


def test_encode_file_missing(tmp_path):
    a = VisionAnalyzer.__new__(VisionAnalyzer)  # 不触发网络/客户端初始化
    with pytest.raises(FileNotFoundError):
        a.encode_file(tmp_path / "nope.jpg")


def test_encode_file_size_limit(tmp_path):
    s = Settings(deepseek_api_key="sk-test", vision_max_image_mb=0)
    a = VisionAnalyzer.__new__(VisionAnalyzer)
    a.settings = s
    p = tmp_path / "big.jpg"
    p.write_bytes(b"x" * 2048)
    with pytest.raises(ValueError, match="超过限制"):
        a.encode_file(p)


def test_encode_file_ok(tmp_path):
    s = Settings(deepseek_api_key="sk-test", vision_max_image_mb=10)
    a = VisionAnalyzer.__new__(VisionAnalyzer)
    a.settings = s
    p = tmp_path / "ok.png"
    p.write_bytes(_png_bytes())
    url = a.encode_file(p)
    assert url.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------- #
# 容错 JSON 解析
# ---------------------------------------------------------------------- #
def test_loads_plain_json():
    assert _loads_lenient('{"city": "巴黎"}')["city"] == "巴黎"


def test_loads_markdown_fenced_json():
    raw = '```json\n{"city": "东京", "confidence": "high"}\n```'
    d = _loads_lenient(raw)
    assert d["city"] == "东京"
    assert d["confidence"] == "high"


def test_loads_json_with_prose_around():
    raw = '好的，结果如下：\n{"city": "杭州"}\n以上就是分析。'
    assert _loads_lenient(raw)["city"] == "杭州"


def test_loads_garbage_returns_empty():
    assert _loads_lenient("完全不是 JSON") == {}
    assert _loads_lenient("") == {}


def test_loads_non_object_returns_empty():
    assert _loads_lenient("[1,2,3]") == {}


# ---------------------------------------------------------------------- #
# 字段清洗
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("v,expected", [
    ("巴黎", "巴黎"), (" 东京 ", "东京"),
    (None, None), ("", None), ("null", None), ("None", None),
    ("未知", None), ("不确定", None), ("unknown", None),
])
def test_opt_str(v, expected):
    assert _opt_str(v) == expected


@pytest.mark.parametrize("v,expected", [
    ("high", "high"), ("LOW", "low"), ("Medium", "medium"),
    ("乱写", "medium"), (None, "medium"),
])
def test_conf(v, expected):
    assert _conf(v) == expected


# ---------------------------------------------------------------------- #
# 渲染
# ---------------------------------------------------------------------- #
def test_render_travel_image():
    r = VisionResult(
        is_travel_image=True, scene_type="城市地标",
        landmarks=["埃菲尔铁塔"], city="巴黎", country="法国",
        description="铁塔与蓝天", confidence="high",
    )
    out = VisionAnalyzer.render(r, (48.8584, 2.2945))
    assert "埃菲尔铁塔" in out
    assert "法国" in out
    assert "48.8584" in out


def test_render_non_travel_image():
    r = VisionResult(is_travel_image=False, description="看起来是一张收据")
    out = VisionAnalyzer.render(r)
    assert "未能" in out
    assert "收据" in out


def test_render_warns_on_low_confidence():
    r = VisionResult(is_travel_image=True, landmarks=["未知建筑"], confidence="low")
    out = VisionAnalyzer.render(r)
    assert "不确定" in out


# ---------------------------------------------------------------------- #
# 配置校验
# ---------------------------------------------------------------------- #
def test_settings_validate_ready_ok():
    Settings(deepseek_api_key="sk-abc").validate_ready()


@pytest.mark.parametrize("key", ["", "abc", "your-key"])
def test_settings_validate_ready_rejects_bad_key(key):
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        Settings(deepseek_api_key=key).validate_ready()


def test_settings_paths_are_absolute():
    s = Settings(deepseek_api_key="sk-test")
    assert s.memory_db_file.is_absolute()
    assert s.cache_path.is_absolute()


def test_settings_defaults_sane():
    s = Settings(deepseek_api_key="sk-test")
    assert s.deepseek_model == "deepseek-flash"
    # 关键：默认 token 预算必须足够大，否则思维链会把正文吃光
    assert s.deepseek_max_tokens >= 3000
    assert s.max_tool_rounds >= 4
