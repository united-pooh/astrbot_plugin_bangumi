import asyncio
import datetime
from collections.abc import Mapping
from typing import cast

import aiohttp
from astrbot.api import logger
from PIL import Image, ImageDraw

from ..domain.contracts import CalendarDay, CalendarItem, CalendarWeekday, RenderData
from .base_renderer import BaseRenderer
from .pillow_utils import (
    FontType,
    add_shadow,
    create_placeholder_image,
    draw_text_block,
    fit_cover,
    get_font,
    image_to_base64,
    load_image_source,
    measure_text,
    select_image_url,
)
from .pillow_utils import (
    stringify_value as _stringify_value,
)

_CALENDAR_COVER_CONCURRENCY = 6


def _item_title(item: CalendarItem) -> str:
    return (
        _stringify_value(item.get("name_cn"))
        or _stringify_value(item.get("name"))
        or "未知条目"
    )


def _item_image_source(item: CalendarItem) -> str:
    return select_image_url(item.get("images"), priority=("common", "large", "medium"))


def _item_score(item: CalendarItem) -> str:
    rating = item.get("rating")
    if not isinstance(rating, Mapping):
        return ""
    score = rating.get("score")
    if isinstance(score, (int, float)):
        return f"{float(score):.1f}"
    if isinstance(score, str):
        return score
    return ""


def _item_rank(item: CalendarItem) -> str:
    rank = item.get("rank")
    if isinstance(rank, int):
        return f"#{rank}"
    if isinstance(rank, str) and rank:
        return f"#{rank.lstrip('#')}"
    return ""


def reorder_days(calendar_data: list[CalendarDay]) -> list[CalendarDay]:
    """
    重新排序天数,使今天排在第一位
    """
    today_id = datetime.datetime.now().isoweekday()

    today_index = 0
    for i, day in enumerate(calendar_data):
        weekday: CalendarWeekday = day.get("weekday", {})
        if weekday.get("id") == today_id:
            today_index = i
            day["is_today"] = True
            break

    reordered = calendar_data[today_index:] + calendar_data[:today_index]
    return reordered


async def _load_calendar_covers(
    calendar_data: list[CalendarDay],
    session: aiohttp.ClientSession | None,
    proxy_url: str | None = None,
) -> dict[tuple[int, int], Image.Image | None]:
    semaphore = asyncio.Semaphore(_CALENDAR_COVER_CONCURRENCY)

    async def load_one(
        day_index: int,
        item_index: int,
        item: CalendarItem,
    ) -> tuple[tuple[int, int], Image.Image | None]:
        async with semaphore:
            image = await load_image_source(
                _item_image_source(item),
                session,
                proxy_url=proxy_url,
            )
        return (day_index, item_index), image

    tasks = []
    for day_index, day in enumerate(calendar_data):
        items = day.get("items", [])
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if isinstance(item, dict):
                tasks.append(load_one(day_index, item_index, item))

    if not tasks:
        return {}
    return dict(await asyncio.gather(*tasks))


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: FontType,
    fill: tuple[int, int, int, int],
) -> None:
    text_width, text_height = measure_text(draw, text, font)
    draw.text(
        (
            box[0] + (box[2] - box[0] - text_width) // 2,
            box[1] + (box[3] - box[1] - text_height) // 2 - 2,
        ),
        text,
        font=font,
        fill=fill,
    )


def _draw_calendar_card_image(
    calendar_data: list[CalendarDay],
    cover_images: dict[tuple[int, int], Image.Image | None],
) -> str:
    max_item_count = 0
    for day in calendar_data[:7]:
        items = day.get("items", [])
        if isinstance(items, list):
            max_item_count = max(
                max_item_count,
                sum(1 for item in items if isinstance(item, dict)),
            )

    width = 2892
    height = max(2124, 426 + max_item_count * 740)
    canvas = Image.new("RGBA", (width, height), (240, 242, 245, 255))
    draw = ImageDraw.Draw(canvas)

    title_font = get_font(96, bold=True)
    subtitle_font = get_font(48, bold=True)
    day_font = get_font(54, bold=True)
    day_en_font = get_font(36)
    item_font = get_font(42, bold=True)
    meta_font = get_font(36, bold=True)
    rank_font = get_font(33)
    empty_font = get_font(42)

    draw.rounded_rectangle((30, 21, 54, 116), radius=12, fill=(251, 140, 0, 255))
    draw.text((90, 18), "每日放送表", font=title_font, fill=(26, 26, 26, 255))
    draw.text(
        (606, 51), "Bangumi Calendar", font=subtitle_font, fill=(133, 144, 166, 255)
    )

    grid_y = 204
    gap = 48
    column_width = 372
    header_height = 243
    item_height = 729
    item_gap = 3
    content_height = (
        header_height
        + max_item_count * item_height
        + max(0, max_item_count - 1) * item_gap
        + 3
    )
    column_height = max(1800, content_height)

    for index in range(7):
        if index < len(calendar_data):
            day = calendar_data[index]
        else:
            day = cast(CalendarDay, {"weekday": {"id": index + 1}, "items": []})
        weekday = day.get("weekday", {})
        weekday_name = _stringify_value(weekday.get("cn")) or _stringify_value(
            weekday.get("ja")
        )
        weekday_en = _stringify_value(weekday.get("en")).upper()
        if not weekday_name:
            weekday_id = weekday.get("id")
            weekday_name = f"周{weekday_id}" if weekday_id else "未知"
        if not weekday_en:
            weekday_en = str(weekday_name)[-1:]

        x = index * (column_width + gap)
        y = grid_y
        col_w = column_width
        col_h = column_height
        is_today = day.get("is_today") is True
        if is_today:
            x = max(0, x - 4)
            y = grid_y - 24
            col_w = column_width + 8
            col_h = column_height + 24

        add_shadow(
            canvas,
            (x, y, x + col_w, y + col_h),
            radius=48,
            blur=42 if is_today else 18,
            offset=(0, 18),
            shadow_color=(0, 0, 0, 30 if is_today else 10),
        )
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (x, y, x + col_w, y + col_h),
            radius=48,
            fill=(255, 255, 255, 255),
            outline=(251, 140, 0, 255) if is_today else (232, 232, 232, 255),
            width=6 if is_today else 1,
        )
        header_box = (x + 3, y + 3, x + col_w - 3, y + header_height)
        draw.rounded_rectangle(
            header_box,
            radius=44,
            fill=(251, 140, 0, 255) if is_today else (255, 255, 255, 255),
        )
        draw.rectangle(
            (
                header_box[0],
                y + header_height - 45,
                header_box[2],
                y + header_height + 3,
            ),
            fill=(251, 140, 0, 255) if is_today else (255, 255, 255, 255),
        )
        text_fill = (255, 255, 255, 255) if is_today else (0, 0, 0, 255)
        en_fill = (255, 232, 204, 255) if is_today else (74, 74, 74, 255)
        _draw_centered_text(
            draw, (x, y + 49, x + col_w, y + 112), weekday_name, day_font, text_fill
        )
        _draw_centered_text(
            draw, (x, y + 122, x + col_w, y + 172), weekday_en, day_en_font, en_fill
        )
        draw.line(
            (x, y + header_height, x + col_w, y + header_height),
            fill=(251, 140, 0, 255) if is_today else (234, 234, 234, 255),
            width=4 if is_today else 2,
        )

        items = day.get("items", [])
        if not isinstance(items, list):
            items = []
        if not items:
            empty_top = y + header_height
            empty_bottom = min(y + header_height + 366, y + col_h)
            draw.rectangle(
                (x + 3, empty_top, x + col_w - 3, empty_bottom),
                fill=(234, 234, 234, 255),
            )
            _draw_centered_text(
                draw,
                (x + 30, empty_top + 66, x + col_w - 30, empty_bottom - 66),
                "今日无更新\n内容",
                empty_font,
                (153, 153, 153, 255),
            )
            continue

        item_y = y + header_height
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_h = item_height
            draw.rectangle(
                (x + 3, item_y, x + col_w - 3, item_y + item_h),
                fill=(255, 255, 255, 255),
            )
            cover_x = x + 39
            cover_y = item_y + 36
            cover_w = col_w - 78
            cover_h = 441
            cover = cover_images.get((index, item_index))
            if cover is None:
                cover = create_placeholder_image(
                    (cover_w, cover_h), _item_title(item), (251, 140, 0)
                )
            cover_card = fit_cover(cover, (cover_w, cover_h), 24)
            add_shadow(
                canvas,
                (cover_x, cover_y, cover_x + cover_w, cover_y + cover_h),
                radius=24,
                blur=24,
                offset=(0, 9),
                shadow_color=(0, 0, 0, 18),
            )
            canvas.alpha_composite(cover_card, (cover_x, cover_y))
            draw = ImageDraw.Draw(canvas)
            title = _item_title(item)
            draw_text_block(
                draw,
                (
                    x + 39,
                    cover_y + cover_h + 24,
                    x + col_w - 39,
                    cover_y + cover_h + 144,
                ),
                title,
                item_font,
                (26, 26, 26, 255),
                max_lines=2,
                line_spacing=9,
            )
            score = _item_score(item)
            rank = _item_rank(item)
            meta_y = item_y + item_h - 93
            if score:
                draw.text(
                    (x + 36, meta_y),
                    f"★ {score}",
                    font=meta_font,
                    fill=(251, 140, 0, 255),
                )
            if rank:
                rank_w = measure_text(draw, rank, rank_font)[0] + 36
                rank_box = (
                    x + col_w - 39 - rank_w,
                    meta_y - 3,
                    x + col_w - 39,
                    meta_y + 54,
                )
                draw.rounded_rectangle(rank_box, radius=12, fill=(245, 245, 245, 255))
                draw.text(
                    (rank_box[0] + 18, meta_y + 6),
                    rank,
                    font=rank_font,
                    fill=(153, 153, 153, 255),
                )
            item_y += item_h + item_gap
            draw.line(
                (x + 3, item_y - item_gap, x + col_w - 3, item_y - item_gap),
                fill=(234, 234, 234, 255),
                width=2,
            )

    return image_to_base64(canvas)


class CalendarRenderer(BaseRenderer):
    async def _render_calendar_pillow(self, calendar_data: list[CalendarDay]) -> str:
        cover_images = await _load_calendar_covers(
            calendar_data, self._session, proxy_url=self.proxy_url
        )
        return await asyncio.to_thread(
            _draw_calendar_card_image,
            calendar_data,
            cover_images,
        )

    async def render_calendar(
        self,
        calendar_data: list[CalendarDay],
        rpc_url: str | None = None,
        headless: bool = True,
        max_retries: int = 3,
    ) -> str | None:
        """
        渲染放送表图片并返回 Base64 字符串
        """
        try:
            reordered_days = reorder_days(calendar_data)
        except (ValueError, TypeError, RuntimeError) as e:
            logger.error(f"[-] 处理日历数据失败: {e}")
            return None

        if self.render_mode == "pillow":
            return await self._render_calendar_pillow(reordered_days)

        return await self.render(
            template_path="calendar/calendar.html",
            render_data=cast(RenderData, {"days": reordered_days}),
            selector=".container",
            sub_dir="calendar",
            rpc_url=rpc_url,
            headless=headless,
            max_retries=max_retries,
            timeout=30000,
            wait_time=2,
            pillow_fallback=lambda: self._render_calendar_pillow(reordered_days),
        )
