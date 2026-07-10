from pathlib import Path

import yaml
from astrbot.api import AstrBotConfig, logger

from ..domain import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EpisodeCardVariant,
    is_episode_card_variant,
)
from ..render.render_mode import DEFAULT_RENDER_MODE, RenderMode, normalize_render_mode


class ConfigManager:
    def __init__(self, config: AstrBotConfig) -> None:
        self.config = config

    def _get_str(self, key: str, default: str) -> str:
        value = self.config.get(key, default)
        return value if isinstance(value, str) else default

    def _get_int(self, key: str, default: int) -> int:
        value = self.config.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    def _get_bool(self, key: str, default: bool = False) -> bool:
        value = self.config.get(key, default)
        return value if isinstance(value, bool) else default

    def get_access_token(self) -> str:
        """
        获取bangumi的access_token
        """
        return self._get_str("access_token", "")

    def get_user_agent(self) -> str:
        user_agent = self._get_str("user_agent", "")
        if user_agent == "":
            with open(
                f"{Path(__file__).resolve().parent.parent.parent}/metadata.yaml",
                encoding="utf-8",
            ) as f:
                metadata = yaml.safe_load(f)
                version = "unknown"
                if isinstance(metadata, dict):
                    version_value = metadata.get("version")
                    if version_value is not None:
                        version = str(version_value)
                user_agent = (
                    f"AstrBot-Bangumi-Plugin/{version} "
                    "(https://github.com/united-pooh/astrbot_plugin_bangumi)"
                )
        return user_agent

    def get_max_fuzzy_results(self) -> int:
        return self._get_int("max_fuzzy_results", 5)

    def get_proxy_http(self) -> str:
        return self._get_str("proxy_http", "")

    def get_port(self) -> str:
        return self._get_str("port", "")

    def get_max_retries(self) -> int:
        return self._get_int("max_retries", 3)

    def get_render_server_url(self) -> str:
        return self._get_str("render_server_url", "https://api.unitedpooh.top/rpc")

    def get_render_mode(self) -> RenderMode:
        return normalize_render_mode(
            self.config.get("render_mode", DEFAULT_RENDER_MODE)
        )

    def get_episode_card_template(self) -> EpisodeCardVariant:
        value = self.config.get("episode_card_template", DEFAULT_EPISODE_CARD_VARIANT)
        if is_episode_card_variant(value):
            return value
        return DEFAULT_EPISODE_CARD_VARIANT

    def get_auto_translate_episode_summary(self) -> bool:
        return self._get_bool("auto_translate_episode_summary", False)

    def get_broadcast_time_30h(self) -> bool:
        return self._get_bool("broadcast_time_30h", True)

    def get_auto_translate_subject_summary(self) -> bool:
        return self._get_bool("auto_translate_subject_summary", False)

    def set_episode_card_template(self, template: EpisodeCardVariant) -> None:
        self.config["episode_card_template"] = template

    def save_config(self) -> None:
        """
        保存bgm插件配置到配置文件中, 并重新加载配置
        """
        try:
            self.config.save_config()
            logger.info("配置已保存")
        except (AttributeError, OSError, RuntimeError, ValueError, TypeError) as e:
            logger.error(f"保存bgm插件配置失败: {e}")
