"""错题本图片资源规范化回归测试。"""

from app.services.error_record_assets import has_usable_figure, normalize_error_assets


def test_normalize_error_assets_converts_legacy_frame_to_image():
    assert normalize_error_assets(
        [{"frames": [{"data_uri": "data:image/svg+xml,abc"}]}]
    ) == [{"type": "image", "src": "data:image/svg+xml,abc", "alt": "题目配图"}]


def test_normalize_error_assets_keeps_valid_ggb_and_discards_unknown_values():
    assert normalize_error_assets(
        [
            {"type": "ggb", "view": "3d", "commands": ["A=(0,0,0)"]},
            {"unexpected": True},
        ]
    ) == [
        {"type": "ggb", "view": "3d", "commands": ["A=(0,0,0)"], "caption": ""}
    ]


def test_figure_requirement_uses_assets_or_explicit_figure_language():
    assert has_usable_figure("求函数的最值", []) is False
    assert has_usable_figure("如图，证明直线与平面垂直", []) is True
    assert has_usable_figure("普通题", [{"type": "image", "src": "https://example.test/a.png", "alt": "题目配图"}]) is True
