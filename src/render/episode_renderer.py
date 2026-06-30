import asyncio
from typing import cast

from astrbot.api import logger
from PIL import Image, ImageDraw

from ..domain.contracts import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EPISODE_CARD_VARIANTS,
    EpisodeCardVariant,
    RenderData,
    is_episode_card_variant,
)
from ..domain.schemas import Episode
from .base_renderer import BaseRenderer
from .pillow_utils import (
    add_shadow,
    blend_color,
    create_placeholder_image,
    draw_centered_text,
    draw_pill,
    draw_text_block,
    fit_cover,
    get_font,
    get_image_accent,
    image_to_base64,
    line_height,
    load_image_source,
    measure_text,
    wrap_text,
)
from .pillow_utils import (
    stringify_value as _stringify_value,
)

_EPISODE_CARD_SIZE = (2304, 3072)


def _extract_episode_titles(data: RenderData) -> tuple[str, str]:
    primary = _stringify_value(data.get("name_cn")) or _stringify_value(
        data.get("name")
    )
    secondary = _stringify_value(data.get("name"))
    if not primary:
        primary = "更新内容待补充"
    if secondary == primary:
        secondary = ""
    return primary, secondary


def _format_duration_label(data: RenderData) -> str:
    existing_label = _stringify_value(data.get("duration_label"))
    if existing_label:
        return existing_label

    duration_text = _stringify_value(data.get("duration"))
    if ":" in duration_text:
        parts = duration_text.split(":")
        if len(parts) >= 3:
            hours = int(parts[0]) if parts[0].isdigit() else 0
            minutes = int(parts[1]) if parts[1].isdigit() else 0
            return f"{hours * 60 + minutes}min"
        if parts[0].isdigit():
            return f"{int(parts[0])}min"
    if duration_text.endswith("min"):
        return duration_text

    duration_seconds = data.get("duration_seconds")
    if isinstance(duration_seconds, int) and duration_seconds > 0:
        return f"{max(1, round(duration_seconds / 60))}min"
    return "24min"


def _normalize_episode_variant(variant: object | None) -> EpisodeCardVariant:
    if variant is None:
        return DEFAULT_EPISODE_CARD_VARIANT
    if is_episode_card_variant(variant):
        return variant
    known = ", ".join(EPISODE_CARD_VARIANTS)
    raise ValueError(
        f"Unknown episode card variant {variant!r}; expected one of: {known}"
    )


def _format_episode_label(render_data: RenderData) -> str:
    episode_number = render_data.get("ep")
    sort_number = render_data.get("sort")
    number_source = sort_number if sort_number not in (None, "") else episode_number
    if isinstance(number_source, int):
        return f"EP.{number_source:02d}"
    if isinstance(number_source, str) and number_source.isdigit():
        return f"EP.{int(number_source):02d}"
    if isinstance(episode_number, int):
        return f"EP.{episode_number:02d}"
    return "EP.01"


def _format_airdate_parts(render_data: RenderData) -> tuple[str, str]:
    airdate = _stringify_value(render_data.get("airdate"))
    year = airdate.split("-", 1)[0] if "-" in airdate else airdate
    return airdate or "TBA", year or "2026"


def _format_comment_label(render_data: RenderData) -> str:
    comment = render_data.get("comment")
    if isinstance(comment, int) and comment > 0:
        return f"{comment} comments"
    return ""


def _format_metadata(
    render_data: RenderData, *, prefer_airdate: bool = False
) -> list[str]:
    airdate, year = _format_airdate_parts(render_data)
    duration = _stringify_value(
        render_data.get("duration_label")
    ) or _format_duration_label(render_data)
    metadata = [
        airdate if prefer_airdate else year,
        duration,
        _format_comment_label(render_data),
    ]
    return [item for item in metadata if item]


def _fit_episode_cover(
    render_data: RenderData,
    cover_image: Image.Image | None,
    size: tuple[int, int],
    *,
    radius: int = 0,
) -> Image.Image:
    primary_title, _secondary_title = _extract_episode_titles(render_data)
    if cover_image is None:
        return create_placeholder_image(size, primary_title, get_image_accent(None))
    return fit_cover(cover_image, size, radius)


def _draw_vertical_gradient(
    canvas: Image.Image,
    start_y: int,
    end_y: int,
    *,
    start_alpha: int,
    end_alpha: int,
) -> None:
    width = canvas.width
    gradient = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(max(0, start_y), min(canvas.height, end_y)):
        ratio = (y - start_y) / max(end_y - start_y - 1, 1)
        alpha = int(start_alpha + (end_alpha - start_alpha) * ratio)
        gradient_draw.line((0, y, width, y), fill=(0, 0, 0, alpha))
    canvas.alpha_composite(gradient)


def _draw_cinematic_poster(
    render_data: RenderData,
    cover_image: Image.Image | None,
) -> Image.Image:
    width, height = _EPISODE_CARD_SIZE
    primary_title, _secondary_title = _extract_episode_titles(render_data)
    accent = get_image_accent(cover_image)

    canvas = _fit_episode_cover(render_data, cover_image, _EPISODE_CARD_SIZE)
    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")
    if cover_image is None:
        draw = ImageDraw.Draw(canvas)
        icon_font = get_font(260, bold=True)
        icon = "EP"
        icon_width, icon_height = measure_text(draw, icon, icon_font)
        draw.text(
            ((width - icon_width) // 2, (height - icon_height) // 2 - 420),
            icon,
            font=icon_font,
            fill=(*blend_color(accent, 0.25, (20, 20, 24)), 210),
        )

    _draw_vertical_gradient(
        canvas,
        int(height * 0.4),
        height,
        start_alpha=0,
        end_alpha=242,
    )
    draw = ImageDraw.Draw(canvas)

    episode_label = _format_episode_label(render_data)
    ep_font = get_font(168, bold=True)
    title_font = get_font(132, bold=True)
    meta_font = get_font(51, bold=True)
    desc_font = get_font(51)

    left = 84
    right = 84
    content_top_padding = 96
    content_bottom_padding = 84
    title_line_spacing = 14
    title_row_margin_bottom = 42
    metadata_height = line_height(draw, meta_font)
    metadata_margin_bottom = 56
    description_line_spacing = 34
    description_margin_bottom = 72

    ep_width, _ = measure_text(draw, episode_label, ep_font)
    title_x = left + ep_width + 21
    title_lines = wrap_text(
        draw,
        primary_title,
        title_font,
        width - right - title_x,
        2,
    )
    title_max_lines = (
        1 if len(title_lines) > 1 and len(title_lines[-1].strip()) <= 2 else 2
    )
    if title_max_lines == 1:
        title_lines = wrap_text(
            draw,
            primary_title,
            title_font,
            width - right - title_x,
            1,
        )
    title_text_height = (
        len(title_lines) * line_height(draw, title_font)
        + max(0, len(title_lines) - 1) * title_line_spacing
    )
    title_row_height = max(line_height(draw, ep_font), title_text_height)

    description = _stringify_value(render_data.get("desc"))
    description_lines = wrap_text(
        draw,
        description,
        desc_font,
        width - left - right,
        3,
    )
    description_height = (
        len(description_lines) * line_height(draw, desc_font)
        + max(0, len(description_lines) - 1) * description_line_spacing
    )
    description_block_height = (
        description_height + description_margin_bottom if description_lines else 0
    )
    content_height = (
        content_top_padding
        + title_row_height
        + title_row_margin_bottom
        + metadata_height
        + metadata_margin_bottom
        + description_block_height
        + content_bottom_padding
    )
    header_y = height - content_height + content_top_padding

    draw.text((left, header_y), episode_label, font=ep_font, fill=(236, 72, 153, 255))
    draw_text_block(
        draw,
        (title_x, header_y + 15, width - right, header_y + title_row_height),
        primary_title,
        title_font,
        (255, 255, 255, 255),
        max_lines=title_max_lines,
        line_spacing=title_line_spacing,
    )
    meta_text = "   |   ".join(_format_metadata(render_data))
    metadata_y = header_y + title_row_height + title_row_margin_bottom
    draw.text((left, metadata_y), meta_text, font=meta_font, fill=(204, 204, 204, 255))

    if description_lines:
        description_y = metadata_y + metadata_height + metadata_margin_bottom
        draw_text_block(
            draw,
            (left, description_y, width - right, height - content_bottom_padding),
            description,
            desc_font,
            (216, 216, 216, 255),
            max_lines=3,
            line_spacing=description_line_spacing,
        )
    return canvas


def _draw_editorial_digest(
    render_data: RenderData,
    cover_image: Image.Image | None,
) -> Image.Image:
    width, height = _EPISODE_CARD_SIZE
    primary_title, secondary_title = _extract_episode_titles(render_data)
    accent = get_image_accent(cover_image)
    ink = (30, 34, 38, 255)
    muted = (93, 98, 105, 255)
    paper = blend_color(accent, 0.86, (244, 241, 232))

    canvas = Image.new("RGBA", (width, height), (*paper, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (0, 0, 42, height), fill=(*blend_color(accent, 0.08, (40, 48, 56)), 255)
    )
    draw.rectangle(
        (width - 104, 0, width, height),
        fill=(*blend_color(accent, 0.68, (255, 255, 255)), 255),
    )

    cover_box = (156, 156, width - 156, 1436)
    add_shadow(
        canvas,
        cover_box,
        radius=48,
        blur=54,
        offset=(0, 30),
        shadow_color=(39, 44, 52, 72),
    )
    cover = _fit_episode_cover(
        render_data,
        cover_image,
        (cover_box[2] - cover_box[0], cover_box[3] - cover_box[1]),
        radius=48,
    )
    canvas.alpha_composite(cover, (cover_box[0], cover_box[1]))

    label_font = get_font(42, bold=True)
    episode_label = _format_episode_label(render_data)
    draw_pill(
        draw,
        (cover_box[0] + 54, cover_box[1] + 54),
        "EPISODE DIGEST",
        label_font,
        fill=(255, 255, 255, 232),
        text_fill=(*blend_color(accent, 0.1, (20, 26, 33)), 255),
        padding_x=28,
        padding_y=14,
    )

    ep_font = get_font(136, bold=True)
    title_font = get_font(126, bold=True)
    secondary_font = get_font(56)
    meta_font = get_font(48, bold=True)
    desc_font = get_font(58)
    rule_color = (*blend_color(accent, 0.35, (55, 60, 66)), 255)

    text_left = 156
    text_right = width - 156
    y = cover_box[3] + 132
    draw.text((text_left, y), episode_label, font=ep_font, fill=rule_color)
    draw.line((text_left, y + 188, text_right, y + 188), fill=rule_color, width=8)
    y += 242

    title_height = draw_text_block(
        draw,
        (text_left, y, text_right, y + 360),
        primary_title,
        title_font,
        ink,
        max_lines=2,
        line_spacing=18,
    )
    y += max(title_height, 132) + 24

    if secondary_title:
        secondary_height = draw_text_block(
            draw,
            (text_left, y, text_right, y + 96),
            secondary_title,
            secondary_font,
            muted,
            max_lines=1,
            line_spacing=0,
        )
        y += secondary_height + 66
    else:
        y += 42

    meta_y = y
    meta_parts = _format_metadata(render_data, prefer_airdate=True)
    meta_x = text_left
    meta_padding_x = 30
    meta_padding_y = 16
    meta_row_height = 0
    for meta in meta_parts:
        preview_width = measure_text(draw, meta, meta_font)[0] + meta_padding_x * 2
        if meta_x > text_left and meta_x + preview_width > text_right:
            meta_x = text_left
            meta_y += meta_row_height + 22
            meta_row_height = 0
        width_used = draw_pill(
            draw,
            (meta_x, meta_y),
            meta,
            meta_font,
            fill=(*blend_color(accent, 0.82, (255, 255, 255)), 255),
            text_fill=ink,
            padding_x=meta_padding_x,
            padding_y=meta_padding_y,
            outline=(*blend_color(accent, 0.48, (165, 165, 165)), 255),
        )
        meta_row_height = max(
            meta_row_height,
            measure_text(draw, meta, meta_font)[1] + meta_padding_y * 2,
        )
        meta_x += width_used + 24
    y = meta_y + meta_row_height + 58

    description = _stringify_value(render_data.get("desc"))
    if description:
        draw_text_block(
            draw,
            (text_left, y, text_right, height - 168),
            description,
            desc_font,
            (54, 59, 64, 255),
            max_lines=5,
            line_spacing=30,
        )

    draw.text(
        (text_left, height - 122),
        "BANGUMI UPDATE",
        font=label_font,
        fill=(115, 119, 124, 255),
    )
    return canvas


def _draw_pastel_lightbox(
    render_data: RenderData,
    cover_image: Image.Image | None,
) -> Image.Image:
    width, height = _EPISODE_CARD_SIZE
    primary_title, secondary_title = _extract_episode_titles(render_data)
    sky = (198, 232, 246)
    blush = (255, 202, 216)
    mint = (218, 244, 226)
    cream = (255, 252, 241)
    ink = (45, 54, 70, 255)
    coral = (226, 104, 139)

    canvas = Image.new("RGBA", (width, height), (*cream, 255))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, width, 420), fill=(*sky, 255))
    draw.rectangle((0, 420, 260, height), fill=(*mint, 255))
    draw.rounded_rectangle(
        (width - 612, 84, width - 86, 392), radius=44, fill=(*blush, 255)
    )
    draw.rounded_rectangle(
        (116, height - 642, width - 116, height - 96),
        radius=42,
        fill=(255, 255, 252, 255),
    )

    mat_box = (164, 304, width - 164, 1580)
    add_shadow(
        canvas,
        mat_box,
        radius=42,
        blur=46,
        offset=(0, 28),
        shadow_color=(109, 126, 145, 52),
    )
    draw.rounded_rectangle(
        mat_box,
        radius=42,
        fill=(255, 255, 252, 255),
        outline=(235, 223, 214, 255),
        width=3,
    )
    cover_box = (216, 360, width - 216, 1516)
    cover = _fit_episode_cover(
        render_data,
        cover_image,
        (cover_box[2] - cover_box[0], cover_box[3] - cover_box[1]),
        radius=28,
    )
    canvas.alpha_composite(cover, (cover_box[0], cover_box[1]))
    draw.rounded_rectangle(
        cover_box,
        radius=28,
        outline=(255, 255, 252, 255),
        width=8,
    )

    episode_label = _format_episode_label(render_data)
    label_font = get_font(48, bold=True)
    ep_font = get_font(164, bold=True)
    title_font = get_font(142, bold=True)
    secondary_font = get_font(54)
    meta_font = get_font(46, bold=True)
    desc_font = get_font(50)

    draw.text((116, 124), "PASTEL LIGHTBOX", font=label_font, fill=ink)
    airdate_text = _format_airdate_parts(render_data)[0]
    airdate_width, _ = measure_text(draw, airdate_text, label_font)
    draw.text(
        (width - 116 - airdate_width, 124),
        airdate_text,
        font=label_font,
        fill=(88, 101, 118, 255),
    )
    ep_box = (116, 1648, 680, 1840)
    draw.rounded_rectangle(ep_box, radius=36, fill=(*blush, 255))
    draw_centered_text(
        draw,
        ep_box,
        episode_label,
        ep_font,
        (255, 255, 252, 255),
    )
    draw.text(
        (720, 1716), "soft daylight edition", font=meta_font, fill=(88, 101, 118, 255)
    )

    draw_text_block(
        draw,
        (146, 1910, width - 146, 2202),
        primary_title,
        title_font,
        ink,
        max_lines=2,
        line_spacing=12,
    )
    if secondary_title:
        draw_text_block(
            draw,
            (146, 2226, width - 146, 2304),
            secondary_title,
            secondary_font,
            (94, 104, 119, 255),
            max_lines=1,
            line_spacing=0,
        )

    meta_text = "  /  ".join(_format_metadata(render_data, prefer_airdate=True))
    draw.text(
        (146, 2368),
        meta_text,
        font=meta_font,
        fill=(*coral, 255),
    )
    description = _stringify_value(render_data.get("desc"))
    if description:
        desc_box = (146, 2450, width - 146, height - 162)
        draw.rounded_rectangle(
            desc_box,
            radius=24,
            fill=(255, 255, 252, 255),
            outline=(230, 217, 208, 255),
            width=2,
        )
        draw_text_block(
            draw,
            (desc_box[0] + 30, desc_box[1] + 30, desc_box[2] - 30, desc_box[3] - 28),
            description,
            desc_font,
            (55, 62, 74, 255),
            max_lines=3,
            line_spacing=22,
        )
    return canvas


def _draw_episode_card_image(
    render_data: RenderData,
    cover_image: Image.Image | None,
) -> str:
    variant = _normalize_episode_variant(render_data.get("episode_variant"))
    if variant == "cinematic_poster":
        canvas = _draw_cinematic_poster(render_data, cover_image)
    elif variant == "editorial_digest":
        canvas = _draw_editorial_digest(render_data, cover_image)
    else:
        canvas = _draw_pastel_lightbox(render_data, cover_image)
    return image_to_base64(canvas)


class EpisodeRenderer(BaseRenderer):
    async def _render_episode_pillow(self, render_data: RenderData) -> str:
        image_url = _stringify_value(render_data.get("image_url"))
        cover_image = await load_image_source(
            image_url, self._session, proxy_url=self.proxy_url
        )
        return await asyncio.to_thread(
            _draw_episode_card_image, render_data, cover_image
        )

    async def _render_episode_pillow_with_placeholder(
        self, render_data: RenderData
    ) -> str:
        try:
            return await self._render_episode_pillow(render_data)
        except Exception as e:
            logger.warning(f"[+] Pillow 单集卡片渲染失败,使用纯 PIL 退避卡片: {e}")
            fallback_data = render_data.copy()
            fallback_data["image_url"] = ""
            return await asyncio.to_thread(
                _draw_episode_card_image,
                fallback_data,
                None,
            )

    async def render_episode(
        self,
        episode_data: Episode,
        rpc_url: str | None = None,
        headless: bool = True,
        max_retries: int = 3,
        *,
        variant: EpisodeCardVariant | None = None,
    ) -> str | None:
        """
        渲染单集信息卡片并返回 Base64 编码的图片字符串
        """
        episode_variant = _normalize_episode_variant(variant)
        render_data = cast(RenderData, episode_data.model_dump())
        render_data["duration_label"] = _format_duration_label(render_data)
        render_data["episode_variant"] = episode_variant

        if self.render_mode == "pillow":
            return await self._render_episode_pillow_with_placeholder(render_data)

        pillow_payload: str | None = None

        async def pillow_fallback() -> str | None:
            nonlocal pillow_payload
            if pillow_payload is None:
                pillow_payload = await self._render_episode_pillow_with_placeholder(
                    render_data
                )
            return pillow_payload

        try:
            pillow_payload = await pillow_fallback()
        except Exception as e:
            logger.warning(f"[+] 单集卡片像素对齐预渲染失败,继续使用 HTML 模板: {e}")
        else:
            if pillow_payload:
                render_data["pillow_card_data_uri"] = (
                    f"data:image/png;base64,{pillow_payload}"
                )

        return await self.render(
            template_path="update/episode.html",
            render_data=render_data,
            selector="#card-container",
            rpc_url=rpc_url,
            headless=headless,
            max_retries=max_retries,
            pillow_fallback=pillow_fallback,
        )
