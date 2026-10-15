"""크로마 키 선택과 정사각 패딩.

god-tibo 의 `invariant` 는 평균 RGB 의 보색으로 패딩한다. 평균이 중간톤이면 보색이
피사체와 겹쳐 무력해진다 — (128,128,128) 의 보색은 (127,127,127) 이다.

대신 후보 키를 놓고 **피사체 픽셀과의 최소 거리**로 점수를 매겨 고른다. 소거 반경
안에 드는 후보는 안전한 대안이 있으면 거부한다.

god-tibo 는 정사각 입력에 대해 패딩을 생략하므로, 여기서 정사각으로 만들어 넘기면
키 통제권을 우리가 갖는다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

# 후보 키 — 색상환에서 서로 멀리 떨어진 포화색.
CANDIDATES: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("magenta", (255, 0, 255)),
    ("green", (0, 255, 0)),
    ("cyan", (0, 255, 255)),
    ("yellow", (255, 255, 0)),
    ("blue", (0, 0, 255)),
    ("red", (255, 0, 0)),
)

# 이 반경 안의 피사체 색은 키잉에서 함께 지워진다.
ERASE_RADIUS = 96.0


@dataclass(frozen=True)
class KeyChoice:
    """키 선택 결과 — 어떤 근거로 골랐는지 그대로 남긴다."""

    name: str
    rgb: tuple[int, int, int]
    score: float
    min_subject_distance: float
    clears_erase_radius: bool
    candidates: tuple[dict, ...]


def _to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _pixels(image: Image.Image, sample_step: int) -> np.ndarray:
    """샘플링한 피사체 픽셀 배열 (N, 3) float."""
    arr = np.asarray(image.convert("RGB"), dtype=np.float64)
    arr = arr[::sample_step, ::sample_step]
    return arr.reshape(-1, 3)


def choose_key(
    image: Image.Image,
    erase_radius: float = ERASE_RADIUS,
    sample_step: int = 2,
) -> KeyChoice:
    """피사체와 가장 멀리 떨어진 키를 고른다.

    `score` 는 평균 거리(클수록 안전), `min_subject_distance` 는 가장 가까운 피사체
    픽셀까지의 거리다. 소거 반경을 넘기는 후보가 하나라도 있으면 그중에서, 없으면
    전체 후보 중에서 `score` 가 가장 큰 후보를 고른다 (동점이면 `min_subject_distance`
    로 가른다). 후자의 경우 `clears_erase_radius=False` 로 정직하게 보고한다
    (조용히 넘어가지 않는다).
    """
    pixels = _pixels(image, sample_step)
    reports: list[dict] = []
    for name, rgb in CANDIDATES:
        diff = pixels - np.asarray(rgb, dtype=np.float64)
        distances = np.sqrt((diff * diff).sum(axis=1))
        min_d = float(distances.min())
        reports.append(
            {
                "name": name,
                "rgb": rgb,
                "hex": _to_hex(rgb),
                "score": float(distances.mean()),
                "min_subject_distance": min_d,
                "clears_erase_radius": min_d > erase_radius,
            }
        )

    clearing = [r for r in reports if r["clears_erase_radius"]]
    pool = clearing if clearing else reports
    key = max(pool, key=lambda r: (r["score"], r["min_subject_distance"]))

    return KeyChoice(
        name=key["name"],
        rgb=key["rgb"],
        score=key["score"],
        min_subject_distance=key["min_subject_distance"],
        clears_erase_radius=key["clears_erase_radius"],
        candidates=tuple(reports),
    )


def pad_to_square(
    image: Image.Image, rgb: tuple[int, int, int]
) -> tuple[Image.Image, tuple[int, int]]:
    """긴 변에 맞춰 정사각 캔버스 중앙에 배치한다. 이미 정사각이면 그대로 둔다.

    RGB 모드 입력만 받는다 (이 파이프라인은 항상 RGB 로 변환한 뒤 호출한다).
    다른 모드를 조용히 변환하면 알파·팔레트 정보가 소리 없이 사라지므로, 대신
    명시적으로 거부한다.
    """
    if image.mode != "RGB":
        raise ValueError(
            f"pad_to_square 는 RGB 모드만 받습니다: 입력 모드 {image.mode!r}"
        )
    w, h = image.size
    side = max(w, h)
    x_off = (side - w) // 2
    y_off = (side - h) // 2
    if side == w and side == h:
        return image.copy(), (0, 0)
    canvas = Image.new("RGB", (side, side), rgb)
    canvas.paste(image, (x_off, y_off))
    return canvas, (x_off, y_off)


def unpad(
    image: Image.Image, offsets: tuple[int, int], size: tuple[int, int]
) -> Image.Image:
    """`pad_to_square` 로 붙인 여백을 제거해 원래 크기를 복원한다."""
    x_off, y_off = offsets
    w, h = size
    return image.crop((x_off, y_off, x_off + w, y_off + h))
