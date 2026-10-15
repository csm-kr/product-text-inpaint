import pytest

from image_lab.prompts import INPAINT_INSTRUCTION, build_inpaint_prompt, padding_note


def test_padding_note_states_exact_rgb():
    note = padding_note((255, 0, 255))
    assert "255" in note and "0" in note
    assert "Do NOT" in note or "do not" in note.lower()


def test_build_inpaint_prompt_places_instruction_before_request():
    out = build_inpaint_prompt("make the hat blue")
    assert out.index(INPAINT_INSTRUCTION) < out.index("make the hat blue")


def test_build_inpaint_prompt_includes_padding_note_when_given():
    out = build_inpaint_prompt("make the hat blue", pad_rgb=(0, 255, 0))
    assert "0,255,0" in out.replace(" ", "")


def test_build_inpaint_prompt_omits_padding_note_when_absent():
    out = build_inpaint_prompt("make the hat blue")
    assert "Padding color is RGB" not in out


def test_build_inpaint_prompt_preserves_korean_request():
    out = build_inpaint_prompt("모자를 파란색으로 바꿔줘")
    assert "모자를 파란색으로 바꿔줘" in out


def test_instruction_forbids_full_regeneration():
    lowered = INPAINT_INSTRUCTION.lower()
    assert "do not regenerate the whole image" in lowered
    assert "local edit" in lowered


# 엣지 케이스 테스트들 — 조용히 통과시키지 않고 명시적으로 거부한다.


def test_build_inpaint_prompt_rejects_empty_user_request():
    """사용자 요청이 비어 있으면 ValueError 를 던진다."""
    with pytest.raises(ValueError):
        build_inpaint_prompt("")


def test_build_inpaint_prompt_rejects_whitespace_only_request():
    """공백만 들어 있는 요청은 요청이 아니므로 거부한다."""
    with pytest.raises(ValueError):
        build_inpaint_prompt("   ")


def test_build_inpaint_prompt_rejects_whitespace_only_request_with_tabs():
    """탭·개행 등 모든 공백을 거부한다."""
    with pytest.raises(ValueError):
        build_inpaint_prompt("\t\n  \r\n")


def test_padding_note_rejects_non_tuple_list():
    """RGB 가 리스트면 거부한다."""
    with pytest.raises(TypeError):
        padding_note([255, 0, 255])


def test_padding_note_rejects_non_tuple_string():
    """RGB 가 문자열이면 거부한다."""
    with pytest.raises(TypeError):
        padding_note("255,0,255")


def test_padding_note_rejects_non_integer_components():
    """RGB 컴포넌트가 정수가 아니면 거부한다."""
    with pytest.raises(TypeError):
        padding_note((255.5, 0, 255))


def test_padding_note_rejects_too_few_components():
    """RGB 가 3개 미만이면 거부한다."""
    with pytest.raises((ValueError, TypeError)):
        padding_note((255, 0))


def test_padding_note_rejects_too_many_components():
    """RGB 가 3개 초과면 거부한다."""
    with pytest.raises((ValueError, TypeError)):
        padding_note((255, 0, 255, 128))


def test_padding_note_rejects_out_of_range_components():
    """각 컴포넌트가 0..255 범위를 벗어나면 거부한다."""
    with pytest.raises(ValueError):
        padding_note((256, 0, 255))

    with pytest.raises(ValueError):
        padding_note((255, -1, 255))

    with pytest.raises(ValueError):
        padding_note((255, 0, 300))


def test_build_inpaint_prompt_has_instruction_once_only():
    """조립한 프롬프트에 지시문이 정확히 한 번 나타나야 한다."""
    out = build_inpaint_prompt("make the hat blue", pad_rgb=(128, 128, 128))
    count = out.count(INPAINT_INSTRUCTION)
    assert count == 1, f"지시문이 {count}번 나타났습니다 (1번이어야 함)"


def test_padding_note_includes_exact_rgb_numbers():
    """패딩 안내에 RGB 숫자들이 정확히 포함되어야 한다."""
    note = padding_note((123, 45, 67))
    # 공백 제거 후 "123,45,67" 형식으로 나타나야 한다.
    assert "123" in note and "45" in note and "67" in note
    # "RGB(123,45,67)" 또는 유사 형식을 기대한다.
    assert "RGB(" in note or "rgb" in note.lower()
