import math

import pytest
from PIL import Image

from image_lab.chroma import ERASE_RADIUS, choose_key, pad_to_square, unpad


def solid(size, rgb):
    return Image.new("RGB", size, rgb)


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def test_choose_key_avoids_subject_colour_on_red_image():
    # 순수 빨강 피사체 — magenta(거리 255)보다 먼 키를 골라야 한다.
    choice = choose_key(solid((64, 64), (255, 0, 0)))
    assert choice.min_subject_distance > ERASE_RADIUS
    assert choice.clears_erase_radius is True
    assert dist(choice.rgb, (255, 0, 0)) == pytest.approx(choice.min_subject_distance)


def test_choose_key_beats_naive_complement_on_midtone():
    """god-tibo 의 255-mean 보색이 무력해지는 케이스에서 우리는 안전한 키를 고른다."""
    grey = (128, 128, 128)
    complement = (255 - grey[0], 255 - grey[1], 255 - grey[2])
    assert dist(complement, grey) < 2.0  # 보색이 피사체와 사실상 같은 색

    choice = choose_key(solid((64, 64), grey))
    assert choice.min_subject_distance > ERASE_RADIUS
    assert choice.clears_erase_radius is True


def test_choose_key_reports_all_candidates_with_scores():
    choice = choose_key(solid((32, 32), (10, 200, 10)))
    assert len(choice.candidates) >= 3
    for c in choice.candidates:
        assert {"name", "hex", "score", "min_subject_distance", "clears_erase_radius"} <= set(c)
    # 채택된 키가 후보 목록에 있어야 한다.
    assert any(c["name"] == choice.name for c in choice.candidates)


def test_choose_key_flags_when_no_candidate_clears():
    """모든 후보색이 실제로 등장하는 이미지 — 안전한 키가 없다고 정직하게 보고한다."""
    img = Image.new("RGB", (8, 8), (0, 0, 0))
    px = img.load()
    from image_lab.chroma import CANDIDATES

    for i, (_name, rgb) in enumerate(CANDIDATES):
        px[i, 0] = rgb
    choice = choose_key(img, sample_step=1)
    assert choice.clears_erase_radius is False


def test_choose_key_prefers_clearing_candidate_over_higher_raw_score():
    """설계문서 §14.3 의 핵심 결정 — 소거 반경(`clears_erase_radius`)을 넘기는 후보가
    하나라도 있으면, 원점수(`score`, 평균 거리)가 더 높아도 그 후보를 거부하고 안전한
    후보 중에서 고른다. 기존 테스트는 전부 단색 이미지라 최고 점수 후보가 이미 소거
    반경을 넘겨서(`pool = clearing if clearing else reports` → `pool = reports` 로
    바꿔도) 이 분기를 전혀 통과하지 않는다.

    녹색 필드에 마젠타에 바짝 붙은 작은 패치를 심어 구분한다: magenta 는 나머지
    화면(녹색)과 거리가 멀어 원점수가 가장 높지만, 그 패치 자체는 마젠타에 너무
    가까워 소거 반경을 못 넘긴다(clears_erase_radius=False). pool 을 reports 로
    바꾸면 이 magenta 가 뽑혀버린다."""
    img = Image.new("RGB", (64, 64), (0, 255, 0))
    px = img.load()
    for y in range(58, 64):
        for x in range(58, 64):
            px[x, y] = (255, 8, 247)  # 마젠타에 바짝 붙은 작은 패치

    choice = choose_key(img)
    assert choice.name == "blue"
    assert choice.clears_erase_radius is True

    # pool=reports 뮤턴트가 뽑았을 후보(원점수 최고인 magenta)가 실제로 소거 반경을
    # 못 넘기고, 그 원점수는 채택된 키보다 높다는 것도 함께 확인해 이 테스트가 그
    # pool 분기를 실제로 구분한다는 것을 보인다.
    magenta_report = next(c for c in choice.candidates if c["name"] == "magenta")
    assert magenta_report["clears_erase_radius"] is False
    assert magenta_report["score"] > choice.score, (
        "magenta 의 원점수가 선택된 키보다 높아야 이 테스트가 pool 분기를 실제로 구분한다"
    )


def test_pad_to_square_centres_content():
    img = solid((40, 100), (10, 20, 30))
    padded, (ox, oy) = pad_to_square(img, (255, 0, 255))
    assert padded.size == (100, 100)
    assert (ox, oy) == (30, 0)
    assert padded.getpixel((0, 0)) == (255, 0, 255)
    assert padded.getpixel((50, 50)) == (10, 20, 30)


def test_pad_to_square_is_noop_for_square_input():
    img = solid((50, 50), (7, 7, 7))
    padded, (ox, oy) = pad_to_square(img, (0, 255, 0))
    assert padded.size == (50, 50)
    assert (ox, oy) == (0, 0)


def test_pad_to_square_rejects_non_rgb_square_input():
    """RGB 가 아닌 입력은 조용히 변환하지 않고 명시적으로 거부한다."""
    img = Image.new("RGBA", (10, 10), (1, 2, 3, 4))
    with pytest.raises(ValueError):
        pad_to_square(img, (0, 255, 0))


def test_unpad_recovers_original_bytes_for_square_input():
    img = Image.new("RGB", (50, 50))
    px = img.load()
    for y in range(50):
        for x in range(50):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)

    padded, offsets = pad_to_square(img, (255, 0, 255))
    recovered = unpad(padded, offsets, img.size)

    assert recovered.size == img.size
    assert recovered.tobytes() == img.tobytes()


def test_unpad_recovers_original_bytes():
    img = Image.new("RGB", (37, 91))
    px = img.load()
    for y in range(91):
        for x in range(37):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)

    padded, offsets = pad_to_square(img, (255, 0, 255))
    recovered = unpad(padded, offsets, img.size)

    assert recovered.size == img.size
    assert recovered.tobytes() == img.tobytes()
