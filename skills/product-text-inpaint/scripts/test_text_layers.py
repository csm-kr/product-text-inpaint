"""Offline behavior checks: no external image generation, real pixel assertions."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import text_layers as layers


class TextLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="product-text-inpaint-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.color = (0, 180, 170)
        self.source = Image.new("RGB", (160, 110), self.color)
        self.source.putpixel((0, 0), (200, 17, 40))
        draw = ImageDraw.Draw(self.source)
        # A-shaped glyph, plus sign, and a detached punctuation dot.
        draw.rectangle((25, 28, 29, 65), fill=(15, 18, 20))
        draw.rectangle((47, 28, 51, 65), fill=(15, 18, 20))
        draw.rectangle((25, 28, 51, 32), fill=(15, 18, 20))
        draw.rectangle((25, 44, 51, 48), fill=(15, 18, 20))
        draw.rectangle((67, 42, 87, 45), fill=(15, 18, 20))
        draw.rectangle((76, 33, 79, 54), fill=(15, 18, 20))
        draw.rectangle((103, 63, 104, 64), fill=(15, 18, 20))
        self.box = [12, 15, 135, 85]
        self.source_path = self.root / "source.png"
        self.source.save(self.source_path)
        self.config_path = self.root / "regions.json"
        self.config = {"regions": [{"name": "title", "box": self.box, "kernel": 31}]}
        self.prepared = self.root / "prepared"
        self.write_config()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def prepare(self):
        return layers.prepare(self.source_path, self.source_path, self.config_path, self.prepared)

    def clean_base(self):
        base = self.source.copy()
        x1, y1, x2, y2 = self.box
        base.paste(Image.new("RGB", (x2 - x1, y2 - y1), (0, 205, 196)), (x1, y1))
        path = self.root / "clean.png"
        base.save(path)
        return path

    def test_restore_changes_substrate_preserves_ink_and_exterior(self):
        self.prepare()
        final_dir = self.root / "final"
        report = layers.composite(self.prepared, self.clean_base(), final_dir)
        result = np.asarray(Image.open(final_dir / "final.png"))
        original = np.asarray(self.source)
        self.assertTrue(report["passed"])
        self.assertEqual(report["outside_region_changed_pixels"], 0)
        self.assertGreater(report["regions"][0]["opaque_ink_pixels"], 100)
        self.assertTrue(np.array_equal(result[30:64, 25:30], original[30:64, 25:30]))
        self.assertTrue(np.array_equal(result[63:65, 103:105], original[63:65, 103:105]))
        self.assertEqual(result[20, 20].tolist(), [0, 205, 196])
        self.assertEqual(result[0, 0].tolist(), [200, 17, 40])

    def test_rejects_clean_candidate_with_one_changed_exterior_pixel(self):
        self.prepare()
        clean = self.clean_base()
        image = Image.open(clean)
        image.putpixel((0, 0), (0, 0, 0))
        image.save(clean)
        with self.assertRaisesRegex(ValueError, "outside"):
            layers.composite(self.prepared, clean, self.root / "final")

    def test_rejects_ghost_text_on_unclean_background(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "residual dark text"):
            layers.composite(self.prepared, self.source_path, self.root / "final")

    def test_manual_mask_preserves_a_tiny_punctuation_mark(self):
        mask = Image.new("L", (123, 70), 0)
        ImageDraw.Draw(mask).rectangle((91, 48, 92, 49), fill=255)
        mask.save(self.root / "manual.png")
        self.config["regions"][0]["mask"] = "manual.png"
        self.write_config()
        self.prepare()
        alpha = np.asarray(Image.open(self.prepared / "title-alpha.png"))
        self.assertEqual(np.count_nonzero(alpha), 4)
        layers.composite(self.prepared, self.clean_base(), self.root / "final")
        result = Image.open(self.root / "final" / "final.png")
        self.assertEqual(result.getpixel((103, 63)), (15, 18, 20))

    def test_verify_detects_a_changed_opaque_glyph_pixel(self):
        self.prepare()
        layers.composite(self.prepared, self.clean_base(), self.root / "final")
        result = Image.open(self.root / "final" / "final.png")
        result.putpixel((25, 30), (255, 255, 255))
        root, plan, base = layers.load_prepared(self.prepared)
        check = layers.verify_image(root, plan, base, result)
        self.assertFalse(check["passed"])
        self.assertEqual(check["regions"][0]["opaque_ink_mismatches"], 1)

    def test_exif_coordinates_are_normalized_before_region_selection(self):
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 30), (12, 23, 34)).save(self.root / "rotated.jpg", exif=exif)
        self.assertEqual(layers.open_rgb(self.root / "rotated.jpg").size, (30, 40))

    def test_transform_is_explicit_and_reports_the_new_coordinate_space(self):
        self.config["source_quad"] = [[0, 0], [159, 0], [159, 109], [0, 109]]
        self.config["base_quad"] = [[0, 0], [159, 0], [154, 109], [5, 109]]
        self.write_config()
        plan = self.prepare()
        self.assertTrue(plan["transformed"])
        self.assertEqual(plan["base_size"], [160, 110])

    def test_refuses_implicit_resize_and_reusing_nonempty_output(self):
        Image.new("RGB", (80, 55), self.color).save(self.root / "small.png")
        with self.assertRaisesRegex(ValueError, "Different source/base sizes"):
            layers.prepare(self.source_path, self.root / "small.png", self.config_path, self.prepared)
        self.prepare()
        with self.assertRaisesRegex(ValueError, "new or empty"):
            self.prepare()

    def test_prepared_mask_tampering_requires_repreparation(self):
        self.prepare()
        mask = Image.open(self.prepared / "title-alpha.png")
        mask.putpixel((0, 0), 255)
        mask.save(self.prepared / "title-alpha.png")
        with self.assertRaisesRegex(ValueError, "Prepared mask changed"):
            layers.load_prepared(self.prepared)

    def test_small_red_channel_variation_on_teal_is_not_ink(self):
        region = self.source.crop(self.box)
        pixels = np.asarray(region).copy()
        rng = np.random.default_rng(12)
        empty_substrate = np.all(pixels == self.color, axis=2)
        pixels[:, :, 0][empty_substrate] = rng.integers(0, 20, size=int(empty_substrate.sum()))
        settings = dict(self.config["regions"][0], base_size=self.source.size)
        alpha, _ = layers.extract_ink(Image.fromarray(pixels), settings, self.root)
        values = np.asarray(alpha)
        self.assertEqual(int(np.count_nonzero(values[empty_substrate])), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
