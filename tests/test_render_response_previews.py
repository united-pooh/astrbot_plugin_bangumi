from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from astrbot_plugin_bangumi.src.render.response_renderer import ResponseRenderer

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_response_previews.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "render_response_previews_test_module", SCRIPT_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_render_previews_requires_rpc_url(tmp_path: Path) -> None:
    script = load_script()

    with pytest.raises(ValueError, match="--rpc-url"):
        await script.render_previews(
            output_dir=tmp_path,
            query="冰之城墙",
            render_mode="rpc",
            user_agent="test-agent",
        )


@pytest.mark.asyncio
async def test_render_previews_passes_rpc_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()

    async def fake_text_set(query: str, user_agent: str) -> object:
        return script.PreviewTextSet(
            source="test-real-sample", text=f"{query}:{user_agent}"
        )

    seen_rpc_urls: list[str | None] = []

    async def fake_render_response_text(
        self: ResponseRenderer,
        text: str,
        *,
        variant: str | None = None,
        title: str = "Bangumi Response",
        rpc_url: str | None = None,
        **kwargs: object,
    ) -> str:
        del self, text, title, kwargs
        seen_rpc_urls.append(rpc_url)
        return base64.b64encode(str(variant).encode()).decode()

    monkeypatch.setattr(script, "_build_text_set", fake_text_set)
    monkeypatch.setattr(
        ResponseRenderer, "render_response_text", fake_render_response_text
    )

    report = await script.render_previews(
        output_dir=tmp_path,
        query="冰之城墙",
        render_mode="rpc",
        user_agent="test-agent",
        rpc_url="http://127.0.0.1:3000",
    )

    assert seen_rpc_urls == ["http://127.0.0.1:3000"] * 3
    assert report["version"] == "v1.5.1"
    assert report["rpc_url_configured"] is True
    assert (tmp_path / "preview-report-rpc.json").exists()
