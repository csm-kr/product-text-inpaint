"""Copy this skill into a Codex skills directory without replacing existing files."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def install(destination: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    root = source.parents[1]
    if not (root / "text-clean").is_file():
        raise ValueError("Install from the independent product-text-inpaint repository.")
    destination = destination.expanduser().resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("The destination must be outside the source skill directory.")
    if destination.exists():
        raise ValueError(f"Destination already exists; keep or rename it before installing: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / "runtime.json").write_text(json.dumps({"root": str(root)}, indent=2), encoding="utf-8")
    return destination


def main() -> int:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=codex_home / "skills/product-text-inpaint",
                        help="Full destination skill directory (default: CODEX_HOME/skills/product-text-inpaint).")
    args = parser.parse_args()
    try:
        print(f"Installed: {install(args.dest)}")
    except (ValueError, OSError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print("Open a new Codex session and invoke $product-text-inpaint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
