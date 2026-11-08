from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_bangumi.src.render import SubjectRenderer
from astrbot_plugin_bangumi.tests.test_subject_renderer import build_subject_data


def test_subject_carrier_css_matches_playwright_scale() -> None:
    renderer = SubjectRenderer(render_mode="playwright")

    html = renderer._generate_html(
        "subject/subject_carrier.html",
        {
            "title": "测试条目",
            "subject_variant": "cinematic_poster",
            "pillow_card_data_uri": "data:image/png;base64,pillow-b64",
        },
    )

    assert "width: 800px" in html
    assert "width: 2400px" not in html
    assert 'id="subject-card"' in html


@pytest.mark.asyncio
async def test_subject_non_pillow_embeds_pillow_variant_carrier() -> None:
    renderer = SubjectRenderer(render_mode="rpc")
    renderer._render_subject_card_pillow_with_placeholder = AsyncMock(
        return_value="pillow-b64"
    )
    renderer.render = AsyncMock(return_value="rpc-b64")

    payload = await renderer.render_subject_card(
        build_subject_data(),
        rpc_url="http://127.0.0.1:3000",
        variant="pastel_lightbox",
    )

    assert payload == "rpc-b64"
    renderer._render_subject_card_pillow_with_placeholder.assert_awaited_once()
    assert (
        renderer._render_subject_card_pillow_with_placeholder.await_args.args[1]
        == "pastel_lightbox"
    )
    renderer.render.assert_awaited_once()
    call = renderer.render.await_args
    assert call is not None
    assert call.kwargs["template_path"] == "subject/subject_carrier.html"
    assert call.kwargs["selector"] == "#subject-card"
    assert call.kwargs["rpc_url"] == "http://127.0.0.1:3000"
    assert call.kwargs["render_data"]["subject_variant"] == "pastel_lightbox"
    assert call.kwargs["render_data"]["pillow_card_data_uri"].endswith("pillow-b64")


@pytest.mark.asyncio
async def test_subject_batch_forwards_variant_to_each_card() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    renderer.render_subject_card = AsyncMock(side_effect=["one", "two"])

    payloads = await renderer.render_batch_subject_cards_to_base64(
        [build_subject_data(), build_subject_data()],
        variant="editorial_digest",
    )

    assert payloads == ["one", "two"]
    assert renderer.render_subject_card.await_count == 2
    for call in renderer.render_subject_card.await_args_list:
        assert call.kwargs["variant"] == "editorial_digest"
