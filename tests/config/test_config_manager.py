import json
from pathlib import Path
from unittest.mock import MagicMock

from astrbot_plugin_bangumi.src.config import ConfigManager


def _manager(values: dict[str, object]) -> ConfigManager:
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return ConfigManager(config)


def test_get_int_accepts_int_and_numeric_string() -> None:
    assert _manager({"max_retries": 5}).get_max_retries() == 5
    assert _manager({"max_retries": "6"}).get_max_retries() == 6


def test_get_int_rejects_bool_and_invalid_string() -> None:
    assert _manager({"max_retries": True}).get_max_retries() == 3
    assert _manager({"max_retries": "bad"}).get_max_retries() == 3


def test_proxy_defaults_do_not_imply_local_proxy() -> None:
    manager = _manager({})

    assert manager.get_proxy_http() == ""
    assert manager.get_port() == ""


def test_get_str_rejects_non_string_proxy_values() -> None:
    manager = _manager({"proxy_http": 123, "port": 7891})

    assert manager.get_proxy_http() == ""
    assert manager.get_port() == ""


def test_get_episode_card_template_reads_valid_config() -> None:
    manager = _manager({"episode_card_template": "editorial_digest"})

    assert manager.get_episode_card_template() == "editorial_digest"


def test_get_render_mode_defaults_to_pillow() -> None:
    assert _manager({}).get_render_mode() == "pillow"


def test_get_render_mode_accepts_new_modes_and_legacy_html() -> None:
    assert _manager({"render_mode": "playwright"}).get_render_mode() == "playwright"
    assert _manager({"render_mode": "RPC"}).get_render_mode() == "rpc"
    assert _manager({"render_mode": "html"}).get_render_mode() == "playwright"


def test_get_episode_card_template_falls_back_to_default() -> None:
    assert _manager({}).get_episode_card_template() == "pastel_lightbox"
    assert (
        _manager(
            {"episode_card_template": "risograph_zine"}
        ).get_episode_card_template()
        == "pastel_lightbox"
    )


def test_get_auto_translate_episode_summary_reads_bool_config() -> None:
    assert _manager(
        {"auto_translate_episode_summary": True}
    ).get_auto_translate_episode_summary()
    assert (
        _manager(
            {"auto_translate_episode_summary": False}
        ).get_auto_translate_episode_summary()
        is False
    )


def test_get_auto_translate_episode_summary_defaults_false_for_missing_or_invalid() -> (
    None
):
    assert _manager({}).get_auto_translate_episode_summary() is False
    assert (
        _manager(
            {"auto_translate_episode_summary": "true"}
        ).get_auto_translate_episode_summary()
        is False
    )
    assert (
        _manager(
            {"auto_translate_episode_summary": 1}
        ).get_auto_translate_episode_summary()
        is False
    )


def test_auto_translate_episode_summary_schema_defaults_false() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["auto_translate_episode_summary"] == {
        "description": "自动翻译单集简介",
        "type": "bool",
        "hint": "订阅更新渲染单集卡片前，使用 AstrBot 默认聊天模型将非空单集简介翻译为中文；失败时保留原文",
        "default": False,
    }


def test_set_episode_card_template_updates_config_value() -> None:
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: default
    manager = ConfigManager(config)

    manager.set_episode_card_template("pastel_lightbox")

    config.__setitem__.assert_called_once_with(
        "episode_card_template", "pastel_lightbox"
    )


def test_user_agent_uses_config_value() -> None:
    assert _manager({"user_agent": "custom"}).get_user_agent() == "custom"


def test_save_config_swallows_supported_errors() -> None:
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: default
    config.save_config.side_effect = OSError("readonly")
    manager = ConfigManager(config)

    manager.save_config()

    config.save_config.assert_called_once()
