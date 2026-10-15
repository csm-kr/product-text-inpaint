import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_runner", ROOT / "skills/product-text-inpaint/scripts/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_installed_skill_resolves_runtime_outside_photo_directory(tmp_path, monkeypatch):
    skill = tmp_path / "installed-skill"
    photo_directory = tmp_path / "photos"
    skill.mkdir(); photo_directory.mkdir()
    (skill / "runtime.json").write_text(json.dumps({"root": str(ROOT)}))
    monkeypatch.delenv("PRODUCT_TEXT_INPAINT_ROOT", raising=False)
    monkeypatch.chdir(photo_directory)
    assert runner.runtime_root(skill) == ROOT


def test_runner_preserves_comma_box_and_spaces_in_prompt(tmp_path):
    arguments = ["cleanup", "--box", "10,20,30,40", "--request", "keep all original ink"]
    command, environment = runner.command(ROOT, arguments)
    assert command[2:] == arguments
    assert "PATH" in environment


def test_hidden_runner_preserves_stdout_stderr_and_failure_exit_code(capsys):
    result = runner.execute([sys.executable, "-c",
        "import sys; print('visible output'); print('visible error',file=sys.stderr); sys.exit(7)"], os.environ.copy())
    captured = capsys.readouterr()
    assert result == 7
    assert "visible output" in captured.out
    assert "visible error" in captured.err
