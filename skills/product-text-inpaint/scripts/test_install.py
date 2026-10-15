import tempfile
import json
import unittest
from pathlib import Path

import install as skill_install


class InstallTests(unittest.TestCase):
    def test_install_copies_a_complete_portable_skill(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "skills/product-text-inpaint"
            skill_install.install(destination)
            for relative in ("SKILL.md", "agents/openai.yaml", "references/workflow.md",
                             "scripts/text_layers.py", "scripts/install.py", "LICENSE"):
                self.assertTrue((destination / relative).is_file(), relative)
            self.assertFalse(list(destination.rglob("*.pyc")))
            runtime = Path(json.loads((destination / "runtime.json").read_text(encoding="utf-8"))["root"])
            self.assertTrue((runtime / "text-clean").is_file())
            self.assertTrue((runtime / "runtime/python/image_lab/text_cli.py").is_file())

    def test_install_refuses_existing_directory_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "existing"
            destination.mkdir()
            marker = destination / "mine.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                skill_install.install(destination)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual([p.name for p in destination.iterdir()], ["mine.txt"])


if __name__ == "__main__":
    unittest.main()
