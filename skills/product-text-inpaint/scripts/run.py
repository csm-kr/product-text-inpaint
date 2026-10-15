"""Run the independent image tool from the user's current photo directory."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def runtime_root(skill=None):
    skill = Path(skill or Path(__file__).resolve().parents[1])
    explicit = os.environ.get("PRODUCT_TEXT_INPAINT_ROOT")
    marker = skill / "runtime.json"
    if explicit:
        root = Path(explicit).expanduser().resolve()
    elif marker.is_file():
        root = Path(json.loads(marker.read_text(encoding="utf-8"))["root"]).expanduser().resolve()
    else:
        root = skill.parents[1]
    if not (root / "text-clean").is_file() or not (root / "runtime/python/image_lab/text_cli.py").is_file():
        raise ValueError("Independent runtime not found. Run this repository's installer or set PRODUCT_TEXT_INPAINT_ROOT.")
    return root


def command(root, arguments):
    environment = os.environ.copy()
    shell = shutil.which("sh")
    if sys.platform == "win32":
        if not shell:
            result = subprocess.run(["git", "--exec-path"], capture_output=True, text=True,
                                    check=True, creationflags=FLAGS)
            candidate = Path(result.stdout.strip()).resolve().parents[2] / "usr/bin/sh.exe"
            if candidate.is_file():
                shell = str(candidate)
        if not shell:
            raise ValueError("Git for Windows is required to run the local toolchain.")
        git_root = Path(shell).resolve().parents[2]
        paths = [str(Path(shell).parent), str(git_root / "mingw64/bin"), str(git_root / "cmd")]
        environment["PATH"] = os.pathsep.join([*paths, environment.get("PATH", "")])
    return [shell or "sh", str(root / "text-clean"), *arguments], environment


def execute(argv, environment):
    child = subprocess.Popen(argv, env=environment, creationflags=FLAGS,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", bufsize=1)
    def forward(source, destination):
        for line in source:
            destination.write(line)
            destination.flush()
        source.close()
    threads = [threading.Thread(target=forward, args=(child.stdout, sys.stdout)),
               threading.Thread(target=forward, args=(child.stderr, sys.stderr))]
    for thread in threads:
        thread.start()
    result = child.wait()
    for thread in threads:
        thread.join()
    return result


def main():
    try:
        argv, environment = command(runtime_root(), sys.argv[1:])
        return execute(argv, environment)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
