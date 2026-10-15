"""박스 기하 — 클램프, 컨텍스트 마진, 면적 비율.

박스는 `(x1, y1, x2, y2)` 이며 x2·y2 는 **배타적**이다 (PIL crop 규약과 동일).
"""
from __future__ import annotations

Box = tuple[int, int, int, int]

# 박스가 이미지의 이 비율을 넘으면 사실상 전체 재생성이라 경고한다.
LARGE_BOX_FRACTION = 0.5


class BoxError(ValueError):
    """박스가 이미지 범위 안에서 유효한 사각형이 되지 못할 때."""


def clamp_box(box: Box, width: int, height: int) -> Box:
    """좌표를 정규화하고 이미지 경계로 자른다. 결과가 비면 BoxError."""
    x1, y1, x2, y2 = (int(v) for v in box)
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x1 = max(0, min(x1, width))
    y1 = max(0, min(y1, height))
    x2 = max(0, min(x2, width))
    y2 = max(0, min(y2, height))
    if x1 >= x2 or y1 >= y2:
        raise BoxError(
            f"박스가 비었습니다: ({x1},{y1})-({x2},{y2}) / 이미지 {width}x{height}"
        )
    return (x1, y1, x2, y2)


def context_margin(box: Box, ratio: float = 0.25) -> int:
    """모델에 줄 주변 맥락의 폭 — 박스 짧은 변의 `ratio` 배, 최소 1px."""
    x1, y1, x2, y2 = box
    return max(1, round(min(x2 - x1, y2 - y1) * ratio))


def expand_box(box: Box, margin: int, width: int, height: int) -> Box:
    """박스를 사방으로 margin 만큼 넓히고 이미지 경계로 자른다."""
    x1, y1, x2, y2 = box
    return clamp_box((x1 - margin, y1 - margin, x2 + margin, y2 + margin), width, height)


def area_fraction(box: Box, width: int, height: int) -> float:
    """박스가 이미지에서 차지하는 면적 비율."""
    x1, y1, x2, y2 = box
    return ((x2 - x1) * (y2 - y1)) / float(width * height)


def box_contains_image(box: Box, width: int, height: int) -> bool:
    """박스가 이미지 전체를 감싸는가.

    아웃페인팅의 박스는 「어디까지 넓힐지」라서 이미지를 포함해야만 뜻이 있다.
    포함하지 않는 박스를 받으면 엔진이 합집합으로 조용히 보정해 왔는데, 그러면
    그린 것과 생성되는 것이 달라진다 — 그 보정을 없애려고 여기서 먼저 판정한다.
    """
    x1, y1, x2, y2 = (int(v) for v in box)
    return x1 <= 0 and y1 <= 0 and x2 >= width and y2 >= height
