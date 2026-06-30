from __future__ import annotations

import base64
import importlib.util
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from astrbot_plugin_bangumi.src.render import SubjectRenderer

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_subject_variants.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "render_subject_variants_test_module", SCRIPT_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fake_png_base64(width: int = 24, height: int = 12) -> str:
    buffer = BytesIO()
    Image.new("RGBA", (width, height), (255, 255, 255, 255)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.asyncio
async def test_render_previews_requires_rpc_url(tmp_path: Path) -> None:
    script = load_script()

    with pytest.raises(ValueError, match="--rpc-url"):
        await script.render_previews(
            output_dir=tmp_path,
            query="葬送的芙莉莲",
            render_mode="rpc",
            user_agent="test-agent",
        )


@pytest.mark.asyncio
async def test_render_previews_outputs_all_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()

    async def fake_subject(
        query: str, subject_id: str | None, user_agent: str
    ) -> object:
        return script.PreviewSubject(
            source=f"test-real-sample:{query}:{user_agent}",
            subject_id=subject_id or "123",
            data={"id": 123, "name_cn": "测试条目"},
        )

    async def fake_render_subject_card(
        self: SubjectRenderer,
        data: dict[str, object],
        *,
        variant: str | None = None,
        rpc_url: str | None = None,
        **kwargs: object,
    ) -> str:
        del self, data, variant, rpc_url, kwargs
        return fake_png_base64()

    monkeypatch.setattr(script, "_build_subject", fake_subject)
    monkeypatch.setattr(
        SubjectRenderer, "render_subject_card", fake_render_subject_card
    )

    report = await script.render_previews(
        output_dir=tmp_path,
        query="葬送的芙莉莲",
        subject_id="123",
        render_mode="pillow",
        user_agent="test-agent",
    )

    assert report["version"] == "v1.5.0"
    assert report["subject_id"] == "123"
    assert [item["variant"] for item in report["previews"]] == [
        "pastel_lightbox",
        "editorial_digest",
        "cinematic_poster",
    ]
    assert all(item["width"] == 24 for item in report["previews"])
    assert all(item["height"] == 12 for item in report["previews"])
    assert all(item["aspect_ratio"] == 2 for item in report["previews"])
    assert (tmp_path / "preview-report-pillow.json").exists()
