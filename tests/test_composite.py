import pytest
from PIL import Image

from image_lab.composite import (
    FEATHER_PX,
    MODE_THRESHOLD,
    BlendState,
    blend,
    choose_blend,
    mean_rgb,
)


def gradient(size=(64, 64)):
    """결정론적 배경 — 픽셀 보존 위반을 잡아내기 좋다."""
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = (x * 3 % 256, y * 5 % 256, (x + y) * 7 % 256)
    return img


def test_mean_rgb_reads_box_region_only():
    img = Image.new("RGB", (10, 10), (0, 0, 0))
    for y in range(2, 6):
        for x in range(2, 6):
            img.putpixel((x, y), (100, 200, 40))
    assert mean_rgb(img, (2, 2, 6, 6)) == pytest.approx((100.0, 200.0, 40.0))


def test_mean_rgb_rejects_non_rgb_input():
    """RGB 가 아닌 입력은 조용히 변환하지 않고 명시적으로 거부한다."""
    img = Image.new("RGBA", (10, 10), (1, 2, 3, 4))
    with pytest.raises(ValueError):
        mean_rgb(img, (2, 2, 6, 6))


def test_outside_box_is_byte_identical_after_blend():
    """최상위 계약 — 박스 밖은 원본과 바이트 단위로 동일해야 한다."""
    src = gradient((80, 80))
    box = (20, 20, 60, 60)
    patch = Image.new("RGB", (40, 40), (255, 128, 0))

    out, _state = blend(src, patch, box)

    assert out.size == src.size
    # 박스 밖 네 영역을 각각 비교한다.
    for region in [(0, 0, 80, 20), (0, 60, 80, 80), (0, 0, 20, 80), (60, 0, 80, 80)]:
        assert out.crop(region).tobytes() == src.crop(region).tobytes(), f"영역 {region} 이 변경됨"


def test_blend_rejects_non_rgb_source():
    """source 는 보존해야 할 원본이다 — 조용히 변환하면 계약이 깨지므로 거부한다."""
    src = Image.new("RGBA", (40, 40), (10, 20, 30, 255))
    patch = Image.new("RGB", (10, 10), (200, 30, 30))
    with pytest.raises(ValueError):
        blend(src, patch, (10, 10, 20, 20))


def test_choose_blend_picks_feather_for_large_colour_change():
    src = Image.new("RGB", (40, 40), (10, 10, 10))
    patch = Image.new("RGB", (20, 20), (250, 250, 250))
    state = choose_blend(src, patch, (10, 10, 30, 30))
    assert state.mode == "feather"
    assert state.mean_delta > MODE_THRESHOLD


def test_choose_blend_picks_seamless_for_small_colour_change():
    src = Image.new("RGB", (40, 40), (120, 120, 120))
    patch = Image.new("RGB", (20, 20), (125, 122, 118))
    state = choose_blend(src, patch, (10, 10, 30, 30))
    assert state.mode == "seamless"
    assert state.mean_delta < MODE_THRESHOLD


def test_supplied_state_is_reused_not_recomputed():
    """시퀀스 플리커 방지 — 같은 state 를 주면 결과가 결정론적으로 같다."""
    src = gradient((60, 60))
    patch = Image.new("RGB", (20, 20), (200, 30, 30))
    box = (20, 20, 40, 40)

    forced = BlendState(mode="feather", mean_delta=123.0)
    a, sa = blend(src, patch, box, state=forced)
    b, sb = blend(src, patch, box, state=forced)

    assert sa == forced and sb == forced
    assert a.tobytes() == b.tobytes()


def test_supplied_paste_state_does_hard_paste_not_feather():
    """state.mode="paste" 를 주면 feather 갈래로 새지 않고 무피더링 하드 페이스트를 한다."""
    src = gradient((60, 60))
    patch = Image.new("RGB", (20, 20), (200, 30, 30))
    box = (20, 20, 40, 40)

    state = BlendState(mode="paste", mean_delta=999.0)
    out, out_state = blend(src, patch, box, state=state)

    assert out_state == state
    # 박스 가장자리 픽셀도 패치 색 그대로여야 한다 (feather 라면 배경 쪽으로 흐려진다).
    assert out.getpixel((20, 20)) == (200, 30, 30)
    assert out.getpixel((39, 39)) == (200, 30, 30)
    # 박스 밖은 여전히 보존된다.
    for region in [(0, 0, 60, 20), (0, 40, 60, 60), (0, 0, 20, 60), (40, 0, 60, 60)]:
        assert out.crop(region).tobytes() == src.crop(region).tobytes(), f"영역 {region} 이 변경됨"


def test_patch_is_resized_when_size_mismatches_box():
    src = gradient((60, 60))
    patch = Image.new("RGB", (10, 10), (5, 200, 5))
    out, _ = blend(src, patch, (20, 20, 40, 40))
    assert out.size == (60, 60)


def test_tiny_box_falls_back_to_paste_without_raising():
    """1x1 박스를 강제로 seamless 경로에 태워 실제 cv2.error 폴백을 검증한다.

    자동 선택(choose_blend)에 맡기면 흰 패치 대 그라디언트의 색 차이가 커서
    feather 가 뽑혀 seamlessClone 자체가 호출되지 않는다 — 그래서는 폴백 경로가
    검증되지 않으므로, state 를 강제로 "seamless" 로 주어 실제로 cv2.error 가
    나는지 확인한다.
    """
    src = gradient((40, 40))
    patch = Image.new("RGB", (1, 1), (255, 255, 255))
    box = (10, 10, 11, 11)
    out, state = blend(src, patch, box, state=BlendState(mode="seamless", mean_delta=0.0))
    assert out.size == (40, 40)
    assert state.mode == "paste"  # cv2.error 를 삼키지 않고 모드로 밝힌다.
    # 박스 밖 네 영역 모두 여전히 보존된다.
    for region in [(0, 0, 40, 10), (0, 11, 40, 40), (0, 0, 10, 40), (11, 0, 40, 40)]:
        assert out.crop(region).tobytes() == src.crop(region).tobytes(), f"영역 {region} 이 변경됨"


def test_feather_default_is_three_pixels():
    assert FEATHER_PX == 3


def test_feather_blend_produces_intermediate_border_values_not_hard_edge():
    """`FEATHER_PX == 3` 확인과 `choose_blend` 모드 문자열 확인만으로는 feather 알파
    램프 자체가 실제로 동작하는지 전혀 검증되지 않는다 — feather 분기 본문을
    `region = sized`(무피더링 하드 페이스트)로 바꿔도 그 두 단언은 그대로 통과한다.
    박스 경계 픽셀이 원본·패치 사이의 실제 중간값을 갖고, 페더 폭(3px) 밖 안쪽은
    패치 색 그대로인지 직접 확인한다."""
    src = Image.new("RGB", (60, 60), (0, 0, 0))
    patch = Image.new("RGB", (40, 40), (255, 255, 255))
    box = (10, 10, 50, 50)
    state = BlendState(mode="feather", mean_delta=999.0)
    out, _state = blend(src, patch, box, state=state)

    border = out.getpixel((10, 30))[0]  # 박스 왼쪽 경계
    interior = out.getpixel((20, 30))[0]  # 페더 폭 밖 — 패치 색 그대로여야 한다
    outside = out.getpixel((9, 30))[0]  # 박스 밖 — 원본 그대로여야 한다

    assert outside == 0
    assert 0 < border < 255, f"경계 픽셀이 원본·패치 중간값이 아니다(하드 에지 의심): {border}"
    assert interior > 250, f"페더 폭 밖 안쪽은 패치 색에 가까워야 한다: {interior}"
