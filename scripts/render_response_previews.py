from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = PROJECT_ROOT.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "rendered_images/response-card-v1.5.1"
DEFAULT_QUERY = "冰之城墙"
DEFAULT_USER_AGENT = (
    "AstrBot-Bangumi-Plugin/response-preview "
    "(https://github.com/united-pooh/astrbot_plugin_bangumi)"
)

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

if TYPE_CHECKING:
    from astrbot_plugin_bangumi.src.render.render_mode import RenderMode


@dataclass(frozen=True)
class PreviewTextSet:
    source: str
    text: str


def _fixture_text_set() -> PreviewTextSet:
    return PreviewTextSet(
        source="cached-real-sample",
        text=(
            "⚠️ 匹配到多个候选,请使用 `/追番 序号` 确认:\n"
            "1. 冰之城墙 (ID: 535669)\n"
            "2. 皮卡丘冰之大冒险 (ID: 90614)\n"
            "3. 巨神与冰华之城 (ID: 303553)\n"
            "4. 冰菓 应该持有之物 (ID: 111762)\n"
            "5. 新网球王子 冰帝 vs 立海 未来之战 (ID: 324788)\n"
            "5分钟内有效;若发送新的斜杠命令或重新输入 `追番` 将自动取消本次确认"
        ),
    )


async def _load_real_text_set(query: str, user_agent: str) -> PreviewTextSet:
    from astrbot_plugin_bangumi.src.api import BangumiService
    from astrbot_plugin_bangumi.src.domain import SubjectType

    async with aiohttp.ClientSession() as session:
        service = BangumiService(
            access_token="",
            user_agent=user_agent,
            session=session,
            max_retries=2,
        )
        search_res = await service.search_subjects(
            keyword=query,
            limit=6,
            subject_type=[SubjectType.ANIME.value],
        )
        items = search_res.get("data", [])
        if not items:
            raise RuntimeError("Bangumi API 未返回候选条目")

        lines = [f"⚠️ Bangumi 真实搜索候选: {query}"]
        for index, item in enumerate(items[:6], start=1):
            subject_id = item.get("id")
            name = item.get("name_cn") or item.get("name") or f"ID:{subject_id}"
            lines.append(f"{index}. {name} (ID: {subject_id})")

        first_id = items[0].get("id")
        if first_id is not None:
            details = await service.get_subject_details(str(first_id))
            summary = str(details.get("summary", "")).strip()
            if summary:
                lines.append("")
                lines.append(summary[:220])
        lines.append("5分钟内有效;请回复序号继续。")
        return PreviewTextSet(source="bangumi-api-live", text="\n".join(lines))


async def _build_text_set(query: str, user_agent: str) -> PreviewTextSet:
    try:
        return await _load_real_text_set(query, user_agent)
    except Exception as exc:
        fallback = _fixture_text_set()
        return PreviewTextSet(
            source=f"{fallback.source}; live Bangumi API failed: {exc}",
            text=fallback.text,
        )


async def render_previews(
    output_dir: Path,
    query: str,
    render_mode: RenderMode,
    user_agent: str,
    rpc_url: str | None = None,
) -> dict[str, object]:
    from astrbot_plugin_bangumi.src.domain import EPISODE_CARD_VARIANTS
    from astrbot_plugin_bangumi.src.render.response_renderer import ResponseRenderer

    if render_mode == "rpc" and not rpc_url:
        raise ValueError(
            "--render-mode rpc 需要同时提供 --rpc-url,避免误把 Pillow fallback 当作 RPC 预览"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    text_set = await _build_text_set(query, user_agent)
    renderer = ResponseRenderer(render_mode=render_mode)

    previews: list[dict[str, str]] = []
    for variant in EPISODE_CARD_VARIANTS:
        payload = await renderer.render_response_text(
            text_set.text,
            variant=variant,
            title="Bangumi Response",
            rpc_url=rpc_url,
        )
        if not payload:
            raise RuntimeError(f"{variant} 未生成预览图")
        target = output_dir / f"{variant}-{render_mode}.png"
        target.write_bytes(base64.b64decode(payload))
        previews.append({"variant": variant, "path": str(target)})

    report = {
        "version": "v1.5.1",
        "render_mode": render_mode,
        "rpc_url_configured": bool(rpc_url),
        "query": query,
        "data_source": text_set.source,
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
        description="Render v1.5.1 response card previews with real Bangumi data."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--render-mode", default="pillow")
    parser.add_argument("--rpc-url")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser.parse_args()


async def main() -> None:
    from astrbot_plugin_bangumi.src.render.render_mode import normalize_render_mode

    args = parse_args()
    render_mode = normalize_render_mode(args.render_mode)
    report = await render_previews(
        output_dir=args.output,
        query=args.query,
        render_mode=render_mode,
        user_agent=args.user_agent,
        rpc_url=args.rpc_url,
    )
    for item in report["previews"]:
        if isinstance(item, dict):
            print(f"generated {item['variant']}: {item['path']}")
    print(f"data source: {report['data_source']}")


if __name__ == "__main__":
    asyncio.run(main())
