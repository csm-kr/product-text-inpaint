"""god-tibo 호출 계층 — job 빌드·검증과 실행·manifest 파싱을 담당한다.

합성과 마스크를 모른다. 입력은 프롬프트·레퍼런스 경로·사이즈 모드이고 출력은
생성된 PNG 경로 목록이다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# 설치본이 인식하는 job 키 전부. 이 밖의 키는 거부한다.
JOB_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "items",
        "references",
        "detail_level",
        "batch_size",
        "workers",
        "size_mode",
        "target_size",
        "output_dir",
        "gif",
    }
)

SIZE_MODES = frozenset({"invariant", "controllable"})

# 설치본 SKILL.md 기준 상한. GitHub README 는 64/32 로 기술하나 버전이 같아 불일치한다.
MAX_BATCH = 8

_TARGET_SIZE_RE = re.compile(r"^\d{2,5}x\d{2,5}$")


class JobError(ValueError):
    """job 이 god-tibo 계약을 만족하지 못할 때."""


def _is_form_present(value) -> bool:
    """프롬프트 형식이 실제로 있는지 검사한다 (빈 문자열·리스트 제외)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)  # 빈 문자열 거부
    if isinstance(value, list):
        return len(value) > 0  # 빈 리스트 거부
    return True


def validate_job(job: dict) -> None:
    """job 딕셔너리가 계약을 만족하는지 검사한다. 문제가 있으면 JobError."""
    unknown = set(job) - JOB_KEYS
    if unknown:
        raise JobError(f"god-tibo 가 모르는 job 키입니다: {sorted(unknown)}")

    # 프롬프트 형식: 정확히 하나만 있고, 비어있지 않아야 한다.
    forms = [k for k in ("prompt", "prompts", "items") if _is_form_present(job.get(k))]
    if len(forms) != 1:
        raise JobError(f"prompt / prompts / items 중 정확히 하나만 있어야 합니다 (현재: {forms})")

    mode = job.get("size_mode")
    if mode not in SIZE_MODES:
        raise JobError(f"size_mode 는 {sorted(SIZE_MODES)} 중 하나여야 합니다 (현재: {mode!r})")

    if mode == "controllable":
        target = job.get("target_size")
        if not target or not _TARGET_SIZE_RE.match(str(target)):
            raise JobError(f"controllable 은 'WxH' 형식의 target_size 가 필요합니다 (현재: {target!r})")
    else:
        # invariant 모드: references 는 필수, target_size 는 불가
        if not job.get("references"):
            raise JobError("invariant 는 reference 가 최소 하나 필요합니다.")
        if "target_size" in job:
            raise JobError("invariant 모드에서는 target_size 를 쓸 수 없습니다.")

    # batch_size 와 workers 는 bool 을 명시적으로 거부한다 (bool 은 int 의 서브클래스).
    for key in ("batch_size", "workers"):
        value = job.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= MAX_BATCH):
            raise JobError(f"{key} 는 1..{MAX_BATCH} 범위의 정수여야 합니다 (현재: {value!r})")

    if not job.get("output_dir"):
        raise JobError("output_dir 이 필요합니다.")


def build_job(
    *,
    prompt: str | None = None,
    prompts: list[str] | None = None,
    items: list[dict] | None = None,
    references: list[str] | tuple[str, ...] = (),
    detail_level: int = 2,
    batch_size: int | None = None,
    workers: int | None = None,
    size_mode: str,
    target_size: str | None = None,
    output_dir: str,
    gif: dict | None = None,
) -> dict:
    """검증을 통과한 job 딕셔너리를 만든다."""
    job: dict = {
        "detail_level": detail_level,
        "size_mode": size_mode,
        "output_dir": output_dir,
    }
    if prompt is not None:
        job["prompt"] = prompt
    if prompts is not None:
        job["prompts"] = list(prompts)
    if items is not None:
        job["items"] = list(items)
    if references:
        job["references"] = list(references)
    if target_size is not None:
        job["target_size"] = target_size
    if gif is not None:
        job["gif"] = gif

    # 생성 수가 목록으로 정해지면 workers 를 거기에 맞춘다.
    count = None
    if prompts is not None:
        count = len(prompts)
    elif items is not None:
        count = len(items)
    elif batch_size is not None:
        count = batch_size

    if batch_size is not None:
        job["batch_size"] = batch_size
    if workers is not None:
        job["workers"] = workers
    elif count is not None:
        job["workers"] = min(count, MAX_BATCH)

    validate_job(job)
    return job


@dataclass(frozen=True)
class RunResult:
    """생성 결과 — 이미지 경로와 manifest, 그리고 그대로 전달할 경고."""

    images: tuple[Path, ...]
    manifest: dict
    warnings: tuple[str, ...] = field(default=())


# 실행 파일 이름을 상수로 뺐다 — 테스트가 실제 subprocess 를 sys.executable 등으로
# 치환해 인코딩 kwargs 를 진짜로 검증할 수 있도록 하는 지점이다. 기본값은 그대로 "node".
_NODE_EXECUTABLE = "node"


def default_skill_root() -> Path:
    """설치된 god-tibo 스킬 경로. 명시적 로컬 경로가 기본값보다 우선한다."""
    configured = os.environ.get("GOD_TIBO_SKILL_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex" / "skills" / "god-tibo-gpt-image2-skill"


def node_executable() -> str:
    """god-tibo 실행용 Node. 테스트용 상수와 환경변수 선택을 함께 지원한다."""
    return os.environ.get("GOD_TIBO_NODE") or _NODE_EXECUTABLE


# 알아본 실패만 바꿔 말한다. 모르는 실패에 그럴듯한 설명을 붙이면 원인을 찾는
# 사람을 엉뚱한 데로 보낸다 — 그런 경우엔 원문만 그대로 내보낸다.
_KNOWN_FAILURES = (
    (
        "image_generation_call",
        "모델이 이미지 대신 텍스트로 답했습니다 (거부했거나 응답 스트림이 끊겼습니다).\n"
        "god-tibo 는 여러 장을 동시에 만들다가 한 장이라도 이렇게 끝나면 배치 전체를\n"
        "버리고 멈춥니다 — 먼저 성공한 장들도 함께 사라집니다. 대개 일시적이니\n"
        "다시 시도해 보세요. 계속 같은 자리에서 실패하면 요청문을 바꿔 보세요.\n\n"
        "--- 원문 ---\n"
    ),
)


def _explain_failure(stderr: str) -> str:
    """알아본 실패면 한국어 설명을 앞에 붙인다. 원문은 지우지 않는다."""
    for needle, explanation in _KNOWN_FAILURES:
        if needle in (stderr or ""):
            return explanation
    return ""


def parse_manifest(manifest: dict) -> RunResult:
    """manifest 에서 이미지 경로와 경고를 뽑는다 (schema_version 2)."""
    if "images" not in manifest:
        # "images": [] (명시적 빈 배열)는 정상적인 0장 결과이지만, 키 자체가 없는 것은
        # god-tibo 버전 불일치나 manifest 손상을 뜻한다 — 조용히 빈 RunResult 로 넘기지 않는다.
        raise RuntimeError(
            "manifest 에 'images' 키가 없습니다 — god-tibo 버전이 다르거나 manifest 가 손상되었을 수 있습니다."
        )

    images: list[Path] = []
    warnings: list[str] = []

    for entry in sorted(manifest.get("images") or [], key=lambda e: e.get("index", 0)):
        path = entry.get("path")
        if path:
            images.append(Path(path))
        # backend 경고는 그대로 전달한다 (비공식 경로임을 숨기지 않는다).
        for w in entry.get("warnings") or []:
            if w not in warnings:
                warnings.append(w)
        check = entry.get("size_check") or {}
        if check.get("matches_expected") is False:
            expected = check.get("expected") or {}
            final = entry.get("final_size") or {}
            warnings.append(
                f"이미지 {entry.get('index')} 의 크기가 기대와 다릅니다: "
                f"기대 {expected.get('width')}x{expected.get('height')}, "
                f"실제 {final.get('width')}x{final.get('height')}"
            )

    return RunResult(images=tuple(images), manifest=manifest, warnings=tuple(warnings))


def run_job(
    job: dict,
    job_dir: Path,
    skill_root: Path | None = None,
    dry_run: bool = False,
    timeout: int = 900,
) -> RunResult:
    """job 을 파일로 쓰고 god-tibo 를 node 로 직접 실행한다.

    tmux·codex CLI 는 필요 없다 — `~/.codex/auth.json` 만 있으면 된다.
    """
    validate_job(job)
    # 절대경로로 고정한다 — subprocess 를 cwd=job_dir 로 실행하는데, job_dir 이
    # 상대경로면 --job 인자도 같은 상대 문자열이 되어 node 프로세스가 (이미
    # job_dir 로 바뀐) 자신의 cwd 기준으로 그 상대경로를 다시 해석해 이중으로
    # 어긋난다(실측: `--out ../ab-test/x` 처럼 흔한 상대경로에서 ENOENT 로 재현됨).
    job_dir = Path(job_dir).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / "job.json"
    # 인코딩을 명시한다 — 한국어 Windows 기본값(cp949)에 맡기면 프롬프트가 깨진다.
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    root = Path(skill_root) if skill_root else default_skill_root()
    # Node의 ESM 진입점 판정은 argv 경로와 import.meta.url을 비교한다. 스킬이
    # 프로젝트 디렉터리에 symlink로 등록되어 있으면 Node는 import 쪽만 realpath로
    # 바꿔 비교가 실패하고, exit 0인데 아무 일도 하지 않는 문제가 생긴다. 실행
    # 전에 실제 경로로 정규화해 어느 등록 방식에서도 같은 경로를 넘긴다.
    script = (root / "scripts" / "tibo-batch.mjs").resolve()
    if not script.is_file():
        raise RuntimeError(f"god-tibo 실행 스크립트를 찾을 수 없습니다: {script}")
    cmd = [node_executable(), str(script), "--job", str(job_path)]
    if dry_run:
        cmd.append("--dry-run")

    completed = subprocess.run(
        cmd,
        cwd=str(job_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",   # codex/훅 출력에 cp949 바이트가 섞여도 죽지 않는다.
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raw = (f"god-tibo 실행 실패 (exit {completed.returncode})\n"
               f"stdout: {completed.stdout}\nstderr: {completed.stderr}")
        raise RuntimeError(_explain_failure(completed.stderr) + raw)

    manifest_path = job_dir / job["output_dir"] / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"manifest 를 찾을 수 없습니다: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = parse_manifest(manifest)

    # exit 0 과 manifest 파싱 성공만으로 성공을 보장하지 않는다.
    # dry-run 이 아니라면 실제로 생성된 파일이 존재하고 비어있지 않은지 확인한다
    # (SKILL.md 의 자체 검증 규칙과 동일 — "모든 images[].path가 존재하고 0바이트가 아니어야 한다").
    # dry-run 은 god-tibo 가 최종 파일을 쓰지 않는다는 사실이 실측으로 확인됐으므로 이 검사에서 제외한다.
    if not dry_run:
        for path in result.images:
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(
                    f"생성 결과 파일이 없거나 비어 있습니다 — 성공으로 보고할 수 없습니다: {path}"
                )

    return result


def run_job_resilient(job: dict, job_dir: Path, *, skill_root: Path | None = None,
                      dry_run: bool = False, _run=run_job) -> RunResult:
    """batch_size 만큼 job 을 쪼개 **한 장씩** 돌린다.

    god-tibo 의 워커 풀에는 재시도도 개별 실패 허용도 없다:

        await Promise.all(Array.from({length: count}, () => runWorker()))

    4장 중 1장의 응답 스트림이 이미지 없이 끝나면 배치 전체가 죽고 exit 1 이
    된다 — **먼저 성공한 3장의 크레딧까지 함께 날아간다.** 실측한 실패다.

    장당 job 으로 쪼개면 실패가 그 장에서 멈춘다. 총 크레딧은 같고(같은 장수를
    만든다), 대신 프로세스를 N번 띄우는 비용이 붙는다 — 생성 자체가 수십 초
    걸리는 것에 비하면 무시할 만하다.

    한 장이라도 건지면 성공으로 보고하되 몇 장이 실패했는지 경고에 남긴다.
    전부 실패하면 원인을 모아 예외로 던진다 — 빈 성공으로 조용히 넘어가지 않는다.
    """
    n = int(job.get("batch_size") or 1)
    # dry-run 은 크레딧을 쓰지 않으므로 쪼갤 이유가 없다. 한 장짜리도 마찬가지다.
    if dry_run or n <= 1:
        return _run(job, job_dir, skill_root=skill_root, dry_run=dry_run)

    base = job["output_dir"]
    images: list[Path] = []
    warnings: list[str] = []
    failures: list[str] = []
    for i in range(n):
        # 출력 폴더를 장마다 나눈다 — 같은 폴더를 쓰면 frame-000.png 가 서로를
        # 덮어써서 쪼갠 의미가 사라진다.
        sub = dict(job, batch_size=1, workers=1, output_dir=f"{base}-{i:03d}")
        try:
            part = _run(sub, job_dir, skill_root=skill_root, dry_run=False)
        except Exception as exc:            # noqa: BLE001 — 어떤 실패든 그 장만 접는다
            failures.append(f"{i + 1}번째 장: {exc}")
            continue
        images.extend(part.images)
        warnings.extend(part.warnings)

    joined = "\n".join(failures)
    if not images:
        raise RuntimeError(f"{n}장을 모두 만들지 못했습니다.\n" + joined)
    if failures:
        warnings.append(
            f"{n}장 중 {len(failures)}장이 실패했습니다 — 나머지 {len(images)}장은 "
            f"그대로 씁니다.\n" + joined
        )
    # manifest 는 장마다 따로 있어 하나로 합칠 수 없다 — 쓰는 쪽이 images/warnings
    # 만 보므로 비워 둔다. 있는 척 한 장짜리를 넣으면 나머지를 감추는 셈이 된다.
    return RunResult(images=tuple(images), manifest={}, warnings=tuple(warnings))
