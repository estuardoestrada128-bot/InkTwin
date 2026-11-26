"""Shared bilinear+sine deterministic elastic micro-deformation for handwriting variants."""

from __future__ import annotations

import hashlib
import math
from typing import List, Tuple

Point = Tuple[float, float]


def elastic_deform_loops(
    loops: List[List[Point]],
    vw: float,
    vh: float,
    variant_idx: int,
    seed_key: str,
    amp: float = 0.022,
) -> List[List[Point]]:
    """Synthesize a gentle deterministic elastic variant of flattened SVG loops without mutating source ink."""
    if not loops or vw <= 1.0 or vh <= 1.0:
        return loops
    digest = hashlib.sha256(f"{seed_key}#{variant_idx}".encode("utf-8")).digest()

    def _u(k: int) -> float:
        raw = int.from_bytes(digest[k * 2 : k * 2 + 2], "little") / 65535.0
        return (raw - 0.5) * 2.0

    dx00, dy00 = _u(0) * amp, _u(1) * amp
    dx10, dy10 = _u(2) * amp, _u(3) * amp
    dx01, dy01 = _u(4) * amp, _u(5) * amp
    dx11, dy11 = _u(6) * amp, _u(7) * amp
    bx, by = _u(8) * amp * 0.65, _u(9) * amp * 0.65

    out: List[List[Point]] = []
    for lp in loops:
        nlp: List[Point] = []
        for x, y in lp:
            u = max(0.0, min(1.0, x / vw))
            v = max(0.0, min(1.0, y / vh))
            sx = (1.0 - u) * (1.0 - v) * dx00 + u * (1.0 - v) * dx10 + (1.0 - u) * v * dx01 + u * v * dx11
            sy = (1.0 - u) * (1.0 - v) * dy00 + u * (1.0 - v) * dy10 + (1.0 - u) * v * dy01 + u * v * dy11
            sx += math.sin(math.pi * v) * bx
            sy += math.sin(math.pi * u) * by
            nlp.append((x + sx * vw, y + sy * vh))
        out.append(nlp)
    return out

