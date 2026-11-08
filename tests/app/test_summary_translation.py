import pytest
from astrbot_plugin_bangumi.src.app.summary_translation import (
    summary_needs_chinese_translation,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这是已经翻译好的中文简介。", False),
        ("社畜街道をひた走る佐々木さんの物語。", True),
        ("这是中文开头,但佐々木さんと田山が話す。", True),
        ("", False),
    ],
)
def test_summary_needs_chinese_translation_detects_japanese_or_mixed(
    text: str, expected: bool
) -> None:
    assert summary_needs_chinese_translation(text) is expected
