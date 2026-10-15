import json
import subprocess
import sys
from pathlib import Path

import pytest

from image_lab.tibo import RunResult, default_skill_root, node_executable, parse_manifest, run_job


def link_directory(link: Path, target: Path) -> None:
    """디렉터리 링크를 만든다.

    Windows 는 개발자 모드나 관리자 권한 없이 symlink 를 만들 수 없다. 그 환경에서는
    junction 으로 대체한다 — Path.resolve() 가 junction 도 실제 경로로 풀어주므로
    이 테스트가 확인하려는 것(argv 에 링크가 아닌 실제 경로가 간다)은 동일하다.
    프로젝트의 로컬 스킬 등록도 Windows 에서는 junction 을 쓴다.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )


def make_manifest(paths, warnings=(), matches=True):
    return {
        "schema_version": 2,
        "dry_run": False,
        "images": [
            {
                "index": i,
                "path": str(p),
                "sha256": "0" * 64,
                "warnings": list(warnings),
                "raw_size": {"width": 1254, "height": 1254},
                "final_size": {"width": 1024, "height": 1024},
                "size_check": {
                    "expected": {"width": 1024, "height": 1024},
                    "matches_expected": matches,
                    "matches_reference": None,
                },
            }
            for i, p in enumerate(paths)
        ],
        "gif_path": None,
    }


def test_parse_manifest_collects_image_paths_in_index_order(tmp_path):
    a, b = tmp_path / "frame-000.png", tmp_path / "frame-001.png"
    result = parse_manifest(make_manifest([a, b]))
    assert isinstance(result, RunResult)
    assert result.images == (a, b)


def test_parse_manifest_surfaces_backend_warnings(tmp_path):
    m = make_manifest([tmp_path / "f.png"], warnings=["unsupported private backend"])
    result = parse_manifest(m)
    assert any("unsupported" in w for w in result.warnings)


def test_parse_manifest_reports_size_check_failure(tmp_path):
    m = make_manifest([tmp_path / "f.png"], matches=False)
    result = parse_manifest(m)
    assert any("크기" in w for w in result.warnings)


def test_parse_manifest_on_empty_images_yields_no_paths():
    result = parse_manifest({"schema_version": 2, "images": [], "gif_path": None})
    assert result.images == ()


def test_default_skill_root_points_at_installed_skill(monkeypatch):
    monkeypatch.delenv("GOD_TIBO_SKILL_ROOT", raising=False)
    root = default_skill_root()
    assert root.name == "god-tibo-gpt-image2-skill"


def test_default_skill_root_honors_environment(monkeypatch, tmp_path):
    configured = tmp_path / "local-skill"
    monkeypatch.setenv("GOD_TIBO_SKILL_ROOT", str(configured))
    assert default_skill_root() == configured


def test_node_executable_honors_environment(monkeypatch, tmp_path):
    configured = tmp_path / "node"
    monkeypatch.setenv("GOD_TIBO_NODE", str(configured))
    assert node_executable() == str(configured)


def test_run_job_writes_job_file_and_invokes_node(tmp_path, monkeypatch):
    """실제 생성 없이 실행 경로만 검증한다 — subprocess 를 가짜로 바꾼다."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    img = out_dir / "frame-000.png"
    img.write_bytes(b"stub")
    (out_dir / "manifest.json").write_text(
        json.dumps(make_manifest([img])), encoding="utf-8"
    )

    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr("image_lab.tibo.subprocess.run", fake_run)

    job = {
        "prompt": "테스트 — 한국어 포함",
        "detail_level": 2,
        "size_mode": "controllable",
        "target_size": "1024x1024",
        "output_dir": "out",
    }
    result = run_job(job, tmp_path)

    # job.json 이 UTF-8 로 기록됐는가
    written = json.loads((tmp_path / "job.json").read_text(encoding="utf-8"))
    assert written["prompt"] == "테스트 — 한국어 포함"

    # node 로 tibo-batch.mjs 를 호출했는가
    assert calls["cmd"][0] == "node"
    assert calls["cmd"][1].endswith("tibo-batch.mjs")
    assert "--job" in calls["cmd"]

    # cp949 사고 방지 — 디코딩 설정이 명시됐는가
    assert calls["kwargs"]["encoding"] == "utf-8"
    assert calls["kwargs"]["errors"] == "replace"

    assert result.images == (img,)


def test_run_job_adds_dry_run_flag(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "images": [], "gif_path": None}), encoding="utf-8"
    )
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr("image_lab.tibo.subprocess.run", fake_run)
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    run_job(job, tmp_path, dry_run=True)
    assert "--dry-run" in captured["cmd"]


def test_run_job_resolves_symlinked_skill_script_before_invoking_node(tmp_path, monkeypatch):
    """Node ESM main guard가 argv/import 경로 불일치로 조용히 끝나지 않게 한다."""
    real_root = tmp_path / "real-skill"
    script = real_root / "scripts" / "tibo-batch.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fake", encoding="utf-8")
    linked_root = tmp_path / "linked-skill"
    link_directory(linked_root, real_root)

    out_dir = tmp_path / "job" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "images": [], "gif_path": None}),
        encoding="utf-8",
    )
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr("image_lab.tibo.subprocess.run", fake_run)
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    run_job(job, tmp_path / "job", skill_root=linked_root, dry_run=True)

    assert Path(captured["cmd"][1]) == script.resolve()


def test_run_job_raises_on_nonzero_exit(tmp_path, monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = "boom"
        stderr = "failed"

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    assert "failed" in str(exc.value) or "boom" in str(exc.value)


MISSING_IMAGE_STDERR = (
    "Error: The response stream completed without an image_generation_call result.\n"
    "    at Module.extractImageGeneration (file:///C:/Users/x/extractImageGeneration.js:53:17)\n"
    "    at async Promise.all (index 1)\n"
    "    at async runWorkerPool (file:///C:/Users/x/tibo-batch.mjs:392:3)\n"
)


def test_missing_image_output_gets_an_explanation_not_a_javascript_stack(tmp_path, monkeypatch):
    """실측한 실패 — 모델이 이미지 대신 텍스트로 답하면 배치 전체가 죽는다.

    god-tibo 의 워커 풀은 Promise.all 이라 재시도도 개별 실패 허용도 없다. 4장 중
    1장의 응답 스트림이 이미지 없이 끝나면 먼저 성공한 나머지까지 통째로 버려지고
    exit 1 이 된다(크레딧은 이미 나갔다).

    사용자에게 자바스크립트 스택을 그대로 들이밀지 않는다 — 무슨 일이 났고 무엇을
    하면 되는지 한국어로 말한다. 원문은 진단용으로 뒤에 붙여 둔다.
    """
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = MISSING_IMAGE_STDERR

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out", "batch_size": 4}
    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    msg = str(exc.value)
    assert "이미지 대신" in msg, "무슨 일이 났는지 한국어로 말하지 않습니다"
    assert "다시" in msg, "무엇을 하면 되는지 말하지 않습니다"
    # 원문을 지우지는 않는다 — 진단할 때 필요하다.
    assert "extractImageGeneration" in msg


def test_other_failures_keep_their_raw_output(tmp_path, monkeypatch):
    """알아본 실패만 바꿔 말한다 — 모르는 실패를 아는 척 설명하면 안 된다."""
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "ENOENT: no such file or directory, open 'job.json'"

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    msg = str(exc.value)
    assert "ENOENT" in msg
    assert "이미지 대신" not in msg, "모르는 실패에 엉뚱한 설명을 붙였습니다"


# --- 아래는 Global Constraints 의 "No Silent Fallback" 을 만족시키기 위해
# 브리프 샘플 코드를 넘어 추가한 테스트다. ---


def test_run_job_raises_when_image_path_missing_on_disk(tmp_path, monkeypatch):
    """exit 0 + manifest 파싱 성공이어도, 실제로는 이미지 파일이 없으면 성공으로 보고하면 안 된다."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    missing = out_dir / "frame-000.png"  # 절대 생성하지 않는다 — 백엔드가 파일 쓰기에 실패한 상황 재현
    (out_dir / "manifest.json").write_text(
        json.dumps(make_manifest([missing])), encoding="utf-8"
    )

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "1024x1024", "output_dir": "out"}

    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    assert str(missing) in str(exc.value)


def test_run_job_raises_when_image_file_is_empty(tmp_path, monkeypatch):
    """path 는 존재하지만 0바이트인 경우도 성공으로 보고하면 안 된다 (SKILL.md 검증 규칙과 동일)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    empty = out_dir / "frame-000.png"
    empty.write_bytes(b"")
    (out_dir / "manifest.json").write_text(
        json.dumps(make_manifest([empty])), encoding="utf-8"
    )

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "1024x1024", "output_dir": "out"}

    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    assert str(empty) in str(exc.value)


def test_run_job_dry_run_allows_missing_image_paths(tmp_path, monkeypatch):
    """dry-run 은 실제 파일을 쓰지 않는다 (god-tibo 실측 확인) — 이 경우 파일 부재를 실패로 보면 안 된다."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    never_written = out_dir / "frame-000.png"
    manifest = make_manifest([never_written])
    manifest["dry_run"] = True
    (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}

    result = run_job(job, tmp_path, dry_run=True)
    assert result.images == (never_written,)


def test_run_job_passes_absolute_job_path_even_when_job_dir_is_relative(tmp_path, monkeypatch):
    """job_dir 를 상대경로로 넘겨도(예: `--out ../ab-test/inpaint-smoke`처럼 실사용에서
    흔한 형태) `--job` 인자는 절대경로여야 한다.

    subprocess 는 `cwd=str(job_dir)`로 실행되는데, 이 cwd 는 (파이썬 프로세스의) 현재
    cwd 기준으로 상대경로가 해석돼 절대경로가 된다. 그런데 `--job` 인자도 같은 상대
    문자열 그대로 넘기면, node 프로세스가 **이미 job_dir 로 바뀐 자신의 cwd** 를 기준으로
    그 상대경로를 다시 한 번 해석해 이중으로 어긋난 경로(예: `.../ab-test/ab-test/
    inpaint-smoke/job.json`)를 찾다가 ENOENT 로 죽는다. 실제 스모크 커맨드
    (`--out ../ab-test/inpaint-smoke`)를 그대로 실행해 재현된 실측 버그다."""
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr("image_lab.tibo.subprocess.run", fake_run)
    monkeypatch.chdir(tmp_path)

    (tmp_path / "run1" / "generated").mkdir(parents=True)
    (tmp_path / "run1" / "generated" / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "images": [], "gif_path": None}), encoding="utf-8"
    )

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "generated"}
    run_job(job, Path("run1"))  # job_dir 을 상대경로로 그대로 넘긴다

    job_arg = calls["cmd"][calls["cmd"].index("--job") + 1]
    assert Path(job_arg).is_absolute(), (
        f"--job 인자가 상대경로입니다: {job_arg!r} — cwd 가 바뀐 상태에서 이중으로 "
        "상대 해석되면 엉뚱한 경로를 가리킬 수 있습니다"
    )


def test_run_job_propagates_subprocess_timeout(tmp_path, monkeypatch):
    """subprocess 가 timeout 나면 부분 성공 RunResult 로 둔갑시키지 않고 그대로 전파해야 한다."""
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr("image_lab.tibo.subprocess.run", fake_run)
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}

    with pytest.raises(subprocess.TimeoutExpired):
        run_job(job, tmp_path)


def test_parse_manifest_raises_when_images_key_is_missing():
    """images 키 자체가 없으면 (스키마 불일치·손상) 빈 RunResult 로 조용히 넘기지 않고 예외를 낸다.

    "images": [] 처럼 키가 명시적으로 있고 값이 빈 배열인 경우는 정상적인 0장 결과이므로
    (test_parse_manifest_on_empty_images_yields_no_paths) 이 검사와 충돌하지 않는다.
    """
    with pytest.raises(RuntimeError) as exc:
        parse_manifest({"schema_version": 2, "dry_run": False, "gif_path": None})
    assert "images" in str(exc.value)


def test_run_job_raises_when_manifest_missing_images_key(tmp_path, monkeypatch):
    """exit 0 + manifest 파싱 성공이어도 images 키 자체가 없으면 성공으로 보고하면 안 된다."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "dry_run": False, "gif_path": None}),
        encoding="utf-8",
    )

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("image_lab.tibo.subprocess.run", lambda cmd, **kw: FakeCompleted())
    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}

    with pytest.raises(RuntimeError) as exc:
        run_job(job, tmp_path)
    assert "images" in str(exc.value)


# --- 아래는 실제 subprocess 바이트를 진짜로 디코딩시켜 인코딩 계약(encoding="utf-8",
# errors="replace")을 검증한다. FakeCompleted 스텁은 이미 str 인 stdout 을 흉내낼 뿐이라
# 이 kwargs 가 빠져도 잡아내지 못한다 — 그래서 subprocess.run 자체는 실제 구현을 그대로
# 호출하는 spy 로만 감싸고, node 자리에는 sys.executable 로 만든 가짜 스크립트를 세운다
# (실제 node 설치 여부에 의존하지 않기 위해서다).

_FAKE_SCRIPT_TEMPLATE = """
import sys, json, pathlib

args = sys.argv[1:]
job_path = None
for i, a in enumerate(args):
    if a == "--job":
        job_path = args[i + 1]
job = json.loads(pathlib.Path(job_path).read_text(encoding="utf-8"))
out_dir = pathlib.Path(job_path).parent / job["output_dir"]
out_dir.mkdir(parents=True, exist_ok=True)

__STDOUT_WRITE__

(out_dir / "manifest.json").write_text(
    json.dumps({"schema_version": 2, "dry_run": False, "images": [], "gif_path": None}),
    encoding="utf-8",
)
"""


def _write_fake_node_script(tmp_path, stdout_write_code: str) -> Path:
    """`node` 자리를 대신할 python 스크립트를 만든다.

    run_job 이 찾는 `<skill_root>/scripts/tibo-batch.mjs` 경로 구조를 그대로 맞추고,
    `--job` 인자로 받은 job.json 을 읽어 manifest.json 을 써서 run_job 의 나머지
    로직(파일 존재 검사 포함)이 정상적으로 이어지게 한다.
    """
    root = tmp_path / "fake-skill"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    source = _FAKE_SCRIPT_TEMPLATE.replace("__STDOUT_WRITE__", stdout_write_code)
    (scripts_dir / "tibo-batch.mjs").write_text(source, encoding="utf-8")
    return root


def test_run_job_decodes_real_utf8_subprocess_output(tmp_path, monkeypatch):
    """실제 subprocess 가 UTF-8 로 인코딩된 한국어·이모지를 stdout 에 흘려도 죽지 않고,
    그 텍스트가 그대로 살아남는다. encoding="utf-8" 이 빠지면 이 테스트가 깨져야 한다
    (Windows 기본 cp949 로는 이 바이트열을 디코딩할 수 없다)."""
    real_run = subprocess.run
    captured = {}

    def spy_run(cmd, **kwargs):
        # run_job 이 encoding 을 실제로 넘겼는지 여부만으로 결과가 갈리게 한다.
        # PYTHONUTF8=1 로 테스트를 돌리면(이 프로젝트의 표준 실행법) 파이썬 자체의
        # 기본 인코딩이 이미 UTF-8 이 되어 있어서, run_job 이 encoding kwarg 를
        # 빠뜨려도 우연히 성공해버려 이 테스트가 회귀를 못 잡는다. 그래서 run_job 이
        # 직접 넘기지 않은 경우에만 이 프로젝트가 실제로 물렸던 cp949 로 강제한다.
        kwargs.setdefault("encoding", "cp949")
        completed = real_run(cmd, **kwargs)
        captured["completed"] = completed
        return completed

    monkeypatch.setattr("image_lab.tibo.subprocess.run", spy_run)
    monkeypatch.setattr("image_lab.tibo._NODE_EXECUTABLE", sys.executable)

    skill_root = _write_fake_node_script(
        tmp_path,
        'sys.stdout.buffer.write("한글 출력 테스트 — 이모지 포함 🎉🔥한국어".encode("utf-8"))\n'
        "sys.stdout.buffer.flush()",
    )

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    result = run_job(job, tmp_path, skill_root=skill_root)

    assert result.images == ()
    assert "한글" in captured["completed"].stdout
    assert "🎉" in captured["completed"].stdout


def test_run_job_replaces_invalid_utf8_bytes_instead_of_raising(tmp_path, monkeypatch):
    """cp949 로 인코딩된 '한글' 바이트(0xc7 0xd1 0xb1 0xdb)는 UTF-8 로는 잘못된 바이트열이다.
    errors="replace" 가 빠지면 UnicodeDecodeError 로 죽는다 — 대신 대체 문자(U+FFFD)로
    치환되어 예외 없이 통과해야 한다."""
    real_run = subprocess.run
    captured = {}

    def spy_run(cmd, **kwargs):
        # 위 테스트와 같은 이유로, encoding 이 빠졌을 때의 실제 기본값을 PYTHONUTF8
        # 모드와 무관하게 cp949 로 고정한다 (errors 는 명시하지 않는 한 파이썬 자체가
        # 항상 "strict" 이므로 별도 처리가 필요 없다).
        kwargs.setdefault("encoding", "cp949")
        completed = real_run(cmd, **kwargs)
        captured["completed"] = completed
        return completed

    monkeypatch.setattr("image_lab.tibo.subprocess.run", spy_run)
    monkeypatch.setattr("image_lab.tibo._NODE_EXECUTABLE", sys.executable)

    skill_root = _write_fake_node_script(
        tmp_path,
        "sys.stdout.buffer.write(bytes([0xc7, 0xd1, 0xb1, 0xdb]))\n"
        "sys.stdout.buffer.flush()",
    )

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "512x512", "output_dir": "out"}
    result = run_job(job, tmp_path, skill_root=skill_root)  # 예외 없이 통과해야 한다

    assert result.images == ()
    assert "�" in captured["completed"].stdout


# -- 장당 분할 실행 ------------------------------------------------------------
#
# god-tibo 의 워커 풀은 Promise.all 이라 재시도도 개별 실패 허용도 없다:
#     await Promise.all(Array.from({length: count}, () => runWorker()))
# 4장 중 1장의 응답 스트림이 이미지 없이 끝나면 배치 전체가 죽고 exit 1 이 된다 —
# **먼저 성공한 3장의 크레딧도 함께 날아간다.** 장당 job 으로 쪼개면 실패가 그
# 장에서 멈춘다. 총 크레딧은 같다.


def test_split_run_keeps_the_images_that_succeeded(tmp_path):
    """한 장이 실패해도 나머지는 건진다."""
    from image_lab.tibo import run_job_resilient

    calls = []

    def inner(job, job_dir, skill_root=None, dry_run=False):
        calls.append(job["output_dir"])
        if len(calls) == 2:
            raise RuntimeError("The response stream completed without an image_generation_call result.")
        out = Path(job_dir) / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / "frame-000.png"
        p.write_bytes(b"x")
        return RunResult(images=(p,), manifest={}, warnings=())

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "8x8", "output_dir": "generated", "batch_size": 4}
    res = run_job_resilient(job, tmp_path, _run=inner)
    assert len(res.images) == 3, "성공한 장까지 버렸습니다"
    assert len(calls) == 4, "장마다 한 번씩 불러야 합니다"
    assert any("4장 중 1장" in w for w in res.warnings), res.warnings


def test_split_run_sends_one_image_per_job(tmp_path):
    """쪼갠 job 은 한 장짜리여야 한다 — 안 그러면 쪼갠 의미가 없다."""
    from image_lab.tibo import run_job_resilient

    seen = []

    def inner(job, job_dir, skill_root=None, dry_run=False):
        seen.append((job["batch_size"], job["output_dir"]))
        out = Path(job_dir) / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / "frame-000.png"
        p.write_bytes(b"x")
        return RunResult(images=(p,), manifest={}, warnings=())

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "8x8", "output_dir": "generated", "batch_size": 3}
    run_job_resilient(job, tmp_path, _run=inner)
    assert [b for b, _ in seen] == [1, 1, 1]
    assert len({d for _, d in seen}) == 3, "장마다 출력 폴더가 달라야 서로 덮어쓰지 않습니다"


def test_split_run_fails_honestly_when_every_image_fails(tmp_path):
    """전부 실패했는데 빈 성공으로 보고하지 않는다."""
    from image_lab.tibo import run_job_resilient

    def inner(job, job_dir, skill_root=None, dry_run=False):
        raise RuntimeError("모델이 응답하지 않았습니다")

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "8x8", "output_dir": "generated", "batch_size": 2}
    with pytest.raises(RuntimeError) as exc:
        run_job_resilient(job, tmp_path, _run=inner)
    assert "모두" in str(exc.value) or "전부" in str(exc.value)
    assert "모델이 응답하지 않았습니다" in str(exc.value), "원인을 지웠습니다"


def test_split_run_does_not_split_a_single_image(tmp_path):
    """한 장짜리는 쪼갤 것이 없다 — 폴더 이름을 괜히 바꾸지 않는다."""
    from image_lab.tibo import run_job_resilient

    seen = []

    def inner(job, job_dir, skill_root=None, dry_run=False):
        seen.append(job["output_dir"])
        out = Path(job_dir) / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / "frame-000.png"
        p.write_bytes(b"x")
        return RunResult(images=(p,), manifest={}, warnings=())

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "8x8", "output_dir": "generated", "batch_size": 1}
    run_job_resilient(job, tmp_path, _run=inner)
    assert seen == ["generated"]


def test_split_run_passes_dry_run_through_without_splitting(tmp_path):
    """dry-run 은 크레딧을 안 쓰므로 쪼갤 이유가 없다."""
    from image_lab.tibo import run_job_resilient

    seen = []

    def inner(job, job_dir, skill_root=None, dry_run=False):
        seen.append((job["batch_size"], dry_run))
        return RunResult(images=(), manifest={}, warnings=())

    job = {"prompt": "x", "detail_level": 2, "size_mode": "controllable",
           "target_size": "8x8", "output_dir": "generated", "batch_size": 4}
    run_job_resilient(job, tmp_path, dry_run=True, _run=inner)
    assert seen == [(4, True)]
