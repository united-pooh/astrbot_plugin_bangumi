from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import aiohttp
from PIL import Image, ImageChops

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = PROJECT_ROOT.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "rendered_images/subject-card-v1.5.5"
DEFAULT_QUERY = "葬送的芙莉莲"
DEFAULT_USER_AGENT = (
    "AstrBot-Bangumi-Plugin/subject-preview "
    "(https://github.com/united-pooh/astrbot_plugin_bangumi)"
)

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

if TYPE_CHECKING:
    from astrbot_plugin_bangumi.src.domain import RenderData
    from astrbot_plugin_bangumi.src.render.render_mode import RenderMode


@dataclass(frozen=True)
class PreviewSubject:
    source: str
    subject_id: str
    data: RenderData


def _fixture_subject() -> PreviewSubject:
    return PreviewSubject(
        source="cached-real-sample",
        subject_id="535669",
        data=cast(
            "RenderData",
            {
                "id": 535669,
                "name": "氷の城壁",
                "name_cn": "冰之城墙",
                "date": "2025-01-10",
                "type": 1,
                "platform": "书籍",
                "summary": (
                    "不擅长与人交往,在自己与别人之间筑墙的冰川小雪。"
                    "除了青梅竹马的安昙美姬外,她从不与他人交往,只是静静地度过每一天。"
                    "然而某天,不知为何主动靠近的男生雨宫凑闯入她的视野。"
                ),
                "tags": [
                    {"name": "漫画"},
                    {"name": "校园"},
                    {"name": "恋爱"},
                    {"name": "青春"},
                ],
                "rating": {
                    "rank": 342,
                    "total": 1812,
                    "score": 7.8,
                    "count": {
                        "1": 4,
                        "2": 5,
                        "3": 12,
                        "4": 31,
                        "5": 82,
                        "6": 244,
                        "7": 650,
                        "8": 578,
                        "9": 155,
                        "10": 51,
                    },
                },
                "collection": {"doing": 3241},
            },
        ),
    )


async def _load_real_subject(
    query: str,
    subject_id: str | None,
    user_agent: str,
) -> PreviewSubject:
    from astrbot_plugin_bangumi.src.api import BangumiService
    from astrbot_plugin_bangumi.src.domain import SubjectType

    async with aiohttp.ClientSession() as session:
        service = BangumiService(
            access_token="",
            user_agent=user_agent,
            session=session,
            max_retries=2,
        )
        resolved_subject_id = subject_id
        if not resolved_subject_id:
            search_res = await service.search_subjects(
                keyword=query,
                limit=1,
                subject_type=[SubjectType.ANIME.value],
            )
            items = search_res.get("data", [])
            if not items:
                raise RuntimeError("Bangumi API 未返回搜索结果")
            resolved_subject_id = str(items[0].get("id"))

        details = await service.get_subject_details(resolved_subject_id)
        if not details:
            raise RuntimeError(f"Bangumi API 未返回条目详情: {resolved_subject_id}")

        try:
            episodes = await service.get_subject_episodes(int(resolved_subject_id))
        except (ValueError, TypeError, RuntimeError):
            episodes = {}
        if isinstance(episodes, dict) and isinstance(episodes.get("data"), list):
            details["episodes"] = episodes["data"]

        return PreviewSubject(
            source="bangumi-api-live",
            subject_id=resolved_subject_id,
            data=cast("RenderData", details),
        )


async def _build_subject(
    query: str,
    subject_id: str | None,
    user_agent: str,
) -> PreviewSubject:
    try:
        return await _load_real_subject(query, subject_id, user_agent)
    except Exception as exc:
        fixture = _fixture_subject()
        return PreviewSubject(
            source=f"{fixture.source}; live Bangumi API failed: {exc}",
            subject_id=fixture.subject_id,
            data=fixture.data,
        )


def _pixel_alignment_ratio(candidate: Path, baseline: Path) -> float:
    candidate_image = Image.open(candidate).convert("RGBA")
    baseline_image = Image.open(baseline).convert("RGBA")
    if candidate_image.size != baseline_image.size:
        candidate_image = candidate_image.resize(
            baseline_image.size, Image.Resampling.BOX
        )
    diff = ImageChops.difference(candidate_image, baseline_image).convert("L")
    flat_data = getattr(diff, "get_flattened_data", diff.getdata)
    changed = sum(1 for value in flat_data() if value > 2)
    return changed / (diff.width * diff.height)


def _image_size(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        width, height = image.size
    return {
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6),
    }


async def render_previews(
    output_dir: Path,
    query: str,
    render_mode: RenderMode,
    user_agent: str,
    *,
    subject_id: str | None = None,
    rpc_url: str | None = None,
    verify_pixel_alignment: bool = False,
) -> dict[str, object]:
    from astrbot_plugin_bangumi.src.domain import EPISODE_CARD_VARIANTS
    from astrbot_plugin_bangumi.src.render import SubjectRenderer

    if render_mode == "rpc" and not rpc_url:
        raise ValueError(
            "--render-mode rpc 需要同时提供 --rpc-url,避免误把 Pillow fallback 当作 RPC 预览"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    subject = await _build_subject(query, subject_id, user_agent)
    renderer = SubjectRenderer(render_mode=render_mode)
    pillow_renderer = SubjectRenderer(render_mode="pillow")

    previews: list[dict[str, object]] = []
    for variant in EPISODE_CARD_VARIANTS:
        payload = await renderer.render_subject_card(
            subject.data,
            rpc_url=rpc_url,
            variant=variant,
        )
        if not payload:
            raise RuntimeError(f"{variant} 未生成搜索结果预览图")

        target = output_dir / f"{variant}-{render_mode}.png"
        target.write_bytes(base64.b64decode(payload))
        preview: dict[str, object] = {
            "variant": variant,
            "path": str(target),
            **_image_size(target),
        }

        if verify_pixel_alignment:
            pillow_payload = await pillow_renderer.render_subject_card(
                subject.data,
                variant=variant,
            )
            if pillow_payload:
                baseline = output_dir / f"{variant}-pillow-baseline.png"
                baseline.write_bytes(base64.b64decode(pillow_payload))
                preview["pixel_changed_ratio"] = _pixel_alignment_ratio(
                    target, baseline
                )
                preview["pixel_baseline"] = str(baseline)

        previews.append(preview)

    report = {
        "version": "v1.5.5",
        "render_mode": render_mode,
        "rpc_url_configured": bool(rpc_url),
        "query": query,
        "subject_id": subject.subject_id,
        "data_source": subject.source,
        "self_review_required": True,
        "previews": previews,
    }
    (output_dir / f"preview-report-{render_mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render v1.5.5 subject search card previews with real Bangumi data."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--subject-id")
    parser.add_argument("--render-mode", default="pillow")
    parser.add_argument("--rpc-url")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--verify-pixel-alignment", action="store_true")
    return parser.parse_args()


async def main() -> None:
    from astrbot_plugin_bangumi.src.render.render_mode import normalize_render_mode

    args = parse_args()
    render_mode = normalize_render_mode(args.render_mode)
    report = await render_previews(
        output_dir=args.output,
        query=args.query,
        subject_id=args.subject_id,
        render_mode=render_mode,
        user_agent=args.user_agent,
        rpc_url=args.rpc_url,
        verify_pixel_alignment=args.verify_pixel_alignment,
    )
    for item in report["previews"]:
        if isinstance(item, dict):
            print(f"generated {item['variant']}: {item['path']}")
    print(f"data source: {report['data_source']}")


if __name__ == "__main__":
    asyncio.run(main())
