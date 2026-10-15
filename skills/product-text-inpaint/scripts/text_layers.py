"""Original dark-ink layers for local AI inpainting; no AI calls or denoising."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


def open_rgb(path):
    with Image.open(path) as image:
        oriented = ImageOps.exif_transpose(image)
        if oriented.mode == "RGBA" or "transparency" in oriented.info:
            raise ValueError("Transparent inputs require an explicit opaque base.")
        return oriented.convert("RGB").copy()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fresh_dir(path):
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Use a new or empty output directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def valid_box(value, size):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("A box must be [x1,y1,x2,y2].")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError("Box coordinates must be integers.")
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError(f"Box outside the normalized image: {value}, {size}")
    return value


def valid_quad(value, size):
    points = np.asarray(value, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("A quad requires four finite [x,y] points in TL,TR,BR,BL order.")
    if np.any(points < 0) or np.any(points[:, 0] >= size[0]) or np.any(points[:, 1] >= size[1]):
        raise ValueError("Quad points must lie inside the EXIF-normalized image.")
    if not cv2.isContourConvex(points) or cv2.contourArea(points) < 1:
        raise ValueError("Quad must be convex, ordered, and nondegenerate.")
    return points


def extract_ink(rgb, region, config_root):
    pixels = np.asarray(rgb, dtype=np.float32)
    kernel = region.get("kernel", 41)
    floor, opaque = region.get("noise_floor", 0.10), region.get("opaque_at", 0.80)
    if isinstance(kernel, bool) or not isinstance(kernel, int) or kernel < 3 or kernel % 2 == 0:
        raise ValueError("kernel must be an odd integer of at least 3.")
    if not (0 <= floor < opaque <= 1):
        raise ValueError("Require 0 <= noise_floor < opaque_at <= 1.")
    struct = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    backdrop = cv2.morphologyEx(pixels, cv2.MORPH_CLOSE, struct)
    if region.get("mask"):
        mask_path = (config_root / region["mask"]).resolve()
        with Image.open(mask_path) as mask:
            layer = mask.getchannel("A") if mask.mode == "RGBA" else mask.convert("L")
            if layer.size != rgb.size:
                raise ValueError(f"Manual mask size must be {rgb.size}: {mask_path}")
            alpha = np.asarray(layer, dtype=np.uint8).copy()
    else:
        # Estimate substrate only to separate ink; never save it as a photo edit.
        # Project the color difference onto the substrate color vector. A max-channel
        # ratio amplifies noise in a nearly-zero red channel on cyan/teal paper.
        contrast = np.sum((backdrop - pixels) * backdrop, axis=2) / np.maximum(np.sum(backdrop ** 2, axis=2), 1024)
        alpha = np.rint(np.clip((contrast - floor) / (opaque - floor), 0, 1) * 255).astype(np.uint8)
    for exclusion in region.get("exclude", []):
        valid_box(exclusion, region["base_size"])
        bx, by, ex, ey = region["box"]
        x1, y1 = max(bx, exclusion[0]), max(by, exclusion[1])
        x2, y2 = min(ex, exclusion[2]), min(ey, exclusion[3])
        if x1 < x2 and y1 < y2:
            alpha[y1 - by:y2 - by, x1 - bx:x2 - bx] = 0
    if not np.any(alpha):
        raise ValueError(f"No ink extracted in {region['name']}; inspect the box and mask.")
    if np.mean(alpha > 0) > 0.65:
        raise ValueError(f"Mask covers most of {region['name']}; use a manual mask for this substrate.")
    opacity = alpha.astype(np.float32)[..., None] / 255
    # Unmatte edge pixels to avoid copying the old substrate as a halo.
    foreground = np.clip((pixels - (1 - opacity) * backdrop) / np.maximum(opacity, 1 / 255), 0, 255)
    rgba = np.dstack((np.rint(foreground).astype(np.uint8), alpha))
    return Image.fromarray(alpha), Image.fromarray(rgba)


def prepare(source_path, base_path, config_path, out_path):
    source_path, base_path, config_path = map(lambda p: Path(p).resolve(), (source_path, base_path, config_path))
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    source, base = open_rgb(source_path), open_rgb(base_path)
    if bool(config.get("source_quad")) != bool(config.get("base_quad")):
        raise ValueError("Provide both source_quad and base_quad, or neither.")
    transform = np.eye(3, dtype=np.float64)
    if config.get("source_quad"):
        transform = cv2.getPerspectiveTransform(valid_quad(config["source_quad"], source.size), valid_quad(config["base_quad"], base.size))
        aligned = Image.fromarray(cv2.warpPerspective(np.asarray(source), transform, base.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT))
        coverage = cv2.warpPerspective(np.full((source.height, source.width), 255, dtype=np.uint8), transform, base.size, flags=cv2.INTER_NEAREST)
    else:
        if source.size != base.size:
            raise ValueError("Different source/base sizes require corresponding print-plane quads.")
        aligned, coverage = source.copy(), np.full((base.height, base.width), 255, dtype=np.uint8)
    regions = config.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("At least one text region is required.")
    names, prepared = set(), []
    for settings in regions:
        name = settings.get("name", "")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name in names:
            raise ValueError("Each region needs a unique lowercase hyphenated name.")
        names.add(name)
        box = valid_box(settings.get("box"), base.size)
        x1, y1, x2, y2 = box
        if not np.all(coverage[y1:y2, x1:x2]):
            raise ValueError(f"Source does not cover {name}; inspect the correspondence quads.")
        region = dict(settings, box=box, base_size=base.size)
        mask, ink = extract_ink(aligned.crop(box), region, config_path.parent)
        prepared.append((region, mask, ink))
    out = fresh_dir(out_path)
    base.save(out / "base-normalized.png")
    aligned.save(out / "source-aligned.png")
    plan = {
        "schema_version": 1, "source": str(source_path), "base": str(base_path),
        "source_sha256": digest(source_path), "base_sha256": digest(base_path),
        "source_size": list(source.size), "base_size": list(base.size),
        "transform": transform.tolist(), "transformed": not np.allclose(transform, np.eye(3)),
        "regions": [], "requires_visual_mask_review": True,
    }
    for region, mask, ink in prepared:
        name = region["name"]
        mask_file, ink_file = f"{name}-alpha.png", f"{name}-ink.png"
        mask.save(out / mask_file)
        ink.save(out / ink_file)
        Image.alpha_composite(Image.new("RGBA", ink.size, "white"), ink).convert("RGB").save(out / f"{name}-preview.png")
        aligned.crop(region["box"]).save(out / f"{name}-source.png")
        plan["regions"].append({"name": name, "box": region["box"], "mask": mask_file, "ink": ink_file, "ink_sha256": digest(out / ink_file), "mask_sha256": digest(out / mask_file)})
    plan["normalized_base_sha256"] = digest(out / "base-normalized.png")
    write_json(out / "plan.json", plan)
    return plan


def load_prepared(folder):
    root = Path(folder).resolve()
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported prepared-plan version.")
    base = open_rgb(root / "base-normalized.png")
    if list(base.size) != plan["base_size"] or digest(root / "base-normalized.png") != plan["normalized_base_sha256"]:
        raise ValueError("Prepared base changed; run prepare again.")
    for region in plan["regions"]:
        valid_box(region["box"], base.size)
        for key in ("ink", "mask"):
            path = (root / region[key]).resolve()
            if not path.is_relative_to(root) or digest(path) != region[f"{key}_sha256"]:
                raise ValueError(f"Prepared {key} changed; run prepare again with the intended mask.")
    return root, plan, base


def inside_regions(plan, size):
    inside = np.zeros((size[1], size[0]), dtype=bool)
    for region in plan["regions"]:
        x1, y1, x2, y2 = region["box"]
        inside[y1:y2, x1:x2] = True
    return inside


def verify_image(root, plan, base, result):
    if result.size != base.size:
        raise ValueError(f"Canvas size changed: {result.size} != {base.size}")
    values, before = np.asarray(result), np.asarray(base)
    changed = np.any(values != before, axis=2)
    outside = int(np.count_nonzero(changed & ~inside_regions(plan, base.size)))
    checks = []
    for region in plan["regions"]:
        ink = np.asarray(Image.open(root / region["ink"]).convert("RGBA"))
        target = np.asarray(result.crop(region["box"]))
        core = ink[:, :, 3] == 255
        mismatch = int(np.count_nonzero(core & np.any(target != ink[:, :, :3], axis=2)))
        checks.append({"name": region["name"], "opaque_ink_pixels": int(core.sum()), "opaque_ink_mismatches": mismatch})
    return {
        "passed": outside == 0 and all(r["opaque_ink_mismatches"] == 0 for r in checks),
        "outside_region_changed_pixels": outside, "regions": checks,
        "source_was_transformed": plan["transformed"], "native_canvas_size": list(base.size),
        "scope": "Outside-base pixels and prepared opaque ink only; visual extraction/letter review is separate.",
    }


def composite(prepared_path, clean_path, out_path):
    root, plan, base = load_prepared(prepared_path)
    clean = open_rgb(clean_path)
    if clean.size != base.size:
        raise ValueError("Clean background must have exactly the normalized base size.")
    changes = np.any(np.asarray(clean) != np.asarray(base), axis=2)
    if np.any(changes & ~inside_regions(plan, base.size)):
        raise ValueError("Clean candidate changed pixels outside the text regions.")
    result = clean.copy()
    for region in plan["regions"]:
        blank = np.asarray(clean.crop(region["box"]))
        dark_count = int(np.count_nonzero(np.max(blank, axis=2) < 85))
        if dark_count > max(5, blank.shape[0] * blank.shape[1] * 0.0005):
            raise ValueError(f"Possible residual dark text in {region['name']}: {dark_count} pixels. Inspect/inpaint again.")
        ink = Image.open(root / region["ink"]).convert("RGBA")
        patch = Image.alpha_composite(result.crop(region["box"]).convert("RGBA"), ink).convert("RGB")
        result.paste(patch, tuple(region["box"][:2]))
    checks = verify_image(root, plan, base, result)
    if not checks["passed"]:
        raise ValueError(f"Preservation check failed: {checks}")
    out = fresh_dir(out_path)
    result.save(out / "final.png")
    checks["final_sha256"] = digest(out / "final.png")
    checks["prepared_plan"] = str(root / "plan.json")
    checks["clean_base_sha256"] = digest(clean_path)
    write_json(out / "verification.json", checks)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    for argument in ("source", "base", "config", "out"):
        prep.add_argument(f"--{argument}", required=True)
    comp = commands.add_parser("composite")
    for argument in ("prepared", "clean-base", "out"):
        comp.add_argument(f"--{argument}", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--prepared", required=True)
    check.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            data = prepare(args.source, args.base, args.config, args.out)
            print(json.dumps({"prepared": str(Path(args.out).resolve()), "regions": [r["name"] for r in data["regions"]], "requires_visual_mask_review": True}, ensure_ascii=False))
        elif args.command == "composite":
            print(json.dumps(composite(args.prepared, args.clean_base, args.out), ensure_ascii=False))
        else:
            root, plan, base = load_prepared(args.prepared)
            data = verify_image(root, plan, base, open_rgb(args.image))
            print(json.dumps(data, ensure_ascii=False))
            return 0 if data["passed"] else 1
    except (ValueError, OSError, KeyError, TypeError, cv2.error) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
