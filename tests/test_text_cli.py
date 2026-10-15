import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from image_lab import text_cli


@pytest.fixture
def source(tmp_path):
    image = Image.new("RGB", (100, 100), (0, 180, 180))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 30, 30), fill=(0, 0, 0))
    draw.rectangle((65, 65, 75, 75), fill=(0, 0, 0))
    path = tmp_path / "source.png"
    image.save(path)
    return path


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []
    def generate(**kwargs):
        out = Path(kwargs["out_dir"])
        out.mkdir(parents=True)
        image = Image.open(kwargs["image_path"]).convert("RGB")
        box = kwargs["box"]
        image.paste((0, 180, 180), box)
        target = out / "inpaint-000.png"
        image.save(target)
        calls.append((Path(kwargs["image_path"]), kwargs["detail_level"]))
        return SimpleNamespace(outputs=(target,), warnings=())
    monkeypatch.setattr("image_lab.inpaint.run_inpaint", generate)
    monkeypatch.setattr(text_cli, "require_auth", lambda: None)
    return calls


def test_cleanup_keeps_canvas_and_exterior(source, tmp_path, fake_ai):
    out = tmp_path / "clean"
    assert text_cli.main(["cleanup", "--image", str(source), "--box", "10,10,40,40",
                          "--out", str(out), "--model", "test-model"]) == 0
    report = json.loads((out / "verification.json").read_text())
    assert report["outputs"][0]["outside_region_changed_pixels"] == 0
    assert report["native_canvas_size"] == [100, 100]
    assert report["text_ink_restored"] is False
    edited = np.asarray(Image.open(out / "clean-000.png"))
    assert np.array_equal(edited[65:76, 65:76], np.zeros((11, 11, 3), np.uint8))
    assert fake_ai[0][1] == 1


def test_whole_image_cleanup_is_reported_as_ai_redrawing(source, tmp_path, fake_ai):
    out = tmp_path / "whole"
    assert text_cli.main(["cleanup", "--image", str(source), "--out", str(out), "--model", "test-model"]) == 0
    report = json.loads((out / "verification.json").read_text())
    assert report["whole_image"] is True
    assert report["text_ink_restored"] is False
    assert Image.open(out / "clean-000.png").size == (100, 100)


def test_restore_uses_sequential_clean_panels_and_preserves_both_ink_regions(source, tmp_path, fake_ai):
    helper = text_cli.layers()
    config = tmp_path / "regions.json"
    config.write_text(json.dumps({"regions": [
        {"name": "title", "box": [10, 10, 40, 40], "kernel": 31},
        {"name": "logo", "box": [55, 55, 85, 85], "kernel": 31}
    ]}))
    prepared = tmp_path / "prepared"
    helper.prepare(source, source, config, prepared)
    out = tmp_path / "restore"
    assert text_cli.main(["restore", "--prepared", str(prepared), "--out", str(out), "--model", "test-model"]) == 0
    report = json.loads((out / "final/verification.json").read_text())
    assert report["passed"] and not report["source_was_transformed"]
    assert all(region["opaque_ink_pixels"] > 0 for region in report["regions"])
    assert fake_ai[1][0] == out / "background-00/inpaint-000.png"
    assert len(fake_ai) == 2


def test_bad_box_is_rejected_before_generation(source, tmp_path, fake_ai):
    assert text_cli.main(["cleanup", "--image", str(source), "--box", "10,10,200,40",
                          "--out", str(tmp_path / "bad")]) == 1
    assert not fake_ai
    assert not (tmp_path / "bad").exists()


def test_offline_layers_verify_does_not_require_login_or_change_argv(monkeypatch):
    helper = SimpleNamespace(main=lambda: 7)
    monkeypatch.setattr(text_cli, "layers", lambda: helper)
    saved = sys.argv
    assert text_cli.main(["layers", "verify", "--help"]) == 7
    assert sys.argv is saved
