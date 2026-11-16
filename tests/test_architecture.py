"""The hexagon's rules, checked on every test run.

domain       may use the standard library and itself, nothing else
application  may use the standard library, the domain and itself
adapters     may use anything; only they may import SoCo, yt-dlp, websockets ...
"""

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "sonosyt"
STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def imports(path: Path) -> set[str]:
    """Absolute module names imported by a file (relative imports resolved)."""
    module = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
    package = module.rsplit(".", 1)[0] if path.name != "__init__.py" else module
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                found.add(".".join(base + ([node.module] if node.module else [])))
            else:
                found.add(node.module)
    return found


def files(layer: str) -> list[Path]:
    return sorted((PACKAGE / layer).rglob("*.py"))


def layer_of(name: str) -> str:
    parts = name.split(".")
    if parts[0] == "sonosyt":
        return parts[1] if len(parts) > 1 else "sonosyt"
    return "stdlib" if parts[0] in STDLIB else "third-party"


@pytest.mark.parametrize("layer, allowed", [
    ("domain", {"stdlib", "domain"}),
    ("application", {"stdlib", "domain", "application"}),
])
def test_inner_layers_depend_only_inwards(layer, allowed):
    offenders = {
        f"{path.relative_to(PACKAGE)} imports {name}"
        for path in files(layer)
        for name in imports(path)
        if layer_of(name) not in allowed
    }
    assert not offenders, sorted(offenders)


def test_only_adapters_and_the_composition_root_touch_technology():
    technology = {"soco", "yt_dlp", "sonos_websocket", "aiohttp", "requests"}
    offenders = {
        f"{path.relative_to(PACKAGE)} imports {name}"
        for path in PACKAGE.rglob("*.py")
        if "adapters" not in path.parts
        for name in imports(path)
        if name.split(".")[0] in technology
    }
    assert not offenders, sorted(offenders)


def test_the_layout_is_what_the_readme_describes():
    for layer in ("domain", "application", "adapters"):
        assert (PACKAGE / layer / "__init__.py").exists()
    assert (PACKAGE / "application" / "ports.py").exists()
    assert (PACKAGE / "bootstrap.py").exists()
