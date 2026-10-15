import json

import numpy as np
import pytest
from PIL import Image

from image_lab.inpaint import (
    build_contact_sheet,
    prepare_crop,
    recover_patch,
    run_inpaint,
)
from image_lab.tibo import RunResult


def gradient(size=(200, 160)):
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = (x % 256, y % 256, (x * 2 + y) % 256)
    return img


def test_prepare_crop_returns_square_padded_image():
    img = gradient()
    padded, expanded, box, offsets, choice = prepare_crop(img, (60, 50, 100, 110))
    assert padded.size[0] == padded.size[1], "god-tibo 가 패딩을 건너뛰려면 정사각이어야 한다"
    assert expanded[0] <= 60 and expanded[1] <= 50
    assert expanded[2] >= 100 and expanded[3] >= 110
    assert box == (60, 50, 100, 110)
    assert isinstance(choice.name, str) and len(choice.rgb) == 3


def test_recover_patch_returns_inner_box_sized_image():
    img = gradient()
    padded, expanded, box, offsets, _choice = prepare_crop(img, (60, 50, 100, 110))
    # 생성 결과를 흉내낸다 — 크기는 같고 내용만 다르다.
    fake = Image.new("RGB", padded.size, (9, 9, 9))
    patch = recover_patch(fake, offsets, (expanded[2] - expanded[0], expanded[3] - expanded[1]),
                          expanded, box)
    assert patch.size == (box[2] - box[0], box[3] - box[1])


def test_run_inpaint_preserves_pixels_outside_box(tmp_path):
    """1차 계획의 핵심 인수 기준 — 박스 밖은 원본과 바이트 동일."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (60, 50, 100, 110)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        paths = []
        for i in range(2):
            p = out / f"frame-{i:03d}.png"
            Image.new("RGB", ref.size, (255, 0, 0)).save(p)   # 완전히 다른 내용
            paths.append(p)
        return RunResult(images=tuple(paths), manifest={"images": []}, warnings=())

    result = run_inpaint(
        image_path=src_path, box=box, request="turn it red",
        out_dir=tmp_path / "out", batch_size=2, runner=fake_runner,
    )

    assert len(result.outputs) == 2
    for path in result.outputs:
        out_img = Image.open(path).convert("RGB")
        assert out_img.size == src.size
        for region in [(0, 0, 200, 50), (0, 110, 200, 160), (0, 0, 60, 160), (100, 0, 200, 160)]:
            assert out_img.crop(region).tobytes() == src.crop(region).tobytes(), \
                f"박스 밖 영역 {region} 이 변경됨"


def test_run_inpaint_warns_on_large_box(tmp_path):
    src = gradient((100, 100))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (1, 2, 3)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(
        image_path=src_path, box=(5, 5, 95, 95), request="x",
        out_dir=tmp_path / "out", batch_size=1, runner=fake_runner,
    )
    assert any("전체 재생성" in w for w in result.warnings)


def test_run_inpaint_writes_job_with_invariant_and_reference(tmp_path):
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    captured = {}

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        captured["job"] = job
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (1, 2, 3)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="모자를 파랗게",
                out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)

    job = captured["job"]
    assert job["size_mode"] == "invariant"
    assert len(job["references"]) == 1
    assert "모자를 파랗게" in job["prompt"]
    assert "Padding color is RGB" in job["prompt"]


def test_run_inpaint_warns_when_no_safe_chroma_key_exists(tmp_path, monkeypatch):
    """안전한 키가 없으면 조용히 넘어가지 않고 경고한다 (설계문서 9절)."""
    from image_lab import inpaint as inpaint_mod
    from image_lab.chroma import KeyChoice

    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    unsafe = KeyChoice(
        name="magenta", rgb=(255, 0, 255), score=10.0,
        min_subject_distance=12.0, clears_erase_radius=False, candidates=(),
    )
    monkeypatch.setattr(inpaint_mod, "choose_key", lambda *a, **k: unsafe)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (1, 2, 3)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                         out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)
    assert any("피사체 색과 가깝" in w for w in result.warnings)


def test_build_contact_sheet_creates_numbered_grid(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"o{i}.png"
        Image.new("RGB", (40, 30), (i * 60, 10, 10)).save(p)
        paths.append(p)
    sheet = build_contact_sheet(paths, tmp_path / "sheet.png", columns=2)
    assert sheet.is_file()
    img = Image.open(sheet)
    assert img.width >= 80 and img.height >= 60


def test_build_contact_sheet_on_empty_list_returns_none(tmp_path):
    assert build_contact_sheet([], tmp_path / "sheet.png") is None


# --- 아래는 Global Constraints 의 "No Silent Fallback" 과 최상위 계약(박스 밖 바이트
# 동일)을 실제로 검증하기 위해 브리프 샘플 코드를 넘어 추가한 테스트다. ---


def test_recover_patch_roundtrip_matches_original_box_exactly_for_identity_generation():
    """무변화 모델(생성 결과 = 보낸 크롭 그대로)이라면 회수된 패치는 원본 박스와
    바이트까지 동일해야 한다. crop→pad→generate→unpad→resize→extract 사슬에
    off-by-one 이 있으면 여기서 어긋난다."""
    img = gradient((200, 160))
    box = (60, 50, 100, 110)
    padded, expanded, box, offsets, _choice = prepare_crop(img, box)
    crop_size = (expanded[2] - expanded[0], expanded[3] - expanded[1])
    patch = recover_patch(padded, offsets, crop_size, expanded, box)
    assert patch.size == (box[2] - box[0], box[3] - box[1])
    # bytes 를 비교식에 직접 넣지 않고 불리언으로 먼저 받는다 — pytest 의 assertion
    # rewriting 이 실패 시 두 큰 bytes 를 통째로 diff 하려 들면(difflib) 수십 KB
    # 만 돼도 체감상 멈춘 것처럼 느려질 수 있다(관찰: 800x600 케이스에서 실제로
    # 재현됨). 먼저 bool 로 접어 두면 실패해도 즉시 assert False 로 끝난다.
    matches = patch.tobytes() == img.crop(box).tobytes()
    assert matches


def test_recover_patch_roundtrip_matches_original_box_exactly_for_portrait_offset_a():
    """위 왕복 테스트는 이 박스 세트에서 y_off 가 항상 0 이라 세로 패딩 경로
    (unpad 의 y 축 오프셋)를 한 번도 실행하지 않는다 — offsets=(0,7) 이 나오는
    이 박스로 세로 오프셋 부호 반전·축 스왑류의 버그를 잡는다."""
    img = gradient((200, 160))
    box = (60, 3, 120, 60)
    padded, expanded, box, offsets, _choice = prepare_crop(img, box)
    assert offsets == (0, 7)
    crop_size = (expanded[2] - expanded[0], expanded[3] - expanded[1])
    patch = recover_patch(padded, offsets, crop_size, expanded, box)
    assert patch.size == (box[2] - box[0], box[3] - box[1])
    matches = patch.tobytes() == img.crop(box).tobytes()  # 큰 bytes diff 회피 (위 설명 참고)
    assert matches


def test_recover_patch_roundtrip_matches_original_box_exactly_for_portrait_offset_b():
    """같은 이유로 더 큰 이미지·다른 종횡비에서도 세로 오프셋(0,29)이 실제로 나오는
    구성을 하나 더 고정한다."""
    img = gradient((800, 600))
    box = (100, 90, 301, 233)
    padded, expanded, box, offsets, _choice = prepare_crop(img, box)
    assert offsets == (0, 29)
    crop_size = (expanded[2] - expanded[0], expanded[3] - expanded[1])
    patch = recover_patch(padded, offsets, crop_size, expanded, box)
    assert patch.size == (box[2] - box[0], box[3] - box[1])
    matches = patch.tobytes() == img.crop(box).tobytes()  # 큰 bytes diff 회피 (위 설명 참고)
    assert matches


def test_run_inpaint_places_landmark_pixel_at_exact_recovered_coordinate(tmp_path):
    """균일한 색으로만 채운 스텁은 좌표 정보가 없어 offset 스왑·마진 무시 같은 기하
    버그를 잡지 못한다(예: patch=source.crop(box) 인 무편집 파이프라인도 통과해
    버린다). 여기서는 "생성" 이미지의 알려진 좌표에 랜드마크 픽셀을 심어, run_inpaint
    가 실제로 사용하는 crop_size 계산과 recover_patch 호출 경로를 거쳐 정확히
    예상 좌표에 나타나는지 확인한다. 세로 패딩 오프셋(y_off=7)이 있는 박스를 써서
    가로축만 검증하던 기존 테스트들의 사각지대도 함께 없앤다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (60, 3, 120, 60)  # offsets=(0,7) — 세로 패딩이 실제로 발생하는 박스

    _padded, expanded, box, offsets, _choice = prepare_crop(src, box)
    ox, oy = offsets
    bw, bh = box[2] - box[0], box[3] - box[1]
    local = (bw // 2, bh // 2)  # 박스 중심 — feather 램프(3px)에서 충분히 떨어진 지점
    landmark_final = (box[0] + local[0], box[1] + local[1])
    landmark_ref = (
        landmark_final[0] - expanded[0] + ox,
        landmark_final[1] - expanded[1] + oy,
    )
    LANDMARK = (10, 220, 30)
    BG = (230, 30, 190)  # 배경과 색 차이를 크게 둬 feather(하드 페이스트) 모드를 강제한다

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        gen = Image.new("RGB", ref.size, BG)
        gen.putpixel(landmark_ref, LANDMARK)
        p = out / "frame-000.png"
        gen.save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)

    out_img = Image.open(result.outputs[0]).convert("RGB")
    assert out_img.getpixel(landmark_final) == LANDMARK, \
        "랜드마크가 예상 좌표에 없다 — crop_size/offsets 계산이 어긋났을 수 있다"
    assert out_img.getpixel(landmark_final) != src.getpixel(landmark_final), \
        "랜드마크 위치가 원본과 같은 값이면 우연히 통과한 것일 수 있다"
    box_before = np.asarray(src.crop(box))
    box_after = np.asarray(out_img.crop(box))
    assert not np.array_equal(box_before, box_after), \
        "박스 안쪽이 전혀 바뀌지 않았다 — no-op 파이프라인(patch=source.crop(box))일 수 있다"


def test_run_inpaint_box_touching_top_left_corner_is_not_shrunk(tmp_path):
    """이미지 좌상단 모서리에 닿은 박스가 마진 확장 때문에 조용히 줄어들면 안 된다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (0, 0, 40, 40)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (200, 10, 10)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)

    assert result.box == box, "모서리에 닿은 박스가 확장 과정에서 변형되면 안 된다"
    out_img = Image.open(result.outputs[0]).convert("RGB")
    assert out_img.size == src.size
    for region in [(40, 0, 200, 160), (0, 40, 40, 160)]:
        assert out_img.crop(region).tobytes() == src.crop(region).tobytes(), \
            f"박스 밖 영역 {region} 이 변경됨"


def test_run_inpaint_box_touching_bottom_right_corner_is_not_shrunk(tmp_path):
    """이미지 우하단(전체 너비/높이에 닿음)에 닿은 박스도 동일하게 보존돼야 한다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (160, 120, 200, 160)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (10, 200, 10)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)

    assert result.box == box
    out_img = Image.open(result.outputs[0]).convert("RGB")
    for region in [(0, 0, 160, 120), (0, 0, 200, 120), (0, 0, 160, 160)]:
        assert out_img.crop(region).tobytes() == src.crop(region).tobytes(), \
            f"박스 밖 영역 {region} 이 변경됨"


def test_run_inpaint_raises_when_runner_returns_zero_images(tmp_path):
    """러너가 이미지를 하나도 못 돌려주면 조용히 빈 성공으로 보고하지 않고 예외를 낸다."""
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        return RunResult(images=(), manifest={"images": []}, warnings=())

    with pytest.raises(RuntimeError):
        run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                    out_dir=tmp_path / "out", batch_size=2, runner=fake_runner)


def test_run_inpaint_warns_when_fewer_images_than_batch_size(tmp_path):
    """요청한 batch_size 보다 적게 돌아오면 조용히 넘어가지 않고 경고한다."""
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (1, 2, 3)).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())  # batch_size=4 인데 1장뿐

    result = run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                          out_dir=tmp_path / "out", batch_size=4, runner=fake_runner)
    assert len(result.outputs) == 1
    assert any("적은" in w for w in result.warnings)


def test_run_inpaint_warns_and_recovers_when_generated_image_has_wrong_size(tmp_path):
    """어드버서리얼 러너: 모델이 요청과 다른 크기의 이미지를 돌려준다.

    합성 전에 리사이즈로 복구하되(그렇지 않으면 unpad 좌표가 어긋난다), 조용히
    넘어가지 않고 경고를 남긴다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (60, 50, 100, 110)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / "frame-000.png"
        Image.new("RGB", (10, 10), (5, 250, 5)).save(p)  # 참조 크기와 다른 크기
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)

    out_img = Image.open(result.outputs[0]).convert("RGB")
    assert out_img.size == src.size
    for region in [(0, 0, 200, 50), (0, 110, 200, 160), (0, 0, 60, 160), (100, 0, 200, 160)]:
        assert out_img.crop(region).tobytes() == src.crop(region).tobytes(), \
            f"박스 밖 영역 {region} 이 변경됨"
    assert any("크기" in w for w in result.warnings)
    # 위 두 단언(경고 문구·박스 밖 보존)은 리사이즈 복구 자체가 사라져도
    # (`generated.resize(...)` → `pass`) 그대로 통과한다 — 그 경우 recover_patch 가
    # 10x10 이미지에 박스 크기로 crop 을 시도해 캔버스 밖은 검게 채워지고, 그 "검은
    # 패치"가 박스 안에 그대로 합성돼도 위 단언들은 못 잡는다. 박스 중심(페더 폭 3px
    # 밖이라 항상 패치 색 그대로인 지점)이 실제로 스텁의 채움색을 담고 있는지 직접
    # 확인해 "복구됨"의 나머지 절반을 검증한다.
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    recovered_center = out_img.getpixel((cx, cy))
    assert recovered_center == (5, 250, 5), (
        f"박스 중심이 생성 채움색과 달라 리사이즈 복구가 깨진 것일 수 있다: {recovered_center}"
    )


def test_run_inpaint_blends_box_border_instead_of_hard_pasting(tmp_path):
    """`merged, _state = blend(source, patch, box)` 를 `source.copy()` + `paste()` 로
    바꿔도(모드 선택·seamlessClone·feathering 을 모두 버림) 기존 테스트는 대부분 그대로
    통과한다 — 박스 밖 보존과 "박스 안이 바뀌었는지"만 보고, "어떻게 바뀌었는지"는
    보지 않기 때문이다. 박스 경계 픽셀이 패치 색 그대로(하드 페이스트)가 아니라
    원본과 패치 사이의 중간값(feather 램프)을 실제로 갖는지 확인해 inpaint ↔ composite
    이음매를 검증한다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (60, 50, 100, 110)
    FILL = (10, 10, 250)  # 원본과 색 차이가 커서 feather 모드가 강제로 뽑힌다

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, FILL).save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)
    out_img = Image.open(result.outputs[0]).convert("RGB")

    x1, y1, x2, y2 = box
    border = out_img.getpixel((x1, (y1 + y2) // 2))
    src_border = src.getpixel((x1, (y1 + y2) // 2))
    assert border != FILL, (
        "박스 경계 픽셀이 패치 색 그대로다 — 하드 페이스트(무피더링)로 합성했을 수 있다"
    )
    assert border != src_border, "박스 경계가 전혀 안 바뀌었다 — 합성 자체가 안 된 것일 수 있다"


def test_run_inpaint_survives_padding_colour_drift(tmp_path):
    """모델이 요청한 키 색을 정확히 유지하지 않고 드리프트해도(예: #FFFF00 로 보냈는데
    #FBFB04 로 돌아오는 식), 복구는 색이 아니라 오프셋 좌표로 동작하므로 결과에
    영향이 없어야 한다.

    이미지 전체를 한 색으로 채운 스텁은 패딩과 콘텐츠가 구별되지 않아 드리프트
    민감도를 전혀 검증하지 못한다(드리프트가 있든 없든 이미지가 균일하므로 통과).
    그래서 여기서는 실제 패딩 픽셀(choose_key 가 고른 색과 정확히 일치하는 픽셀)만
    드리프트된 색으로 바꾸고 나머지 콘텐츠는 그대로 둔다.

    이 그래디언트 이미지·이 박스에 대해 choose_key 가 실제로 고르는 키는
    yellow(#FFFF00) 다(magenta 가 아니다) — 아래에서 그 전제를 직접 확인한다."""
    src = gradient((200, 160))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    box = (60, 50, 100, 110)

    _padded, _expanded, box, _offsets, choice = prepare_crop(src, box)
    assert choice.name == "yellow" and choice.rgb == (255, 255, 0)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0]).convert("RGB")
        arr = np.array(ref)
        pad_mask = np.all(arr == np.array(choice.rgb), axis=-1)
        arr[pad_mask] = (251, 251, 4)  # god-tibo 실측 사례와 같은 방식의 드리프트
        p = out / "frame-000.png"
        Image.fromarray(arr, mode="RGB").save(p)
        return RunResult(images=(p,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=box, request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)
    out_img = Image.open(result.outputs[0]).convert("RGB")
    for region in [(0, 0, 200, 50), (0, 110, 200, 160), (0, 0, 60, 160), (100, 0, 200, 160)]:
        assert out_img.crop(region).tobytes() == src.crop(region).tobytes(), \
            f"박스 밖 영역 {region} 이 변경됨"
    # 패딩만 드리프트됐을 뿐 박스 안 콘텐츠는 원본과 사실상 동일해야 한다
    # (seamless 합성의 반올림 오차 정도만 허용 — 리뷰어 실측: 최대차 1).
    box_diff = np.abs(
        np.asarray(out_img.crop(box), dtype=np.int16)
        - np.asarray(src.crop(box), dtype=np.int16)
    )
    assert box_diff.max() <= 1, f"박스 안 콘텐츠가 패딩 드리프트에 영향을 받았다 (최대차 {box_diff.max()})"


def test_run_inpaint_passes_through_runner_warnings(tmp_path):
    """RunResult.warnings 가 InpaintResult.warnings 까지 실제로 전달되는지 검증한다."""
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        ref = Image.open(job["references"][0])
        p = out / "frame-000.png"
        Image.new("RGB", ref.size, (1, 2, 3)).save(p)
        return RunResult(images=(p,), manifest={"images": []},
                          warnings=("백엔드 경고: unsupported private backend",))

    result = run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner)
    assert any("unsupported private backend" in w for w in result.warnings)


def test_run_inpaint_dry_run_skips_composition_without_raising(tmp_path):
    """dry_run=True 는 배관(잡 빌드·검증) 경로만 확인한다. god-tibo 는 dry-run 에서
    실제 파일을 쓰지 않으므로(tibo.run_job 실측 확인), 존재하지 않을 수 있는 경로를
    열어 합성하려 들면 안 되고 조용히 가짜 결과를 만들어도 안 된다."""
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)
    captured = {}

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        captured["dry_run"] = dry_run
        captured["job"] = job
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        never_written = out / "frame-000.png"  # dry-run 실측과 동일하게 실제로 만들지 않는다
        return RunResult(images=(never_written,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner,
                          dry_run=True)

    assert captured["dry_run"] is True
    assert captured["job"]["size_mode"] == "invariant"
    assert result.outputs == ()
    assert result.contact_sheet is None


def test_run_inpaint_dry_run_result_warns_that_nothing_was_generated(tmp_path):
    """dry_run=True 결과는 outputs=() 만으로는 "이것이 dry-run 이었다"는 사실을 결과
    자체가 말하지 않는다 — CLI 가 이 결과만 보고 정직한 메시지를 만들 수 있도록,
    InpaintResult.warnings 에 dry-run 이었다는 한국어 경고를 남긴다."""
    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        out = job_dir / job["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        never_written = out / "frame-000.png"
        return RunResult(images=(never_written,), manifest={"images": []}, warnings=())

    result = run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                          out_dir=tmp_path / "out", batch_size=1, runner=fake_runner,
                          dry_run=True)

    assert any("dry-run" in w or "실제 생성" in w for w in result.warnings), \
        "dry-run 이었다는 사실이 warnings 에 없다"


def test_build_contact_sheet_handles_single_image(tmp_path):
    p = tmp_path / "o0.png"
    Image.new("RGB", (40, 30), (10, 10, 10)).save(p)
    sheet = build_contact_sheet([p], tmp_path / "sheet.png", columns=2)
    assert sheet.is_file()
    img = Image.open(sheet)
    assert img.width >= 40 and img.height >= 30


def test_build_contact_sheet_handles_mismatched_sizes(tmp_path):
    """썸네일 크기가 서로 다르면 큰 쪽 기준으로 셀을 잡아야 한다(작은 쪽이 잘리면 안 된다)."""
    p1 = tmp_path / "o0.png"
    p2 = tmp_path / "o1.png"
    Image.new("RGB", (20, 20), (10, 10, 10)).save(p1)
    Image.new("RGB", (80, 60), (20, 20, 20)).save(p2)
    sheet = build_contact_sheet([p1, p2], tmp_path / "sheet.png", columns=2)
    img = Image.open(sheet)
    assert img.width >= 80 * 2
    assert img.height >= 60


def test_run_inpaint_rejects_batch_size_above_global_limit(tmp_path):
    """Global Constraints — batch_size 는 1..8. 러너에 도달하기 전에 job 빌드
    단계(tibo.validate_job)에서 이미 거부돼야 한다."""
    from image_lab.tibo import JobError

    src = gradient((120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    def fake_runner(job, job_dir, skill_root=None, dry_run=False, timeout=900):
        raise AssertionError("job 검증에서 걸러졌어야 하며 러너가 호출되면 안 된다")

    with pytest.raises(JobError):
        run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                    out_dir=tmp_path / "out", batch_size=9, runner=fake_runner)


# --- 비 RGB 원본 거부 — composite.blend / chroma.pad_to_square 와 같은 원칙 ---
# (리뷰: run_inpaint 의 Image.open(...).convert("RGB") 가 두 모듈의 방어를 우회한다)


def _must_not_be_called(*a, **k):
    raise AssertionError("비 RGB 입력은 러너 호출 전에 거부돼야 한다")


def test_run_inpaint_rejects_rgba_source(tmp_path):
    """RGBA 원본을 조용히 RGB 로 변환하면 알파 정보가 사라지고, composite.blend·
    chroma.pad_to_square 가 이미 거부하는 것과 같은 입력이 파이프라인 한 단계 앞에서
    새어나간다. 명시적으로 거부해야 한다."""
    src = Image.new("RGBA", (120, 120), (200, 200, 200, 0))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    with pytest.raises(ValueError):
        run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                    out_dir=tmp_path / "out", batch_size=1, runner=_must_not_be_called)


def test_run_inpaint_rejects_l_source(tmp_path):
    """L(그레이스케일) 원본도 같은 이유로 거부한다."""
    src = Image.new("L", (120, 120), 128)
    src_path = tmp_path / "src.png"
    src.save(src_path)

    with pytest.raises(ValueError):
        run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                    out_dir=tmp_path / "out", batch_size=1, runner=_must_not_be_called)


def test_run_inpaint_rejects_p_source(tmp_path):
    """P(팔레트) 원본도 같은 이유로 거부한다."""
    src = Image.new("P", (120, 120))
    src_path = tmp_path / "src.png"
    src.save(src_path)

    with pytest.raises(ValueError):
        run_inpaint(image_path=src_path, box=(20, 20, 60, 60), request="x",
                    out_dir=tmp_path / "out", batch_size=1, runner=_must_not_be_called)
