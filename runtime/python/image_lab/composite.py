"""합성 — 박스 안만 교체하고 박스 밖은 원본 바이트를 보존한다.

**최상위 계약**: 반환 이미지의 박스 밖 픽셀은 원본과 바이트 단위로 동일하다.
`cv2.seamlessClone` 이 ROI 밖을 건드리지 않는다고 알려져 있으나, 계약을 구현으로
보장하기 위해 결과의 박스 영역만 잘라 **원본 사본에 붙인다.**

블렌드 모드는 색 변화량으로 자동 선택한다:
  · 변화가 작다  -> seamlessClone(NORMAL_CLONE). 이음새를 지운다.
  · 변화가 크다  -> feather alpha. Poisson 은 gradient domain 에서 박스 안쪽 색을
                   주변에 맞춰 끌고 가므로, 의도한 강한 색 변경(빨강->파랑)이 죽는다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from image_lab.geometry import Box

# 박스 평균색 차이가 이 값을 넘으면 생성 색을 살리기 위해 feather 로 간다.
MODE_THRESHOLD = 40.0
# feather 램프 폭 (px).
FEATHER_PX = 3


@dataclass(frozen=True)
class BlendState:
    """블렌드 결정 — 시퀀스에서 프레임 간 재계산을 막기 위해 고정해 재사용한다."""

    mode: str
    mean_delta: float


def mean_rgb(image: Image.Image, box: Box) -> tuple[float, float, float]:
    """박스 영역의 평균 RGB.

    RGB 모드 입력만 받는다. 다른 모드를 조용히 변환하면 알파·팔레트 정보가 소리
    없이 사라지므로, 대신 명시적으로 거부한다 (`chroma.pad_to_square` 와 같은 원칙).
    """
    if image.mode != "RGB":
        raise ValueError(f"mean_rgb 는 RGB 모드만 받습니다: 입력 모드 {image.mode!r}")
    arr = np.asarray(image.crop(box), dtype=np.float64)
    return tuple(float(v) for v in arr.reshape(-1, 3).mean(axis=0))


def choose_blend(
    source: Image.Image,
    patch: Image.Image,
    box: Box,
    threshold: float = MODE_THRESHOLD,
) -> BlendState:
    """원본 박스와 패치의 평균색 차이로 모드를 정한다."""
    x1, y1, x2, y2 = box
    sized = patch if patch.size == (x2 - x1, y2 - y1) else patch.resize(
        (x2 - x1, y2 - y1), Image.LANCZOS
    )
    a = np.asarray(source.convert("RGB").crop(box), dtype=np.float64).reshape(-1, 3).mean(axis=0)
    b = np.asarray(sized.convert("RGB"), dtype=np.float64).reshape(-1, 3).mean(axis=0)
    delta = float(np.sqrt(((a - b) ** 2).sum()))
    return BlendState(mode="feather" if delta > threshold else "seamless", mean_delta=delta)


def _feather_mask(size: tuple[int, int], feather: int) -> Image.Image:
    """가장자리에서 0 으로 떨어지는 알파 마스크."""
    w, h = size
    mask = Image.new("L", size, 0)
    inset = min(feather, max(0, (min(w, h) - 1) // 2))
    draw = ImageDraw.Draw(mask)
    draw.rectangle([inset, inset, w - 1 - inset, h - 1 - inset], fill=255)
    if inset > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=inset))
    return mask


def blend(
    source: Image.Image,
    patch: Image.Image,
    box: Box,
    state: BlendState | None = None,
    feather: int = FEATHER_PX,
) -> tuple[Image.Image, BlendState]:
    """패치를 박스에 합성한다. 박스 밖은 원본 바이트가 그대로 남는다."""
    if source.mode != "RGB":
        # source 는 보존해야 할 원본이다 — 조용히 변환하면 RGBA·팔레트 등에서
        # 픽셀당 바이트 수가 달라져 "박스 밖 바이트 동일" 계약이 깨진다.
        raise ValueError(f"blend 의 source 는 RGB 모드만 받습니다: 입력 모드 {source.mode!r}")
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    base = source.convert("RGB")
    # patch 는 생성된 콘텐츠이지 보존해야 할 원본이 아니므로 변환해도 안전하다.
    sized = patch.convert("RGB")
    if sized.size != (bw, bh):
        sized = sized.resize((bw, bh), Image.LANCZOS)

    if state is None:
        state = choose_blend(base, sized, box)

    if state.mode == "seamless":
        src_bgr = cv2.cvtColor(np.array(sized), cv2.COLOR_RGB2BGR)
        dst_bgr = cv2.cvtColor(np.array(base), cv2.COLOR_RGB2BGR)
        mask = np.zeros((bh, bw), dtype=np.uint8)
        if bh > 2 and bw > 2:
            # 경계 1px 를 제외해 seamlessClone 의 엣지 아티팩트를 피한다.
            mask[1 : bh - 1, 1 : bw - 1] = 255
        else:
            mask[:, :] = 255
        center = (x1 + bw // 2, y1 + bh // 2)
        try:
            out_bgr = cv2.seamlessClone(src_bgr, dst_bgr, mask, center, cv2.NORMAL_CLONE)
        except cv2.error:
            # 박스가 너무 작거나 경계에 걸린 경우 — 조용히 실패하지 말고 모드를 밝힌다.
            result = base.copy()
            result.paste(sized, (x1, y1))
            return result, BlendState(mode="paste", mean_delta=state.mean_delta)
        blended = Image.fromarray(cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB))
        region = blended.crop(box)
    elif state.mode == "paste":
        # 재사용된 state 가 "paste" 면 그 의미 그대로 무피더링 하드 페이스트를 한다 —
        # feather 갈래로 흘러 들어가 조용히 다른 결과를 내면 안 된다.
        region = sized
    else:
        region = base.crop(box)
        region.paste(sized, (0, 0), _feather_mask((bw, bh), feather))

    # 박스 밖 바이트 동일성은 여기서 보장된다 — 원본 사본에 박스만 붙인다.
    result = base.copy()
    result.paste(region, (x1, y1))
    return result, state
