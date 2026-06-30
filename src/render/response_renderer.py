import asyncio
from typing import cast

import aiohttp
from PIL import Image, ImageDraw

from ..domain import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EpisodeCardVariant,
    RenderData,
    is_episode_card_variant,
)
from .base_renderer import BaseRenderer
from .pillow_utils import (
    FontType,
    create_linear_gradient,
    draw_pill,
    ellipsize_text,
    get_font,
    image_to_base64,
    line_height,
    measure_text,
    wrap_text,
)
from .render_mode import RenderMode

RESPONSE_TEXT_IMAGE_THRESHOLD = 30
_CARD_WIDTH = 1600
_CONTENT_LEFT = 150
_CONTENT_RIGHT = _CARD_WIDTH - 150
_MAX_RESPONSE_LINES = 30
_ICON_REPLACEMENTS = {
    "⚠️": "注意:",
    "⚠": "注意:",
    "❌": "错误:",
    "✅": "完成:",
    "🔍": "搜索:",
    "⏰": "超时:",
}


def should_render_text_as_image(text: str) -> bool:
    return bool(text) and ("\n" in text or len(text) > RESPONSE_TEXT_IMAGE_THRESHOLD)


def _normalize_variant(variant: EpisodeCardVariant | None) -> EpisodeCardVariant:
    if is_episode_card_variant(variant):
        return variant
    return DEFAULT_EPISODE_CARD_VARIANT


def _sanitize_text_for_render(text: str) -> str:
    rendered = text
    for source, replacement in _ICON_REPLACEMENTS.items():
        rendered = rendered.replace(source, replacement)
    return rendered


def _wrap_preserving_newlines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: FontType,
    max_width: int,
    max_lines: int,
) -> list[str]:
    lines: list[str] = []
    paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for paragraph_index, paragraph in enumerate(paragraphs):
        if len(lines) >= max_lines:
            break
        if not paragraph.strip():
            lines.append("")
        else:
            remaining = max_lines - len(lines)
            wrapped = wrap_text(draw, paragraph, font, max_width, remaining)
            lines.extend(wrapped)
        if paragraph_index < len(paragraphs) - 1 and len(lines) < max_lines:
            continue

    if len(lines) >= max_lines and len(paragraphs) > 1:
        lines[-1] = ellipsize_text(draw, lines[-1], font, max_width)
    return lines[:max_lines]


def _line_width(draw: ImageDraw.ImageDraw, line: str, font: FontType) -> int:
    if not line:
        return 0
    return measure_text(draw, line, font)[0]


def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    lines: list[str],
    font: FontType,
    fill: tuple[int, int, int, int],
    *,
    line_spacing: int,
) -> int:
    current_y = xy[1]
    text_line_height = max(50, line_height(draw, font))
    for line in lines:
        if line:
            draw.text((xy[0], current_y), line, font=font, fill=fill)
        current_y += text_line_height + line_spacing
    return current_y - xy[1] - line_spacing if lines else 0


def _draw_response_card_image(
    text: str,
    variant: EpisodeCardVariant,
    title: str,
) -> str:
    display_text = _sanitize_text_for_render(text)
    probe = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    probe_draw = ImageDraw.Draw(probe)
    label_font = get_font(42, bold=True)
    title_font = get_font(70, bold=True)
    body_font = get_font(50)
    footer_font = get_font(34, bold=True)
    body_lines = _wrap_preserving_newlines(
        probe_draw,
        display_text,
        body_font,
        _CONTENT_RIGHT - _CONTENT_LEFT,
        _MAX_RESPONSE_LINES,
    )
    body_line_height = max(50, line_height(probe_draw, body_font))
    body_height = max(120, len(body_lines) * (body_line_height + 18) - 18)
    card_height = max(760, 475 + body_height + 210)

    if variant == "pastel_lightbox":
        canvas = Image.new("RGBA", (_CARD_WIDTH, card_height), (255, 252, 244, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, _CARD_WIDTH, 238), fill=(198, 232, 246, 255))
        draw.rectangle((0, 238, 184, card_height), fill=(218, 244, 226, 255))
        draw.rounded_rectangle(
            (1180, 60, 1518, 206), radius=32, fill=(255, 202, 216, 255)
        )
        draw.text(
            (_CONTENT_LEFT, 76),
            "PASTEL LIGHTBOX",
            font=label_font,
            fill=(42, 55, 73, 255),
        )
        draw.rounded_rectangle(
            (118, 250, 1482, card_height - 90),
            radius=28,
            fill=(255, 255, 252, 245),
            outline=(232, 222, 212, 255),
            width=2,
        )
        body_box = (_CONTENT_LEFT, 505, _CONTENT_RIGHT, card_height - 180)
        draw_pill(
            draw,
            (_CONTENT_LEFT, 330),
            title,
            label_font,
            fill=(255, 185, 211, 255),
            text_fill=(255, 255, 252, 255),
            padding_x=34,
            padding_y=17,
        )
        draw.text(
            (_CONTENT_LEFT, 430),
            "soft response edition",
            font=footer_font,
            fill=(88, 101, 118, 255),
        )
        _draw_text_lines(
            draw,
            (body_box[0], body_box[1]),
            body_lines,
            body_font,
            (54, 65, 82, 255),
            line_spacing=18,
        )
        return image_to_base64(canvas)

    if variant == "editorial_digest":
        canvas = Image.new("RGBA", (_CARD_WIDTH, card_height), (238, 240, 232, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 32, card_height), fill=(111, 137, 129, 255))
        draw.text(
            (_CONTENT_LEFT, 110),
            "EPISODE DIGEST",
            font=label_font,
            fill=(105, 128, 120, 255),
        )
        draw.text(
            (_CONTENT_LEFT, 200),
            title,
            font=title_font,
            fill=(31, 36, 44, 255),
        )
        draw.line(
            (_CONTENT_LEFT, 310, _CONTENT_RIGHT, 310),
            fill=(92, 115, 112, 255),
            width=5,
        )
        body_y = 375
        _draw_text_lines(
            draw,
            (_CONTENT_LEFT, body_y),
            body_lines,
            body_font,
            (48, 55, 63, 255),
            line_spacing=20,
        )
        draw.text(
            (_CONTENT_LEFT, card_height - 95),
            "BANGUMI RESPONSE",
            font=footer_font,
            fill=(119, 126, 134, 255),
        )
        return image_to_base64(canvas)

    canvas = create_linear_gradient(
        (_CARD_WIDTH, card_height), (238, 247, 236), (247, 242, 232)
    )
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, _CARD_WIDTH, 170), fill=(199, 231, 240, 255))
    draw.rounded_rectangle(
        (94, 232, 1506, card_height - 92),
        radius=24,
        fill=(255, 255, 250, 232),
        outline=(220, 226, 218, 255),
        width=2,
    )
    draw.text(
        (_CONTENT_LEFT, 82),
        "CINEMATIC RESPONSE",
        font=label_font,
        fill=(42, 55, 73, 255),
    )
    draw.text(
        (_CONTENT_LEFT, 310),
        title,
        font=title_font,
        fill=(31, 36, 44, 255),
    )
    accent_width = max(240, _line_width(draw, title, title_font))
    draw.rectangle(
        (_CONTENT_LEFT, 400, min(_CONTENT_LEFT + accent_width, _CONTENT_RIGHT), 412),
        fill=(236, 72, 153, 255),
    )
    _draw_text_lines(
        draw,
        (_CONTENT_LEFT, 475),
        body_lines,
        body_font,
        (45, 53, 65, 255),
        line_spacing=19,
    )
    draw.text(
        (_CONTENT_LEFT, card_height - 170),
        "2026  |  24min  |  readable card",
        font=footer_font,
        fill=(97, 108, 119, 255),
    )
    return image_to_base64(canvas)


class ResponseRenderer(BaseRenderer):
    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        render_mode: RenderMode = "pillow",
        proxy_url: str | None = None,
    ) -> None:
        super().__init__(session=session, render_mode=render_mode, proxy_url=proxy_url)

    async def _render_response_pillow(
        self,
        text: str,
        variant: EpisodeCardVariant,
        title: str,
    ) -> str:
        return await asyncio.to_thread(_draw_response_card_image, text, variant, title)

    async def render_response_text(
        self,
        text: str,
        *,
        variant: EpisodeCardVariant | None = None,
        title: str = "Bangumi Response",
        rpc_url: str | None = None,
        headless: bool = True,
        max_retries: int = 3,
    ) -> str | None:
        response_variant = _normalize_variant(variant)
        if self.render_mode == "pillow":
            return await self._render_response_pillow(text, response_variant, title)

        pillow_payload: str | None = None

        async def pillow_fallback() -> str | None:
            nonlocal pillow_payload
            if pillow_payload is None:
                pillow_payload = await self._render_response_pillow(
                    text, response_variant, title
                )
            return pillow_payload

        pillow_payload = await pillow_fallback()
        render_data = cast(
            RenderData,
            {
                "pillow_card_data_uri": f"data:image/png;base64,{pillow_payload}",
                "response_variant": response_variant,
                "title": title,
            },
        )

        return await self.render(
            template_path="response/response.html",
            render_data=render_data,
            selector="#response-card",
            sub_dir="response",
            rpc_url=rpc_url,
            headless=headless,
            max_retries=max_retries,
            pillow_fallback=pillow_fallback,
        )
