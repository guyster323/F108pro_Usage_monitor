#!/usr/bin/env python3
"""Keep package, module, and collector-docs versions aligned at 0.2.3."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.2.3"


def _dunder_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant):
                        return str(node.value.value)
    raise SystemExit(f"no __version__ in {path}")


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    versions = {
        "pyproject": project["project"]["version"],
        "package": _dunder_version(ROOT / "src" / "quotadeck" / "__init__.py"),
    }
    rpc = (ROOT / "src" / "quotadeck" / "providers" / "codex" / "appserver_rpc.py").read_text(
        encoding="utf-8"
    )
    if f'"version": "{EXPECTED}"' not in rpc:
        print("appserver clientInfo version is not 0.2.3", file=sys.stderr)
        return 1
    for name, version in versions.items():
        if version != EXPECTED:
            print(f"{name} version {version} != {EXPECTED}", file=sys.stderr)
            return 1
    coin = ROOT / "src" / "quotadeck" / "bundled" / "icons" / "coin-pixel.png"
    source = ROOT / "artwork" / "coin-pixel-source.png"
    if not coin.is_file() or not source.is_file():
        print("coin source or runtime asset is missing", file=sys.stderr)
        return 1
    print(f"release ok: QuotaDeck {EXPECTED} with packaged coin assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
