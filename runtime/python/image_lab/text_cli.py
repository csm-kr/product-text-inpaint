"""AI image cleanup and restoration of original text/logo ink."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLEAN_REQUEST = (
    "Clean random grain, speckles, scratches and jagged noise in the selected region. "
    "Keep the exact product, shapes, colors, layout, lighting, text and logos. "
    "Preserve meaningful natural detail. Do not add writing, objects or artificial texture."
)
BLANK_REQUEST = (
    "Remove ALL printed text, numerals and logos from the selected packaging panel. "
    "Leave only a clean smooth substrate matching the surrounding panel color and lighting. "
    "No new writing, logos, grain, speckles, scratches, mottling or artificial paper texture. "
    "Original ink will be restored afterward from source pixels. Preserve geometry and boundaries."
)


def layers():
    path = ROOT / "skills/product-text-inpaint/scripts/text_layers.py"
    spec = importlib.util.spec_from_file_location("product_ink_layers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def doctor() -> int:
    from image_lab.preflight import check_auth, default_codex_home
    from image_lab.tibo import default_skill_root, node_executable
    home = default_codex_home()
    status = check_auth(home / "auth.json", home / "installation_id", int(time.time()))
    ok = status.ok
    print(f"auth_ok: {status.ok}; mode: {status.auth_mode}")
    for message in status.problems + status.warnings:
        print(message)
    for label, command, arguments in (("node", node_executable(), ["--version"]),
                                      ("ffmpeg", "ffmpeg", ["-version"]),
                                      ("ffprobe", "ffprobe", ["-version"])):
        executable = shutil.which(command)
        if not executable:
            ok = False
            print(f"Missing runtime: {label}")
            continue
        result = subprocess.run([executable, *arguments], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, creationflags=FLAGS)
        lines = (result.stdout or result.stderr).splitlines()
        print(f"{label}: {lines[0] if lines else result.returncode}")
        ok = ok and result.returncode == 0
    script = default_skill_root() / "scripts/tibo-batch.mjs"
    print(f"image runtime available: {script.is_file()}")
    print(f"model: {os.environ.get('CODEX_IMAGEGEN_MODEL') or 'set CODEX_IMAGEGEN_MODEL to a supported model'}")
    return 0 if ok and script.is_file() else 1


def require_auth():
    from image_lab.preflight import check_auth, default_codex_home
    home = default_codex_home()
    status = check_auth(home / "auth.json", home / "installation_id", int(time.time()))
    if not status.ok:
        raise ValueError("Codex login required: " + "; ".join(status.problems))
    if not os.environ.get("CODEX_IMAGEGEN_MODEL"):
        raise ValueError("Set --model or CODEX_IMAGEGEN_MODEL to a model supported by your account.")


def parse_box(value):
    try:
        box = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("box must be x1,y1,x2,y2") from error
    if len(box) != 4:
        raise argparse.ArgumentTypeError("box must contain four integers")
    return box


def cleanup(args):
    import numpy as np
    from image_lab.inpaint import run_inpaint
    helper = layers()
    image = helper.open_rgb(args.image)
    box = args.box or [0, 0, image.width, image.height]
    helper.valid_box(box, image.size)
    require_auth()
    root = helper.fresh_dir(args.out)
    normalized = root / "input-normalized.png"
    image.save(normalized)
    result = run_inpaint(image_path=normalized, box=tuple(box), request=args.request or CLEAN_REQUEST,
                         out_dir=root / "ai", batch_size=args.batch, detail_level=args.detail_level,
                         dry_run=args.dry_run)
    for message in result.warnings:
        print(message, file=sys.stderr)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "images_generated": 0}))
        return
    inside = np.zeros((image.height, image.width), bool)
    x1, y1, x2, y2 = box
    inside[y1:y2, x1:x2] = True
    outputs = []
    for index, candidate in enumerate(result.outputs):
        edited = helper.open_rgb(candidate)
        if edited.size != image.size:
            raise ValueError("Canvas size changed.")
        outside = int(np.count_nonzero(np.any(np.asarray(image) != np.asarray(edited), axis=2) & ~inside))
        if outside:
            raise ValueError("Pixels outside the edit box changed.")
        target = root / f"clean-{index:03d}.png"
        edited.save(target)
        outputs.append({"image": str(target), "outside_region_changed_pixels": outside})
    report = {"mode": "ai-cleanup", "native_canvas_size": list(image.size), "box": box,
              "text_ink_restored": False, "whole_image": args.box is None,
              "scope": "AI redraws editable pixels; source text identity requires the restoration workflow.",
              "model": os.environ["CODEX_IMAGEGEN_MODEL"], "outputs": outputs}
    helper.write_json(root / "verification.json", report)
    print(json.dumps(report, ensure_ascii=False))


def restore(args):
    from image_lab.inpaint import run_inpaint
    helper = layers()
    prepared, plan, base = helper.load_prepared(args.prepared)
    require_auth()
    root = helper.fresh_dir(args.out)
    current = prepared / "base-normalized.png"
    for index, region in enumerate(plan["regions"]):
        print(f"Cleaning panel {index + 1}/{len(plan['regions'])}: {region['name']}", flush=True)
        result = run_inpaint(image_path=current, box=tuple(region["box"]), request=BLANK_REQUEST,
                             out_dir=root / f"background-{index:02d}", batch_size=1,
                             detail_level=args.detail_level)
        for message in result.warnings:
            print(message, file=sys.stderr)
        current = result.outputs[0]
    report = helper.composite(prepared, current, root / "final")
    print(json.dumps(report, ensure_ascii=False))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "layers":
        previous = sys.argv
        try:
            sys.argv = ["text_layers", *argv[1:]]
            return layers().main()
        finally:
            sys.argv = previous
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check login and local runtime without generation")
    sub.add_parser("layers", help="Local ink extraction, compositing and verification")
    clean = sub.add_parser("cleanup", help="AI cleanup of an image or a box")
    clean.add_argument("--image", required=True)
    clean.add_argument("--box", type=parse_box, help="Omit to clean the entire image")
    clean.add_argument("--request")
    clean.add_argument("--batch", type=int, choices=range(1, 9), default=1)
    clean.add_argument("--dry-run", action="store_true")
    restore_parser = sub.add_parser("restore", help="Clean reviewed panels and restore prepared original ink")
    restore_parser.add_argument("--prepared", required=True)
    for command in (clean, restore_parser):
        command.add_argument("--out", required=True)
        command.add_argument("--model")
        command.add_argument("--detail-level", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args(argv)
    if getattr(args, "model", None):
        os.environ["CODEX_IMAGEGEN_MODEL"] = args.model
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "cleanup":
            cleanup(args)
        else:
            restore(args)
    except (ValueError, OSError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
