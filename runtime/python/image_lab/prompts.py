"""프롬프트 조립.

god-tibo job 에는 `system_prompt` 필드가 없다. 지시문을 `prompt` 앞에 이어 붙인다.
원문은 `references/prompts.md` 에 보관한다.
"""
from __future__ import annotations

INPAINT_INSTRUCTION = """\
[Role]
You are a high-precision local image editing and inpainting engine.

[Editing Mode]
This is a precise local edit, not a full regeneration.
Modify only the user-specified target region. Do not alter unrelated areas.

[Base Image Priority]
Image 1 is the absolute source of truth for the final output.
Preserve its background, composition, framing, perspective, lighting, colour tone,
atmosphere, and scene structure.

[Target Change Rule]
The requested target region must change clearly and visibly.
Do not preserve the old target when the user asked to replace, remove, or modify it.
If the instruction is removal, remove it completely.

[Edge Blending Rule]
Preserve the outer edge and surrounding area so the edit connects naturally to the
original scene. Blend boundaries naturally in colour, brightness, contrast, saturation,
lighting direction, texture, grain, and sharpness.
Avoid visible seams, halos, blur mismatch, patch artifacts, or colour shifts.

[Non-Target Preservation]
Everything outside the requested target must remain unchanged as much as possible.
Do not add, remove, move, restyle, or reinterpret anything not explicitly requested.
Preserve all original text unless explicitly requested otherwise.
Preserve original character identity, proportions, pose, and appearance unless
explicitly requested to change them.

[No Full Reinterpretation]
Do not regenerate the whole image. Do not redesign the scene.
Do not perform global style transfer.

[Quality Standard]
Match the original resolution, style, detail level, and overall image quality exactly.
The result must look like the same original image with only the requested local edit
applied."""


def padding_note(rgb: tuple[int, int, int]) -> str:
    """패딩 색을 모델에 명시한다 — 참조 구현의 핵심 개선점.

    Args:
        rgb: RGB 색을 나타내는 정수 튜플. 각 값은 0..255 범위여야 한다.

    Returns:
        모델에 패딩 색을 설명하는 안내문.

    Raises:
        TypeError: rgb 가 튜플이 아니거나 컴포넌트가 정수가 아니면.
        ValueError: 컴포넌트 개수가 3이 아니거나 범위를 벗어나면.
    """
    # 타입 검증: 튜플이어야 한다 (리스트·문자열은 불허).
    if not isinstance(rgb, tuple):
        raise TypeError(
            f"rgb 은 tuple 이어야 합니다. {type(rgb).__name__} 이 주어졌습니다."
        )

    # 길이 검증: 정확히 3개.
    if len(rgb) != 3:
        raise ValueError(f"rgb 은 정확히 3개 컴포넌트여야 합니다. {len(rgb)}개가 주어졌습니다.")

    # 각 컴포넌트가 0..255 범위 정수여야 한다.
    for i, component in enumerate(rgb):
        if not isinstance(component, int) or isinstance(component, bool):
            raise TypeError(
                f"rgb[{i}] 은 정수여야 합니다. {type(component).__name__} 이 주어졌습니다."
            )
        if not 0 <= component <= 255:
            raise ValueError(
                f"rgb[{i}] 은 0..255 범위여야 합니다. {component}가 주어졌습니다."
            )

    r, g, b = rgb
    return (
        f"The content is centered on a square canvas with solid-colour padding.\n"
        f"Padding color is RGB({r},{g},{b}). Do NOT modify any pixel with this color.\n"
        f"Treat the padding region as background; never draw new content over it.\n"
        f"Do not expand, extend, or bleed any content into the padding area."
    )


def build_inpaint_prompt(
    user_request: str, pad_rgb: tuple[int, int, int] | None = None
) -> str:
    """지시문 + (선택) 패딩 안내 + 사용자 요청 순으로 이어 붙인다.

    Args:
        user_request: 사용자가 요청하는 편집 지시. 비어 있거나 공백만 포함하면 안 된다.
        pad_rgb: (선택) RGB 패딩 색. 주어지면 padding_note 를 프롬프트에 포함한다.

    Returns:
        모델에 보낼 전체 프롬프트.

    Raises:
        ValueError: user_request 가 비어 있거나 공백만 포함하면.
        TypeError: pad_rgb 가 잘못된 형식이면.
        ValueError: pad_rgb 의 값이 범위를 벗어나면.
    """
    # 사용자 요청 검증: 비어 있거나 공백만 포함하면 거부한다.
    if not user_request or not user_request.strip():
        raise ValueError("user_request 는 비어 있으면 안 됩니다.")

    blocks = [INPAINT_INSTRUCTION]
    if pad_rgb is not None:
        # padding_note 가 검증을 수행한다.
        blocks.append(padding_note(pad_rgb))
    blocks.append(f"[User Edit Request]\n{user_request}")
    return "\n\n".join(blocks)
