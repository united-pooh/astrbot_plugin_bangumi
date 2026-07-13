"""
bgmlist.com API 客户端

提供获取热门番剧广播时间数据的功能。
数据来源: https://bgmlist.com (开源项目 wxt2005/bangumi-list-v3)
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping

import aiohttp
from astrbot.api import logger

BGM_LIST_API = "https://bgmlist.com/api/v1/bangumi/onair"


def _parse_broadcast_time(begin_iso: str) -> tuple[str, int] | None:
    """
    ponytail: 从 bgmlist 的 begin 字段解析出 CST (UTC+8) 的播出时间 HH:MM + ISO weekday.

    Returns:
        (HH:MM, weekday 1-7) 元组, 解析失败返回 None.
    """
    if not begin_iso:
        return None
    try:
        dt_str = begin_iso
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        cst_dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
        return cst_dt.strftime("%H:%M"), cst_dt.isoweekday()
    except (ValueError, TypeError) as e:
        logger.warning(f"解析广播时间失败: {begin_iso} - {e}")
        return None


async def fetch_onair_data(
    session: aiohttp.ClientSession | None = None,
    proxy_url: str | None = None,
) -> dict[str, tuple[str, int]] | None:
    """
    ponytail: 从 bgmlist API 获取放送中番剧的播出时间 + weekday.

    Returns:
        {bangumi_subject_id: (HH:MM, iso_weekday)} 映射, 如 {"377130": ("22:00", 3)}.
        失败返回 None.
    """
    _session: aiohttp.ClientSession | None = session
    _close = session is None

    try:
        if _session is None:
            _session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=10),
                headers={
                    "User-Agent": "AstrBot-BangumiPlugin/1.0",
                    "Accept": "application/json",
                },
            )

        assert _session is not None  # mypy: narrow Optional after None check

        async with _session.get(
            BGM_LIST_API,
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=15, connect=10),
            headers={
                "User-Agent": "AstrBot-BangumiPlugin/1.0",
                "Accept": "application/json",
            },
        ) as resp:
            if resp.status != 200:
                logger.warning(f"bgmlist API 返回 {resp.status}")
                return None
            data = await resp.json()

        items = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            logger.warning("bgmlist API 返回格式异常: data 非列表")
            return None

        result: dict[str, tuple[str, int]] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue

            sites = item.get("sites", [])
            bangumi_id: str | None = None
            if isinstance(sites, list):
                for site in sites:
                    if isinstance(site, Mapping) and site.get("site") == "bangumi":
                        bangumi_id = str(site.get("id", ""))
                        break

            if not bangumi_id:
                continue

            begin_raw = item.get("begin")
            parsed = _parse_broadcast_time(str(begin_raw) if begin_raw else "")
            if parsed:
                result[bangumi_id] = parsed

        logger.info(f"从 bgmlist 获取到 {len(result)} 条放送时间数据")
        return result

    except (aiohttp.ClientError, OSError, ValueError) as e:
        logger.warning(f"获取 bgmlist 数据失败: {e}")
        return None
    finally:
        if _close and _session is not None:
            await _session.close()
