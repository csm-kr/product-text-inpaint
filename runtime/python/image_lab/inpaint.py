"""인페인팅 오케스트레이션.

파이프라인:
  1. 박스를 클램프하고 컨텍스트 마진만큼 넓힌다.
  2. 넓힌 영역만 잘라낸다 — 모델은 박스 바깥을 보지 않는다.
  3. 안전한 크로마 키를 골라 정사각으로 패딩한다 (god-tibo 는 정사각 입력에
     대해 자체 보색 패딩을 건너뛴다).
  4. size_mode=invariant 로 생성한다.
  5. 패딩을 제거하고 마진을 걷어내 **안쪽 박스만** 회수한다.
  6. 원본에 합성한다 — 박스 밖은 바이트 그대로 유지된다.

`unpad`/`recover_patch` 는 패딩 오프셋(좌표)만으로 복구한다 — god-tibo 가 요청한
키 색을 그대로 지키지 않고 드리프트해도(#FF00FF 로 보냈는데 #FB04CB 로 돌아오는
식) 복구 정확도에는 영향이 없다. 색을 신뢰하는 지점은 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from image_lab.chroma import KeyChoice, choose_key, pad_to_square, unpad
from image_lab.composite import blend
from image_lab.geometry import (
    LARGE_BOX_FRACTION,
    Box,
    area_fraction,
    clamp_box,
    context_margin,
    expand_box,
)
from image_lab.prompts import build_inpaint_prompt
from image_lab.tibo import RunResult, build_job, run_job_resilient


@dataclass(frozen=True)
class InpaintResult:
    """인페인팅 한 건의 결과 — 산출물 경로와 그 과정에서 나온 모든 경고."""

    outputs: tuple[Path, ...]
    contact_sheet: Path | None
    box: Box
    key_name: str
    warnings: tuple[str, ...] = field(default=())


def prepare_crop(
    image: Image.Image, box: Box, margin_ratio: float = 0.25
) -> tuple[Image.Image, Box, Box, tuple[int, int], KeyChoice]:
    """생성에 보낼 정사각 이미지를 만든다."""
    w, h = image.size
    box = clamp_box(box, w, h)
    margin = context_margin(box, margin_ratio)
    expanded = expand_box(box, margin, w, h)
    crop = image.convert("RGB").crop(expanded)
    choice = choose_key(crop)
    padded, offsets = pad_to_square(crop, choice.rgb)
    return padded, expanded, box, offsets, choice


def recover_patch(
    generated: Image.Image,
    offsets: tuple[int, int],
    crop_size: tuple[int, int],
    expanded: Box,
    box: Box,
) -> Image.Image:
    """생성 결과에서 패딩과 마진을 걷어내고 안쪽 박스만 돌려준다."""
    crop = unpad(generated.convert("RGB"), offsets, crop_size)
    inner = (
        box[0] - expanded[0],
        box[1] - expanded[1],
        box[2] - expanded[0],
        box[3] - expanded[1],
    )
    return crop.crop(inner)


def build_contact_sheet(
    paths: list[Path], out_path: Path, columns: int = 2
) -> Path | None:
    """번호를 붙인 격자 시트를 만든다 — 사용자가 번호로 고른다.

    산출물이 없으면(빈 리스트) 시트를 만들 대상이 없다는 뜻이므로 None 을 돌려준다
    — 빈 파일을 만들어 "시트가 있다"고 조용히 거짓 보고하지 않는다.
    """
    if not paths:
        return None
    thumbs = [Image.open(p).convert("RGB") for p in paths]
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    label_h = 22
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, thumb in enumerate(thumbs):
        r, c = divmod(i, columns)
        x = c * cell_w
        y = r * (cell_h + label_h)
        sheet.paste(thumb, (x, y))
        draw.text((x + 6, y + cell_h + 4), f"[{i + 1}] {paths[i].name}", fill=(230, 230, 230))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def run_inpaint(
    *,
    image_path: Path,
    box: Box,
    request: str,
    out_dir: Path,
    batch_size: int = 4,
    detail_level: int = 2,
    margin_ratio: float = 0.25,
    skill_root: Path | None = None,
    dry_run: bool = False,
    runner=run_job_resilient,
) -> InpaintResult:
    """인페인팅 한 건을 끝까지 수행한다."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    opened = Image.open(image_path)
    if opened.mode != "RGB":
        # composite.blend·chroma.pad_to_square 는 이미 RGB 가 아닌 입력을 거부한다
        # (알파·팔레트 정보가 조용히 사라지는 것을 막기 위해서다). 여기서 미리
        # convert("RGB") 를 하면 그 방어가 실행 경로에서 도달 불가능해지므로,
        # 같은 원칙으로 여기서도 명시적으로 거부한다.
        raise ValueError(
            f"run_inpaint 은 RGB 모드 원본만 받습니다: 입력 모드 {opened.mode!r}"
        )
    source = opened.convert("RGB")

    padded, expanded, box, offsets, choice = prepare_crop(source, box, margin_ratio)

    warnings: list[str] = []
    frac = area_fraction(box, *source.size)
    if frac > LARGE_BOX_FRACTION:
        warnings.append(
            f"박스가 이미지의 {frac:.0%} 를 차지합니다 — 사실상 전체 재생성입니다. "
            "그대로 진행합니다."
        )
    if not choice.clears_erase_radius:
        # 안전한 후보가 하나도 없었다 — 조용히 넘어가지 않는다.
        warnings.append(
            f"선택한 크로마 키 {choice.name} 이 피사체 색과 가깝습니다 "
            f"(최소 거리 {choice.min_subject_distance:.1f}). "
            "패딩이 피사체와 섞여 결과가 나빠질 수 있습니다."
        )

    ref_path = out_dir / "crop-padded.png"
    padded.save(ref_path)

    job = build_job(
        prompt=build_inpaint_prompt(request, pad_rgb=choice.rgb),
        references=[str(ref_path.resolve())],
        detail_level=detail_level,
        batch_size=batch_size,
        size_mode="invariant",
        output_dir="generated",
    )
    run: RunResult = runner(job, out_dir, skill_root=skill_root, dry_run=dry_run)
    warnings.extend(run.warnings)

    if dry_run:
        # dry-run 은 god-tibo 가 최종 파일을 쓰지 않는다는 사실이 실측으로 확인됐다
        # (tibo.run_job 참고) — manifest 의 이미지 경로가 실제로는 없을 수 있으므로,
        # 여기서 열어 합성을 시도하면 안 된다. 잡 빌드·검증 경로만 확인하고 끝낸다.
        # outputs=() 만으로는 호출자가 "이것이 dry-run 이었다"는 사실을 알 수 없으므로
        # (예외를 던지지 않은 정상 경로다) warnings 에 명시적으로 남긴다.
        warnings.append("[dry-run] 실제 생성을 수행하지 않았습니다 — 크레딧이 사용되지 않았습니다.")
        return InpaintResult(
            outputs=(),
            contact_sheet=None,
            box=box,
            key_name=choice.name,
            warnings=tuple(warnings),
        )

    if not run.images:
        # 아무것도 생성되지 않았다 — 빈 outputs 로 조용히 "성공"을 보고하지 않는다.
        raise RuntimeError(
            "생성 결과가 없습니다 — 러너가 이미지를 하나도 반환하지 않았습니다."
        )
    if len(run.images) < batch_size:
        warnings.append(
            f"요청한 배치 크기 {batch_size}장보다 적은 {len(run.images)}장만 생성되었습니다."
        )

    crop_size = (expanded[2] - expanded[0], expanded[3] - expanded[1])
    outputs: list[Path] = []
    for i, gen_path in enumerate(run.images):
        generated = Image.open(gen_path).convert("RGB")
        if generated.size != padded.size:
            # 모델이 요청과 다른 크기를 돌려준 경우 — 리사이즈로 복구하되(그렇지
            # 않으면 unpad 의 오프셋 좌표가 어긋난다) 조용히 넘어가지 않고 알린다.
            warnings.append(
                f"{i}번째 생성 이미지 크기가 예상과 다릅니다: "
                f"기대 {padded.size[0]}x{padded.size[1]}, "
                f"실제 {generated.size[0]}x{generated.size[1]} — 리사이즈해서 사용합니다."
            )
            generated = generated.resize(padded.size, Image.LANCZOS)
        patch = recover_patch(generated, offsets, crop_size, expanded, box)
        merged, _state = blend(source, patch, box)
        dest = out_dir / f"inpaint-{i:03d}.png"
        merged.save(dest)
        outputs.append(dest)

    sheet = build_contact_sheet(outputs, out_dir / "contact-sheet.png")
    return InpaintResult(
        outputs=tuple(outputs),
        contact_sheet=sheet,
        box=box,
        key_name=choice.name,
        warnings=tuple(warnings),
    )
