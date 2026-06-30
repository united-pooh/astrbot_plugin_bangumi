import asyncio
import datetime
import re
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from astrbot.api import logger
from PIL import Image, ImageDraw

from ..bangumi_types import JsonValue
from ..domain.contracts import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EpisodeCardVariant,
    EpisodeItem,
    RenderData,
    is_episode_card_variant,
)
from ..domain.types import SubjectType
from .base_renderer import BaseRenderer
from .pillow_utils import (
    FontType,
    add_shadow,
    draw_centered_text,
    draw_pill,
    draw_text_block,
    ellipsize_text,
    fit_cover,
    get_font,
    image_to_base64,
    load_image_source,
    measure_text,
    measure_text_block,
    select_image_url,
)
from .pillow_utils import (
    stringify_value as _stringify_value,
)

_EPISODE_GRID_COLUMNS = 7
_EPISODE_GRID_CELL_SIZE = 72
_EPISODE_GRID_CELL_GAP = 10
_MAX_EPISODE_GRID_ROWS = 4
_MAX_EPISODE_GRID_ITEMS = _EPISODE_GRID_COLUMNS * _MAX_EPISODE_GRID_ROWS
_SUBJECT_CARD_BASE_HEIGHT = 1638
_SUBJECT_EPISODE_ROW_SHIFT = _EPISODE_GRID_CELL_SIZE + _EPISODE_GRID_CELL_GAP
_SUBJECT_SCORE_TABLE_BOTTOM = 1578
_SUBJECT_FOOTER_HEIGHT = 63
_SUBJECT_FOOTER_BOTTOM_GAP = 20
_SUBJECT_SUMMARY_FOOTER_GAP = 72
_SUBJECT_BOTTOM_PADDING = 80
_SUBJECT_TAG_START_Y = 494
_SUBJECT_TAG_ROW_GAP = 100
_SUBJECT_COVER_BOX = (75, 78, 705, 969)
_SUBJECT_LEFT_PANEL_RIGHT = _SUBJECT_COVER_BOX[2] + _SUBJECT_COVER_BOX[0]
_SUBJECT_RIGHT_X = _SUBJECT_LEFT_PANEL_RIGHT + 60
_SUBJECT_TITLE_PANEL_BOTTOM = 300
_SUBJECT_TOP_ORB_BOX = (2097, -150, 2547, 300)

Color = tuple[int, int, int, int]


@dataclass(frozen=True)
class SubjectCardStyle:
    surface: Color
    card: Color
    outline: Color
    accent: Color
    accent_soft: Color
    accent_text: Color
    title: Color
    secondary: Color
    body: Color
    muted: Color
    panel: Color
    panel_outline: Color
    tag_fill: Color
    side_strip: Color | None
    header_band: Color | None
    top_orb: Color


@dataclass(frozen=True)
class EpisodeGridLayout:
    columns: int
    rows: int
    cell_size: int
    gap: int
    font_size: int
    radius: int
    y_shift: int


_SUBJECT_CARD_STYLES: dict[EpisodeCardVariant, SubjectCardStyle] = {
    "pastel_lightbox": SubjectCardStyle(
        surface=(255, 252, 244, 255),
        card=(255, 255, 252, 255),
        outline=(232, 222, 212, 255),
        accent=(231, 126, 116, 255),
        accent_soft=(255, 229, 224, 255),
        accent_text=(146, 73, 66, 255),
        title=(42, 55, 73, 255),
        secondary=(88, 101, 118, 255),
        body=(54, 65, 82, 255),
        muted=(102, 112, 128, 255),
        panel=(255, 255, 252, 255),
        panel_outline=(232, 222, 212, 255),
        tag_fill=(255, 255, 252, 255),
        side_strip=(221, 243, 229, 255),
        header_band=(206, 236, 242, 255),
        top_orb=(255, 218, 226, 135),
    ),
    "editorial_digest": SubjectCardStyle(
        surface=(238, 240, 232, 255),
        card=(238, 240, 232, 255),
        outline=(211, 216, 208, 255),
        accent=(111, 134, 121, 255),
        accent_soft=(229, 237, 228, 255),
        accent_text=(63, 83, 74, 255),
        title=(31, 36, 44, 255),
        secondary=(92, 105, 112, 255),
        body=(48, 55, 63, 255),
        muted=(105, 128, 120, 255),
        panel=(248, 249, 244, 255),
        panel_outline=(204, 214, 206, 255),
        tag_fill=(246, 248, 242, 255),
        side_strip=(139, 160, 148, 255),
        header_band=(226, 236, 226, 255),
        top_orb=(203, 222, 209, 150),
    ),
    "cinematic_poster": SubjectCardStyle(
        surface=(253, 247, 238, 255),
        card=(255, 255, 250, 255),
        outline=(232, 218, 203, 255),
        accent=(214, 112, 94, 255),
        accent_soft=(250, 229, 215, 255),
        accent_text=(135, 71, 60, 255),
        title=(31, 36, 44, 255),
        secondary=(97, 108, 119, 255),
        body=(45, 53, 65, 255),
        muted=(97, 108, 119, 255),
        panel=(255, 255, 250, 255),
        panel_outline=(232, 218, 203, 255),
        tag_fill=(255, 255, 250, 255),
        side_strip=(255, 241, 226, 255),
        header_band=(255, 231, 213, 255),
        top_orb=(245, 196, 164, 150),
    ),
}


def _resolve_episode_grid_layout(visible_count: int) -> EpisodeGridLayout:
    if visible_count <= 0:
        return EpisodeGridLayout(
            columns=_EPISODE_GRID_COLUMNS,
            rows=0,
            cell_size=_EPISODE_GRID_CELL_SIZE,
            gap=_EPISODE_GRID_CELL_GAP,
            font_size=30,
            radius=16,
            y_shift=0,
        )

    columns = _EPISODE_GRID_COLUMNS
    cell_size = _EPISODE_GRID_CELL_SIZE
    gap = _EPISODE_GRID_CELL_GAP
    font_size = 30
    radius = 16

    rows = (visible_count + columns - 1) // columns
    rows = min(rows, _MAX_EPISODE_GRID_ROWS)
    return EpisodeGridLayout(
        columns=columns,
        rows=rows,
        cell_size=cell_size,
        gap=gap,
        font_size=font_size,
        radius=radius,
        y_shift=max(0, rows - 1) * _SUBJECT_EPISODE_ROW_SHIFT,
    )


def _draw_left_panel(
    draw: ImageDraw.ImageDraw,
    height: int,
    fill: Color,
) -> None:
    """绘制封面承载侧栏,让封面左右留白保持一致。"""

    radius = 60
    panel_box = (0, 0, _SUBJECT_LEFT_PANEL_RIGHT, height - 1)
    draw.rounded_rectangle(panel_box, radius=radius, fill=fill)
    draw.rectangle(
        (radius, 0, _SUBJECT_LEFT_PANEL_RIGHT, height - 1),
        fill=fill,
    )
    draw.rectangle(
        (0, radius, _SUBJECT_LEFT_PANEL_RIGHT, height - radius),
        fill=fill,
    )
    draw.line(
        (_SUBJECT_LEFT_PANEL_RIGHT, 0, _SUBJECT_LEFT_PANEL_RIGHT, height),
        fill=fill,
        width=1,
    )


def _draw_title_panel(
    draw: ImageDraw.ImageDraw,
    width: int,
    fill: Color,
) -> None:
    """标题背景只包住中日文标题,底部停在标题与评分之间的中线。"""

    radius = 60
    panel_box = (
        _SUBJECT_LEFT_PANEL_RIGHT,
        0,
        width - 1,
        _SUBJECT_TITLE_PANEL_BOTTOM,
    )
    draw.rounded_rectangle(panel_box, radius=radius, fill=fill)
    draw.rectangle(
        (
            _SUBJECT_LEFT_PANEL_RIGHT,
            0,
            width - radius,
            _SUBJECT_TITLE_PANEL_BOTTOM,
        ),
        fill=fill,
    )
    draw.rectangle(
        (
            _SUBJECT_LEFT_PANEL_RIGHT,
            radius,
            width - 1,
            _SUBJECT_TITLE_PANEL_BOTTOM,
        ),
        fill=fill,
    )


def _normalize_subject_variant(
    variant: EpisodeCardVariant | None,
) -> EpisodeCardVariant:
    if is_episode_card_variant(variant):
        return variant
    return DEFAULT_EPISODE_CARD_VARIANT


def _process_images(data: RenderData) -> None:
    if "image_url" in data:
        return

    images = data.get("images")
    if not isinstance(images, dict):
        return

    data["image_url"] = select_image_url(images)


def _process_dates(data: RenderData) -> None:
    if "date" in data:
        return
    if "air_date" in data:
        data["date"] = data["air_date"]


def _process_platform(data: RenderData) -> None:
    if "platform" in data:
        return
    if "type" not in data:
        return

    raw_type = data.get("type")
    if isinstance(raw_type, int):
        type_id = raw_type
    elif isinstance(raw_type, str):
        try:
            type_id = int(raw_type)
        except ValueError:
            data["platform"] = "未知"
            return
    else:
        data["platform"] = "未知"
        return

    try:
        data["platform"] = SubjectType(type_id).to_display()
    except ValueError:
        data["platform"] = "未知"


def _infer_air_weekday(aired_weekdays: list[int]) -> str:
    if not aired_weekdays:
        return ""

    weekday_names = {1: "月", 2: "火", 3: "水", 4: "木", 5: "金", 6: "土", 7: "日"}
    recent = aired_weekdays[-4:]
    most_common = Counter(recent).most_common(1)[0][0]
    return weekday_names.get(most_common, "")


def _parse_episode_list(
    episodes: list[EpisodeItem], today: datetime.date
) -> tuple[list[dict[str, int | bool | None]], list[int]]:
    episode_list: list[dict[str, int | bool | None]] = []
    aired_weekdays: list[int] = []

    for ep in episodes:
        if ep.get("type", 0) != 0 or ep.get("ep", 0) == 0:
            continue

        aired = False
        airdate_str = ep.get("airdate")
        if airdate_str:
            try:
                airdate = datetime.datetime.strptime(airdate_str, "%Y-%m-%d").date()
                aired = airdate <= today
                if aired:
                    aired_weekdays.append(airdate.isoweekday())
            except ValueError:
                pass

        if ep.get("comment", 0) > 0:
            aired = True

        episode_list.append({"ep": ep.get("ep"), "aired": aired})

    return episode_list, aired_weekdays


def _process_episodes(data: RenderData) -> None:
    episodes = data.get("episodes")
    if not isinstance(episodes, list):
        return

    today = datetime.date.today()
    normalized_episodes: list[EpisodeItem] = []
    for episode in episodes:
        if isinstance(episode, dict):
            normalized_episodes.append(cast(EpisodeItem, episode))
    episode_list, aired_weekdays = _parse_episode_list(normalized_episodes, today)
    data["episode_list"] = cast(JsonValue, episode_list)

    air_weekday = _infer_air_weekday(aired_weekdays)
    if air_weekday:
        data["air_weekday"] = air_weekday


def preprocess_data(data: RenderData) -> RenderData:
    processed = data.copy()
    _process_images(processed)
    _process_dates(processed)
    _process_platform(processed)
    _process_episodes(processed)
    return processed


def _extract_infobox_text(value: object) -> str:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _extract_infobox_text(item)
            if text:
                parts.append(text)
        return " / ".join(parts)
    if isinstance(value, Mapping):
        if "v" in value:
            return _extract_infobox_text(value["v"])
        if "value" in value:
            return _extract_infobox_text(value["value"])
    return _stringify_value(value)


def _get_infobox_value(data: RenderData, key_name: str) -> str:
    infobox = data.get("infobox")
    if not isinstance(infobox, list):
        return ""

    for item in infobox:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if isinstance(key, str) and key.strip() == key_name:
            return _extract_infobox_text(item.get("value"))
    return ""


def _extract_total_episodes(data: RenderData) -> int | None:
    for key in ("total_episodes", "eps"):
        raw_value = data.get(key)
        if isinstance(raw_value, int) and raw_value > 0:
            return raw_value
        if isinstance(raw_value, str):
            match = re.search(r"\d+", raw_value)
            if match:
                return int(match.group())

    infobox_value = _get_infobox_value(data, "话数")
    match = re.search(r"\d+", infobox_value)
    if match:
        return int(match.group())
    return None


def _extract_subject_titles(data: RenderData) -> tuple[str, str]:
    primary = _stringify_value(data.get("name_cn")) or _stringify_value(
        data.get("name")
    )
    secondary = _stringify_value(data.get("name"))
    if not primary:
        primary = "未知条目"
    if secondary == primary:
        secondary = ""
    return primary, secondary


def _extract_tags(data: RenderData, limit: int = 8) -> list[str]:
    raw_tags = data.get("tags")
    if not isinstance(raw_tags, list):
        return []

    tags: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, Mapping):
            continue
        name = tag.get("name")
        if isinstance(name, str) and name.strip():
            tags.append(name.strip())
        if len(tags) >= limit:
            break
    return tags


def _extract_rating_metrics(data: RenderData) -> tuple[str, str, str]:
    score_text = "--"
    rank_text = "--"
    total_text = "--"

    rating = data.get("rating")
    if not isinstance(rating, Mapping):
        return score_text, rank_text, total_text

    score = rating.get("score")
    if isinstance(score, (int, float)):
        score_text = f"{float(score):.1f}"
    elif isinstance(score, str):
        with suppress(ValueError):
            score_text = f"{float(score):.1f}"

    rank = rating.get("rank")
    if isinstance(rank, int) or (isinstance(rank, str) and rank.isdigit()):
        rank_text = f"#{rank}"

    total = rating.get("total")
    if isinstance(total, int):
        total_text = f"{total:,}"
    elif isinstance(total, str):
        with suppress(ValueError):
            total_text = f"{int(total):,}"

    return score_text, rank_text, total_text


def _extract_rating_counts(data: RenderData) -> dict[int, int]:
    rating = data.get("rating")
    if not isinstance(rating, Mapping):
        return {}
    raw_counts = rating.get("count")
    if not isinstance(raw_counts, Mapping):
        return {}
    count_map = cast(Mapping[object, object], raw_counts)

    counts: dict[int, int] = {}
    for value in range(1, 11):
        raw_count = count_map.get(str(value), count_map.get(value))
        if isinstance(raw_count, int):
            counts[value] = max(0, raw_count)
        elif isinstance(raw_count, str) and raw_count.isdigit():
            counts[value] = int(raw_count)
        else:
            counts[value] = 0
    return counts


def _extract_collection_doing_label(data: RenderData) -> str:
    collection = data.get("collection")
    if not isinstance(collection, Mapping):
        return ""

    doing = collection.get("doing")
    if isinstance(doing, int):
        if doing <= 0:
            return ""
        return f"{doing} 人在看"
    if isinstance(doing, str):
        doing_text = doing.strip()
        if not doing_text or doing_text == "0":
            return ""
        if "人在看" in doing_text:
            return doing_text
        return f"{doing_text} 人在看"
    return ""


def _build_subject_meta(data: RenderData) -> list[str]:
    meta_items: list[str] = []

    date_text = _stringify_value(data.get("date")) or _get_infobox_value(
        data, "放送开始"
    )
    if date_text:
        meta_items.append(f"首播 {date_text}")

    platform = _stringify_value(data.get("platform"))
    if platform:
        meta_items.append(platform)

    total_episodes = _extract_total_episodes(data)
    if total_episodes:
        meta_items.append(f"全 {total_episodes} 话")

    return meta_items


def _build_progress_text(data: RenderData) -> str:
    parts: list[str] = []

    air_weekday = _stringify_value(data.get("air_weekday"))
    if air_weekday:
        parts.append(f"放送: 每周{air_weekday}")

    raw_episode_list = data.get("episode_list")
    if isinstance(raw_episode_list, list) and raw_episode_list:
        aired_count = 0
        total_count = 0
        for item in raw_episode_list:
            if not isinstance(item, Mapping):
                continue
            total_count += 1
            if item.get("aired") is True:
                aired_count += 1

        if total_count > 0:
            total_episodes = _extract_total_episodes(data) or total_count
            parts.append(f"已播: {aired_count}/{total_episodes}")

    return "  ·  ".join(parts)


def _build_subject_summary(data: RenderData) -> str:
    summary = _stringify_value(data.get("summary"))
    if not summary:
        return "暂无简介"
    return re.sub(r"\s+", " ", summary).strip()


def _measure_subject_tag_pill(
    draw: ImageDraw.ImageDraw,
    tag: str,
    tag_font: FontType,
    *,
    tag_x: int,
    tag_right: int,
    tag_padding_x: int,
) -> tuple[str, int]:
    available_text_width = max(48, tag_right - tag_x - tag_padding_x * 2)
    visible_tag = ellipsize_text(draw, tag, tag_font, available_text_width)
    pill_width = measure_text(draw, visible_tag, tag_font)[0] + tag_padding_x * 2
    return visible_tag, pill_width


def _layout_subject_tag_pill(
    draw: ImageDraw.ImageDraw,
    tag: str,
    tag_font: FontType,
    *,
    right_x: int,
    tag_x: int,
    tag_right: int,
    tag_padding_x: int,
) -> tuple[int, str, int, bool]:
    visible_tag, pill_width = _measure_subject_tag_pill(
        draw,
        tag,
        tag_font,
        tag_x=tag_x,
        tag_right=tag_right,
        tag_padding_x=tag_padding_x,
    )
    if tag_x > right_x and tag_x + pill_width > tag_right:
        tag_x = right_x
        visible_tag, pill_width = _measure_subject_tag_pill(
            draw,
            tag,
            tag_font,
            tag_x=tag_x,
            tag_right=tag_right,
            tag_padding_x=tag_padding_x,
        )
        return tag_x, visible_tag, pill_width, True
    return tag_x, visible_tag, pill_width, False


def _measure_subject_tag_rows(
    draw: ImageDraw.ImageDraw,
    tags: list[str],
    tag_font: FontType,
    *,
    right_x: int,
    tag_right: int,
    tag_padding_x: int,
    tag_gap: int,
) -> int:
    tag_x = right_x
    tag_rows = 1
    for tag in tags:
        tag_x, _, pill_width, wrapped = _layout_subject_tag_pill(
            draw,
            tag,
            tag_font,
            right_x=right_x,
            tag_x=tag_x,
            tag_right=tag_right,
            tag_padding_x=tag_padding_x,
        )
        if wrapped:
            tag_rows += 1
        if tag_rows > 2:
            return 2
        tag_x += pill_width + tag_gap
    return tag_rows


def _draw_subject_card_image(
    data: RenderData,
    cover_image: Image.Image | None,
    variant: EpisodeCardVariant = DEFAULT_EPISODE_CARD_VARIANT,
) -> str:
    resolved_variant = _normalize_subject_variant(variant)
    style = _SUBJECT_CARD_STYLES[resolved_variant]
    raw_episode_list = data.get("episode_list")
    episode_items: list[Mapping[str, object]] = []
    if isinstance(raw_episode_list, list):
        episode_items = [item for item in raw_episode_list if isinstance(item, Mapping)]
    visible_episode_items = episode_items[:_MAX_EPISODE_GRID_ITEMS]
    episode_grid = _resolve_episode_grid_layout(len(visible_episode_items))
    episode_y_shift = episode_grid.y_shift

    width = 2400
    right_x = _SUBJECT_RIGHT_X
    tag_font = get_font(36, bold=True)
    summary_font = get_font(45)
    probe_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tags = _extract_tags(data)
    tag_right = 2175
    tag_gap = 24
    tag_padding_x = 36
    tag_rows = _measure_subject_tag_rows(
        probe_draw,
        tags,
        tag_font,
        right_x=right_x,
        tag_right=tag_right,
        tag_padding_x=tag_padding_x,
        tag_gap=tag_gap,
    )
    summary_top = 628 if tag_rows == 1 else 704
    summary_text = _build_subject_summary(data)
    _, full_summary_height = measure_text_block(
        probe_draw,
        summary_text,
        summary_font,
        2300 - right_x,
        max_lines=None,
        line_spacing=24,
    )
    summary_text_y = summary_top + 164
    summary_bottom = summary_text_y + full_summary_height
    left_height = _SUBJECT_CARD_BASE_HEIGHT + episode_y_shift
    left_footer_y = (
        _SUBJECT_SCORE_TABLE_BOTTOM
        + episode_y_shift
        - _SUBJECT_FOOTER_HEIGHT
        - _SUBJECT_FOOTER_BOTTOM_GAP
    )
    footer_y = max(left_footer_y, summary_bottom + _SUBJECT_SUMMARY_FOOTER_GAP)
    height = max(
        left_height,
        footer_y + _SUBJECT_FOOTER_HEIGHT + _SUBJECT_BOTTOM_PADDING,
    )
    primary_title, secondary_title = _extract_subject_titles(data)

    canvas = Image.new("RGBA", (width, height), style.surface)
    card_box = (0, 0, width, height)
    add_shadow(
        canvas,
        card_box,
        radius=60,
        blur=34,
        offset=(0, 18),
        shadow_color=(0, 0, 0, 28),
    )
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=60,
        fill=style.card,
        outline=style.outline,
        width=1,
    )
    if style.side_strip:
        _draw_left_panel(draw, height, style.side_strip)
    if style.header_band:
        _draw_title_panel(draw, width, style.header_band)
    if resolved_variant == "cinematic_poster":
        draw.polygon(
            [
                (_SUBJECT_LEFT_PANEL_RIGHT - 180, 0),
                (_SUBJECT_LEFT_PANEL_RIGHT, 0),
                (_SUBJECT_LEFT_PANEL_RIGHT - 120, height),
                (_SUBJECT_LEFT_PANEL_RIGHT - 300, height),
            ],
            fill=(255, 232, 206, 255),
        )

    decoration = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    decoration_draw = ImageDraw.Draw(decoration)
    decoration_draw.ellipse(
        _SUBJECT_TOP_ORB_BOX,
        fill=style.top_orb,
    )
    if resolved_variant == "cinematic_poster":
        decoration_draw.ellipse(
            (-180, 940, 520, 1640),
            fill=(255, 232, 206, 120),
        )
    canvas.alpha_composite(decoration)
    draw = ImageDraw.Draw(canvas)

    cover_box = _SUBJECT_COVER_BOX
    add_shadow(
        canvas,
        cover_box,
        radius=36,
        blur=30,
        offset=(0, 12),
        shadow_color=(0, 0, 0, 22),
    )
    if cover_image is None:
        cover_card = Image.new(
            "RGBA",
            (cover_box[2] - cover_box[0], cover_box[3] - cover_box[1]),
            style.accent_soft,
        )
        cover_draw = ImageDraw.Draw(cover_card)
        cover_draw.rounded_rectangle(
            (0, 0, cover_card.width - 1, cover_card.height - 1),
            radius=36,
            fill=style.accent_soft,
        )
    else:
        cover_card = fit_cover(
            cover_image,
            (cover_box[2] - cover_box[0], cover_box[3] - cover_box[1]),
            36,
        )
    canvas.alpha_composite(cover_card, (cover_box[0], cover_box[1]))
    draw = ImageDraw.Draw(canvas)

    title_font = get_font(84, bold=True)
    subtitle_font = get_font(42, bold=True)
    score_font = get_font(108, bold=True)
    star_font = get_font(78, bold=True)
    meta_font = get_font(39, bold=True)
    summary_label_font = get_font(42, bold=True)
    footer_font = get_font(39)
    small_font = get_font(30, bold=True)

    air_weekday = _stringify_value(data.get("air_weekday"))
    if air_weekday:
        badge_size = 172
        badge_box = (width - badge_size, 0, width, badge_size)
        draw.rounded_rectangle(
            badge_box,
            radius=42,
            fill=style.accent,
        )
        day_font = get_font(66, bold=True)
        sub_font = get_font(25, bold=True)
        day_w, _ = measure_text(draw, air_weekday, day_font)
        sub_w, _ = measure_text(draw, "曜日", sub_font)
        center_x = (badge_box[0] + badge_box[2]) // 2
        draw.text(
            (center_x - day_w // 2, 22),
            air_weekday,
            font=day_font,
            fill=(255, 255, 255, 255),
        )
        draw.text(
            (center_x - sub_w // 2, 100),
            "曜日",
            font=sub_font,
            fill=(252, 252, 252, 255),
        )

    draw_text_block(
        draw,
        (right_x, 80, 2180, 196),
        primary_title,
        title_font,
        style.title,
        max_lines=1,
        line_spacing=0,
    )
    if secondary_title:
        draw_text_block(
            draw,
            (right_x, 209, 2180, 268),
            secondary_title,
            subtitle_font,
            style.secondary,
            max_lines=1,
        )

    score_text, rank_text, total_text = _extract_rating_metrics(data)
    draw.text((right_x, 350), "★", font=star_font, fill=style.accent)
    draw.text((right_x + 87, 333), score_text, font=score_font, fill=style.accent)
    rank_width = max(168, measure_text(draw, rank_text, meta_font)[0] + 72)
    rank_x = right_x + 275
    rank_box = (rank_x, 337, rank_x + rank_width, 407)
    draw.rounded_rectangle(
        rank_box,
        radius=24,
        fill=style.accent_soft,
    )
    draw_centered_text(draw, rank_box, rank_text, meta_font, style.accent_text)
    count_label = f"{total_text.replace(',', '')} 人评分"
    count_width = measure_text(draw, count_label, meta_font)[0] + 84
    badge_right = width - 75
    collection_label = _extract_collection_doing_label(data)
    if collection_label:
        collection_width = measure_text(draw, collection_label, meta_font)[0] + 84
        collection_box = (badge_right - collection_width, 334, badge_right, 408)
        draw.rounded_rectangle(
            collection_box,
            radius=37,
            fill=style.panel,
        )
        draw_centered_text(
            draw,
            collection_box,
            collection_label,
            meta_font,
            style.muted,
        )
        badge_right = collection_box[0] - 24
    count_box = (badge_right - count_width, 334, badge_right, 408)
    draw.rounded_rectangle(count_box, radius=37, fill=style.panel)
    draw_centered_text(draw, count_box, count_label, meta_font, style.muted)

    tag_x = right_x
    tag_y = _SUBJECT_TAG_START_Y
    tag_padding_y = 16
    rendered_tag_rows = 1
    for tag in tags:
        tag_x, visible_tag, _, wrapped = _layout_subject_tag_pill(
            draw,
            tag,
            tag_font,
            right_x=right_x,
            tag_x=tag_x,
            tag_right=tag_right,
            tag_padding_x=tag_padding_x,
        )
        if wrapped:
            tag_y += _SUBJECT_TAG_ROW_GAP
            rendered_tag_rows += 1
        if rendered_tag_rows > 2:
            break

        pill_width = draw_pill(
            draw,
            (tag_x, tag_y),
            visible_tag,
            tag_font,
            fill=style.tag_fill,
            text_fill=style.secondary,
            outline=style.panel_outline,
            padding_x=tag_padding_x,
            padding_y=tag_padding_y,
        )
        tag_x += pill_width + tag_gap

    for x in range(right_x, 2325, 22):
        draw.line(
            (x, summary_top, min(x + 10, 2325), summary_top),
            fill=style.panel_outline,
            width=3,
        )
    draw.text(
        (right_x, summary_top + 69),
        "简介",
        font=summary_label_font,
        fill=style.title,
    )
    draw_text_block(
        draw,
        (right_x, summary_text_y, 2300, footer_y - _SUBJECT_SUMMARY_FOOTER_GAP),
        summary_text,
        summary_font,
        style.body,
        max_lines=None,
        line_spacing=24,
    )

    if episode_items:
        ep_box = (75, 1014, 705, 1213 + episode_y_shift)
        add_shadow(
            canvas,
            ep_box,
            radius=36,
            blur=30,
            offset=(0, 12),
            shadow_color=(0, 0, 0, 18),
        )
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(ep_box, radius=36, fill=style.panel)
        aired_count = sum(1 for item in episode_items if item.get("aired") is True)
        draw.text((105, 1045), "放送进度", font=small_font, fill=style.muted)
        progress = f"{aired_count} / {len(episode_items)}"
        progress_w, _ = measure_text(draw, progress, small_font)
        draw.text(
            (672 - progress_w, 1045), progress, font=small_font, fill=style.accent
        )
        cell_x = 105
        cell_y = 1102
        cell_size = episode_grid.cell_size
        cell_gap = episode_grid.gap
        cell_font = get_font(episode_grid.font_size, bold=True)
        for item in visible_episode_items:
            fill = style.accent if item.get("aired") is True else style.accent_soft
            text_fill = (
                (255, 255, 255, 255) if item.get("aired") is True else style.muted
            )
            draw.rounded_rectangle(
                (cell_x, cell_y, cell_x + cell_size, cell_y + cell_size),
                radius=episode_grid.radius,
                fill=fill,
            )
            label = str(item.get("ep") or "")
            draw_centered_text(
                draw,
                (cell_x, cell_y, cell_x + cell_size, cell_y + cell_size),
                label,
                cell_font,
                text_fill,
            )
            cell_x += cell_size + cell_gap
            if cell_x + cell_size > 675:
                cell_x = 105
                cell_y += cell_size + cell_gap

    rating_counts = _extract_rating_counts(data)
    if rating_counts:
        chart_box = (75, 1251 + episode_y_shift, 705, 1578 + episode_y_shift)
        add_shadow(
            canvas,
            chart_box,
            radius=36,
            blur=30,
            offset=(0, 12),
            shadow_color=(0, 0, 0, 18),
        )
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(chart_box, radius=36, fill=style.panel)
        title = "评分分布"
        title_w, _ = measure_text(draw, title, small_font)
        draw.text(
            ((chart_box[0] + chart_box[2] - title_w) // 2, 1296 + episode_y_shift),
            title,
            font=small_font,
            fill=style.muted,
        )
        max_count = max(rating_counts.values()) or 1
        bar_area = (105, 1352 + episode_y_shift, 675, 1490 + episode_y_shift)
        bar_width = 42
        gap = 15
        for index, value in enumerate(range(1, 11)):
            count = rating_counts[value]
            bar_height = max(6, int((count / max_count) * (bar_area[3] - bar_area[1])))
            x = bar_area[0] + index * (bar_width + gap)
            color = style.accent_soft if value < 8 else style.accent
            draw.rounded_rectangle(
                (x, bar_area[3] - bar_height, x + bar_width, bar_area[3]),
                radius=6,
                fill=color,
            )
        draw.line(
            (105, 1498 + episode_y_shift, 675, 1498 + episode_y_shift),
            fill=style.panel_outline,
            width=3,
        )
        for label, x in (("1", 105), ("5", 357), ("10", 639)):
            draw.text(
                (x, 1522 + episode_y_shift),
                label,
                font=get_font(27),
                fill=style.muted,
            )

    date_text = _stringify_value(data.get("date"))
    platform = _stringify_value(data.get("platform"))
    if date_text:
        date_box = (right_x, footer_y, right_x + 336, footer_y + 63)
        draw.rounded_rectangle(date_box, radius=18, fill=style.panel)
        icon_x = right_x + 31
        icon_y = footer_y + 17
        draw.rounded_rectangle(
            (icon_x, icon_y + 6, icon_x + 36, icon_y + 39),
            radius=4,
            outline=style.muted,
            width=3,
        )
        draw.rectangle((icon_x, icon_y + 6, icon_x + 36, icon_y + 16), fill=style.muted)
        draw.line(
            (icon_x + 9, icon_y, icon_x + 9, icon_y + 10),
            fill=style.muted,
            width=3,
        )
        draw.line(
            (icon_x + 27, icon_y, icon_x + 27, icon_y + 10),
            fill=style.muted,
            width=3,
        )
        draw.text(
            (right_x + 96, footer_y + 12),
            date_text,
            font=footer_font,
            fill=style.muted,
        )
    if platform:
        platform_box = (right_x + 408, footer_y, right_x + 586, footer_y + 63)
        draw.rounded_rectangle(platform_box, radius=18, fill=style.panel)
        tv_x = right_x + 439
        tv_y = footer_y + 18
        draw.rounded_rectangle(
            (tv_x, tv_y + 4, tv_x + 38, tv_y + 35),
            radius=5,
            outline=style.muted,
            width=3,
        )
        draw.line((tv_x + 10, tv_y, tv_x + 19, tv_y + 7), fill=style.muted, width=3)
        draw.line((tv_x + 28, tv_y, tv_x + 19, tv_y + 7), fill=style.muted, width=3)
        draw.line(
            (tv_x + 13, tv_y + 40, tv_x + 25, tv_y + 40),
            fill=style.muted,
            width=3,
        )
        draw.text(
            (right_x + 504, footer_y + 12),
            platform,
            font=footer_font,
            fill=style.muted,
        )
    subject_id = _stringify_value(data.get("id"))
    if subject_id:
        id_label = f"ID: {subject_id}"
        id_w, _ = measure_text(draw, id_label, footer_font)
        draw.text(
            (width - 75 - id_w, footer_y + 14),
            id_label,
            font=footer_font,
            fill=style.muted,
        )

    # Browser locator screenshots keep a faint antialiased edge around the card.
    alpha = canvas.getchannel("A")
    alpha_draw = ImageDraw.Draw(alpha)
    alpha_draw.rectangle((0, 0, width - 1, height - 1), outline=254, width=1)
    alpha.putpixel((0, 0), 2)
    canvas.putalpha(alpha)

    return image_to_base64(canvas)


class SubjectRenderer(BaseRenderer):
    async def _render_subject_card_pillow(
        self,
        render_data: RenderData,
        variant: EpisodeCardVariant = DEFAULT_EPISODE_CARD_VARIANT,
    ) -> str:
        cover_source = _stringify_value(render_data.get("image_url"))
        cover_image = await load_image_source(
            cover_source, self._session, proxy_url=self.proxy_url
        )
        return await asyncio.to_thread(
            _draw_subject_card_image,
            render_data,
            cover_image,
            variant,
        )

    async def _render_subject_card_pillow_with_placeholder(
        self,
        render_data: RenderData,
        variant: EpisodeCardVariant = DEFAULT_EPISODE_CARD_VARIANT,
    ) -> str:
        try:
            return await self._render_subject_card_pillow(render_data, variant)
        except Exception as e:
            logger.warning(f"[+] Pillow 条目卡片渲染失败,使用纯 PIL 退避卡片: {e}")
            fallback_data = render_data.copy()
            fallback_data["image_url"] = ""
            return await asyncio.to_thread(
                _draw_subject_card_image,
                fallback_data,
                None,
                variant,
            )

    async def render_subject_card(
        self,
        data: RenderData,
        rpc_url: str | None = None,
        headless: bool = True,
        wait_time: int = 0,
        max_retries: int = 3,
        timeout: int = 30000,
        variant: EpisodeCardVariant | None = None,
    ) -> str | None:
        render_data = preprocess_data(data)
        subject_variant = _normalize_subject_variant(variant)
        if self.render_mode == "pillow":
            return await self._render_subject_card_pillow_with_placeholder(
                render_data,
                subject_variant,
            )

        pillow_payload: str | None = None

        async def pillow_fallback() -> str | None:
            nonlocal pillow_payload
            if pillow_payload is None:
                pillow_payload = (
                    await self._render_subject_card_pillow_with_placeholder(
                        render_data,
                        subject_variant,
                    )
                )
            return pillow_payload

        pillow_payload = await pillow_fallback()
        carrier_data = cast(
            RenderData,
            {
                "pillow_card_data_uri": f"data:image/png;base64,{pillow_payload}",
                "subject_variant": subject_variant,
                "title": _stringify_value(render_data.get("name_cn"))
                or _stringify_value(render_data.get("name"))
                or "Subject Card",
            },
        )

        return await self.render(
            template_path="subject/subject_carrier.html",
            render_data=carrier_data,
            selector="#subject-card",
            sub_dir="subject",
            rpc_url=rpc_url,
            headless=headless,
            max_retries=max_retries,
            wait_time=wait_time,
            timeout=timeout,
            pillow_fallback=pillow_fallback,
        )

    async def render_batch_subject_cards_to_base64(
        self,
        data_list: list[RenderData],
        rpc_url: str | None = None,
        headless: bool = True,
        wait_time: int = 0,
        max_retries: int = 3,
        timeout: int = 30000,
        max_concurrency: int = 3,
        variant: EpisodeCardVariant | None = None,
    ) -> list[str]:
        semaphore = asyncio.Semaphore(max_concurrency)
        subject_variant = _normalize_subject_variant(variant)

        async def _limited_render(data: RenderData) -> str | None:
            async with semaphore:
                return await self.render_subject_card(
                    data=data,
                    rpc_url=rpc_url,
                    headless=headless,
                    wait_time=wait_time,
                    max_retries=max_retries,
                    timeout=timeout,
                    variant=subject_variant,
                )

        tasks = [_limited_render(data) for data in data_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: list[str] = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning(f"批量渲染第 {i + 1} 项失败: {res}")
            elif isinstance(res, str) and res:
                valid_results.append(res)
        return valid_results
