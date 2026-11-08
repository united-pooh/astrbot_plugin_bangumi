from pathlib import Path

import pytest
from astrbot_plugin_bangumi.src.utils.env_manager import EnvManager


def test_generate_env_from_schema_writes_defaults(tmp_path: Path) -> None:
    schema = tmp_path / "_conf_schema.json"
    env = tmp_path / ".env"
    schema.write_text(
        '{"access_token":{"default":""},"max_retries":{"default":3},'
        '"render_mode":{"default":"html"}}',
        encoding="utf-8",
    )

    EnvManager.generate_env_from_schema(schema, env, render_mode_default="pillow")

    assert env.read_text(encoding="utf-8").splitlines() == [
        "# Generated from _conf_schema.json for local testing.",
        "# Fill access_token before running integration tests that hit Bangumi APIs.",
        "",
        "access_token=",
        "max_retries=3",
        "render_mode=pillow",
    ]


def test_generate_env_from_schema_preserves_existing_values(tmp_path: Path) -> None:
    schema = tmp_path / "_conf_schema.json"
    env = tmp_path / ".env"
    schema.write_text(
        '{"access_token":{"default":""},"render_mode":{"default":"html"}}',
        encoding="utf-8",
    )
    env.write_text("access_token=kept\nrender_mode=html\n", encoding="utf-8")

    EnvManager.generate_env_from_schema(schema, env, render_mode_default="pillow")

    content = env.read_text(encoding="utf-8")
    assert "access_token=kept" in content
    assert "render_mode=html" in content


def test_start_font_download_uses_background_font_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str | None]] = []

    def fake_start_font_download(
        font_dir: str | Path, proxy_url: str | None = None
    ) -> None:
        calls.append((Path(font_dir), proxy_url))

    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.pillow_utils.start_font_download",
        fake_start_font_download,
    )

    manager = EnvManager(str(tmp_path))
    manager.start_font_download(proxy_url="http://proxy.local:7890")

    assert calls == [(tmp_path / "fonts", "http://proxy.local:7890")]
