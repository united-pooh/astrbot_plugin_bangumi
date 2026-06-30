import ast
import json
import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASTROBOT_IMPORT_RULE = "AstrBot package-loading import rule"
TOP_LEVEL_SRC_PREFIX = "src" + "."


def _python_files_under(*relative_roots: str) -> list[Path]:
    return sorted(
        file_path
        for relative_root in relative_roots
        for file_path in (PROJECT_ROOT / relative_root).rglob("*.py")
    )


def _format_location(file_path: Path, line_number: int, detail: str) -> str:
    return f"{file_path.relative_to(PROJECT_ROOT)}:{line_number}: {detail}"


def _is_top_level_src_module(module_name: str | None) -> bool:
    return module_name == "src" or bool(
        module_name and module_name.startswith(TOP_LEVEL_SRC_PREFIX)
    )


def _find_direct_internal_imports(file_path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_top_level_src_module(alias.name):
                    violations.append(
                        _format_location(
                            file_path,
                            node.lineno,
                            f"uses top-level {alias.name} import",
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and _is_top_level_src_module(node.module):
            violations.append(
                _format_location(
                    file_path,
                    node.lineno,
                    f"uses top-level {node.module} import",
                )
            )
    return violations


def _is_monkeypatch_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "monkeypatch"
    )


def _find_top_level_monkeypatch_targets(file_path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_monkeypatch_call(node):
            continue

        string_nodes = [
            value
            for value in [*node.args, *(keyword.value for keyword in node.keywords)]
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        for value in string_nodes:
            if value.value.startswith(TOP_LEVEL_SRC_PREFIX):
                violations.append(
                    _format_location(
                        file_path,
                        value.lineno,
                        "uses top-level src monkeypatch target",
                    )
                )
    return violations


def _string_literals_from_node(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Set | ast.List | ast.Tuple):
        return {
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    return set()


def _command_names_from_decorator(decorator: ast.expr) -> set[str]:
    if not (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "command"
    ):
        return set()

    commands: set[str] = set()
    if decorator.args:
        commands.update(_string_literals_from_node(decorator.args[0]))
    for keyword in decorator.keywords:
        if keyword.arg == "alias":
            commands.update(_string_literals_from_node(keyword.value))
    return commands


def _commands_for_handler(main_py: str, handler_name: str) -> set[str]:
    tree = ast.parse(main_py)
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != handler_name:
            continue
        for decorator in node.decorator_list:
            commands.update(_command_names_from_decorator(decorator))
    return commands


def _command_decorator_count_for_handler(main_py: str, handler_name: str) -> int:
    tree = ast.parse(main_py)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != handler_name:
            continue
        for decorator in node.decorator_list:
            if _command_names_from_decorator(decorator):
                count += 1
    return count


def _registered_commands(main_py: str) -> set[str]:
    tree = ast.parse(main_py)
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            commands.update(_command_names_from_decorator(decorator))
    return commands


def _command_aliases_for_handler(main_py: str, handler_name: str) -> set[str]:
    tree = ast.parse(main_py)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != handler_name:
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "alias":
                    continue
                aliases.update(_string_literals_from_node(keyword.value))
    return aliases


def test_metadata_declares_recommended_astrbot_fields() -> None:
    metadata = yaml.safe_load((PROJECT_ROOT / "metadata.yaml").read_text())

    assert metadata["name"].startswith("astrbot_plugin_")
    assert metadata["display_name"]
    assert metadata["license"] == "Apache-2.0"
    assert metadata["astrbot_version"] == ">=4.26.2,<5"


def test_readme_documents_registered_commands_and_dependency_behavior() -> None:
    main_py = (PROJECT_ROOT / "main.py").read_text()
    readme = (PROJECT_ROOT / "README.md").read_text()

    commands = _registered_commands(main_py)
    readme_commands = set(re.findall(r"^\| `/([^`]+)` \|", readme, re.MULTILINE))
    documented_invocations = {"bgm help"}

    assert commands
    assert commands <= readme_commands
    assert readme_commands - commands == documented_invocations
    assert "插件首次运行时会自动检查并安装" not in readme


def test_bgm_search_aliases_register_on_existing_handlers() -> None:
    main_py = (PROJECT_ROOT / "main.py").read_text()

    assert _command_decorator_count_for_handler(main_py, "search_anime") == 1
    assert _commands_for_handler(main_py, "search_anime") == {
        "bgm番剧",
        "bgm动漫",
        "bgm动画",
        "bgm番",
        "bgm动画片",
    }
    assert _command_decorator_count_for_handler(main_py, "search_movie") == 1
    assert _commands_for_handler(main_py, "search_movie") == {
        "bgm剧场版",
        "bgm电影",
    }


def test_bgm_category_aliases_are_contiguous_commands() -> None:
    main_py = (PROJECT_ROOT / "main.py").read_text()

    assert _command_aliases_for_handler(main_py, "search_anime") == {
        "bgm动漫",
        "bgm动画",
        "bgm番",
        "bgm动画片",
    }
    assert _command_aliases_for_handler(main_py, "search_movie") == {"bgm电影"}
    for command_name in _registered_commands(main_py):
        assert " " not in command_name


def test_readme_version_badge_matches_metadata_version() -> None:
    metadata = yaml.safe_load((PROJECT_ROOT / "metadata.yaml").read_text())
    readme = (PROJECT_ROOT / "README.md").read_text()

    assert (
        f"https://img.shields.io/badge/version-{metadata['version']}-blue.svg" in readme
    )


def test_changelog_documents_metadata_version() -> None:
    metadata = yaml.safe_load((PROJECT_ROOT / "metadata.yaml").read_text())
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text()

    assert f"## {metadata['version']}" in changelog


def test_card_template_docs_include_search_results() -> None:
    schema = json.loads((PROJECT_ROOT / "_conf_schema.json").read_text())
    readme = (PROJECT_ROOT / "README.md").read_text()

    assert "/bgm 搜索结果" in schema["episode_card_template"]["hint"]
    assert "`episode_card_template`" in readme
    assert "`/bgm` 搜索结果" in readme
    assert "scripts/render_subject_variants.py" in readme


def test_plugin_uses_metadata_instead_of_deprecated_register_decorator() -> None:
    main_py = (PROJECT_ROOT / "main.py").read_text()

    assert "@register(" not in main_py
    assert (
        "from astrbot.api.star import Context, Star, StarTools, register" not in main_py
    )


def test_main_imports_match_astrbot_package_loading() -> None:
    main_py = (PROJECT_ROOT / "main.py").read_text()

    assert "from .src." in main_py, (
        f"{ASTROBOT_IMPORT_RULE}: main.py must import plugin internals via .src"
    )
    assert "from src." not in main_py, (
        f"{ASTROBOT_IMPORT_RULE}: main.py must not fall back to top-level src imports"
    )
    assert "import src." not in main_py, (
        f"{ASTROBOT_IMPORT_RULE}: main.py must not fall back to top-level src imports"
    )
    assert "except ImportError" not in main_py, (
        f"{ASTROBOT_IMPORT_RULE}: main.py must not use top-level src fallback imports"
    )


def test_tests_and_scripts_use_package_imports_for_plugin_internals() -> None:
    violations: list[str] = []
    for file_path in _python_files_under("tests", "scripts"):
        violations.extend(_find_direct_internal_imports(file_path))
        violations.extend(_find_top_level_monkeypatch_targets(file_path))

    assert not violations, (
        f"{ASTROBOT_IMPORT_RULE}: tests and scripts must use "
        "astrbot_plugin_bangumi.src package paths instead of top-level src imports "
        "or monkeypatch targets:\n" + "\n".join(violations)
    )


def test_config_schema_exposes_render_mode_options() -> None:
    schema = json.loads((PROJECT_ROOT / "_conf_schema.json").read_text())

    assert schema["render_mode"]["default"] == "pillow"
    assert schema["render_mode"]["options"] == ["pillow", "playwright", "rpc"]
    assert schema["episode_card_template"]["default"] == "pastel_lightbox"
    assert schema["episode_card_template"]["options"] == [
        "pastel_lightbox",
        "editorial_digest",
        "cinematic_poster",
    ]


def test_gitignore_excludes_generated_artifacts() -> None:
    gitignore_lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "rendered_images/" in gitignore_lines
    assert ".codex-pet-runs/" in gitignore_lines
    assert ".pipeline-last-run-summary.json" in gitignore_lines
