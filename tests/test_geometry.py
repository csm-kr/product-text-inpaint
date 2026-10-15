import pytest

from image_lab.geometry import (
    LARGE_BOX_FRACTION,
    BoxError,
    area_fraction,
    box_contains_image,
    clamp_box,
    context_margin,
    expand_box,
)


def test_clamp_box_passes_valid_box_through():
    assert clamp_box((10, 20, 30, 40), 100, 100) == (10, 20, 30, 40)


def test_clamp_box_swaps_reversed_coordinates():
    assert clamp_box((30, 40, 10, 20), 100, 100) == (10, 20, 30, 40)


def test_clamp_box_clips_to_image_bounds():
    assert clamp_box((-10, -5, 200, 300), 100, 80) == (0, 0, 100, 80)


def test_clamp_box_rejects_degenerate_box():
    with pytest.raises(BoxError):
        clamp_box((10, 10, 10, 50), 100, 100)
    with pytest.raises(BoxError):
        clamp_box((10, 10, 50, 10), 100, 100)


def test_clamp_box_rejects_box_entirely_outside():
    with pytest.raises(BoxError):
        clamp_box((200, 200, 300, 300), 100, 100)


def test_context_margin_uses_shorter_side():
    # 짧은 변 = 40, 25% = 10
    assert context_margin((0, 0, 100, 40)) == 10


def test_context_margin_is_at_least_one_pixel():
    assert context_margin((0, 0, 2, 2), ratio=0.01) == 1


def test_expand_box_grows_by_margin():
    assert expand_box((20, 20, 40, 40), 5, 100, 100) == (15, 15, 45, 45)


def test_expand_box_clamps_at_image_edge():
    assert expand_box((0, 0, 20, 20), 5, 100, 100) == (0, 0, 25, 25)


def test_area_fraction_computes_ratio():
    assert area_fraction((0, 0, 50, 50), 100, 100) == pytest.approx(0.25)


def test_large_box_fraction_threshold_is_half():
    assert LARGE_BOX_FRACTION == 0.5


def test_box_exactly_equal_to_the_image_contains_it():
    """딱 맞는 박스는 포함이다 — 넓어지는 곳이 없다는 판정은 엔진의 몫이다."""
    assert box_contains_image((0, 0, 100, 80), 100, 80) is True


def test_box_larger_on_every_side_contains_the_image():
    assert box_contains_image((-10, -20, 110, 100), 100, 80) is True


def test_box_one_pixel_short_on_the_right_does_not_contain_the_image():
    assert box_contains_image((0, 0, 99, 80), 100, 80) is False


def test_box_starting_inside_does_not_contain_the_image():
    assert box_contains_image((1, 0, 100, 80), 100, 80) is False


def test_box_entirely_inside_does_not_contain_the_image():
    assert box_contains_image((10, 10, 50, 50), 100, 80) is False
