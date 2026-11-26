"""Procedural pressure-tapered university physics & set symbols (zero built-in fonts)."""

from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple

from ..geom.ribbon import pts_to_ribbon_d

Point = Tuple[float, float]


def _quad_pts(p0: Point, p1: Point, p2: Point, steps: int = 10) -> List[Point]:
    out: List[Point] = []
    for k in range(steps + 1):
        t = k / steps
        mt = 1.0 - t
        out.append(
            (
                mt * mt * p0[0] + 2.0 * mt * t * p1[0] + t * t * p2[0],
                mt * mt * p0[1] + 2.0 * mt * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _strokes_to_ribbon_d(strokes: List[List[Point]], base_half_w: float = 2.35) -> str:
    """Convert centerlines in 0..100 coords into closed pressure-tapered SVG polygon paths."""
    parts = [
        pts_to_ribbon_d(pts, base_half_w, taper_base=0.68, taper_amp=0.42, min_r=0.75, decimals=1)
        for pts in strokes
        if len(pts) >= 2
    ]
    return " ".join(p for p in parts if p)


def _dot_poly(cx: float, cy: float, r: float = 3.2) -> List[Point]:
    pts = []
    for k in range(10):
        ang = (k / 10.0) * 2.0 * math.pi
        pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))
    return pts


def _make_variant(w: float, strokes: List[List[Point]], rng: random.Random, half_w: float = 2.35, t: float = 4.75) -> dict:
    jittered: List[List[Point]] = []
    for sp in strokes:
        j_sp = [(x + rng.uniform(-1.3, 1.3), y + rng.uniform(-1.3, 1.3)) for x, y in sp]
        jittered.append(j_sp)
    d = _strokes_to_ribbon_d(jittered, base_half_w=half_w)
    return {"w": round(w, 1), "h": 100.0, "d": d, "fill": True, "t": t}


def _build_physics_bank() -> Dict[str, dict]:
    rng = random.Random(20260404)
    bank: Dict[str, dict] = {}

    def add(ch: str, cat: str, w: float, strokes_fn, half_w: float = 2.35):
        vs = [_make_variant(w, strokes_fn(i), rng, half_w=half_w) for i in range(3)]
        bank[ch] = {"cat": cat, "v": vs}

    # ℏ reduced Planck constant
    add("ℏ", "asc", 62.0, lambda i: [
        _quad_pts((22, 6), (17, 48), (11, 93), 12),
        _quad_pts((15, 56), (34, 38), (46, 52), 10) + _quad_pts((46, 52), (48, 72), (53, 91), 8),
        _quad_pts((6, 26), (26, 19), (44, 15), 8),
    ])
    # ℓ cursive liter / angular momentum quantum number
    add("ℓ", "asc", 52.0, lambda i: [
        _quad_pts((10, 68), (38, 36), (42, 12), 10)
        + _quad_pts((42, 12), (40, 2), (30, 8), 7)
        + _quad_pts((30, 8), (18, 38), (20, 86), 12)
        + _quad_pts((20, 86), (28, 96), (46, 78), 7)
    ])
    # ∮ contour integral
    add("∮", "bigop", 58.0, lambda i: [
        _quad_pts((48, 9), (40, 2), (34, 16), 7)
        + _quad_pts((34, 16), (29, 52), (24, 86), 14)
        + _quad_pts((24, 86), (18, 97), (10, 90), 7),
        _quad_pts((29, 36), (45, 38), (43, 52), 8)
        + _quad_pts((43, 52), (40, 66), (25, 63), 8)
        + _quad_pts((25, 63), (13, 58), (16, 44), 8)
        + _quad_pts((16, 44), (19, 35), (29, 36), 6),
    ], half_w=2.1)
    # ∬ double integral
    add("∬", "bigop", 74.0, lambda i: [
        _quad_pts((33, 9), (26, 2), (21, 17), 6) + _quad_pts((21, 17), (17, 52), (13, 86), 12) + _quad_pts((13, 86), (8, 97), (3, 90), 6),
        _quad_pts((66, 9), (59, 2), (54, 17), 6) + _quad_pts((54, 17), (50, 52), (46, 86), 12) + _quad_pts((46, 86), (41, 97), (36, 90), 6),
    ], half_w=2.1)
    # ∭ triple integral
    add("∭", "bigop", 96.0, lambda i: [
        _quad_pts((26, 9), (20, 2), (16, 17), 6) + _quad_pts((16, 17), (12, 52), (9, 86), 12) + _quad_pts((9, 86), (5, 97), (1, 90), 5),
        _quad_pts((56, 9), (50, 2), (46, 17), 6) + _quad_pts((46, 17), (42, 52), (39, 86), 12) + _quad_pts((39, 86), (35, 97), (31, 90), 5),
        _quad_pts((86, 9), (80, 2), (76, 17), 6) + _quad_pts((76, 17), (72, 52), (69, 86), 12) + _quad_pts((69, 86), (65, 97), (61, 90), 5),
    ], half_w=2.0)
    # ∇ nabla / del operator
    add("∇", "cap", 84.0, lambda i: [
        _quad_pts((8, 12), (44, 10), (77, 10), 10)
        + _quad_pts((77, 10), (58, 52), (40, 91), 10)
        + _quad_pts((40, 91), (24, 52), (9, 13), 10)
    ])
    # ∝ proportional to
    add("∝", "op", 88.0, lambda i: [
        _quad_pts((79, 24), (58, 28), (44, 52), 8)
        + _quad_pts((44, 52), (30, 78), (12, 72), 8)
        + _quad_pts((12, 72), (5, 50), (14, 30), 8)
        + _quad_pts((14, 30), (30, 24), (44, 50), 8)
        + _quad_pts((44, 50), (58, 76), (80, 74), 8)
    ])
    # ⊥ perpendicular
    add("⊥", "op", 76.0, lambda i: [
        _quad_pts((41, 10), (38, 48), (37, 86), 10),
        _quad_pts((8, 86), (38, 84), (69, 83), 10),
    ])
    # ⟨ bra angle bracket
    add("⟨", "delim", 42.0, lambda i: [
        _quad_pts((35, 6), (20, 28), (8, 50), 9) + _quad_pts((8, 50), (20, 72), (34, 94), 9),
    ])
    # ⟩ ket angle bracket
    add("⟩", "delim", 42.0, lambda i: [
        _quad_pts((8, 6), (22, 28), (34, 50), 9) + _quad_pts((34, 50), (22, 72), (7, 94), 9),
    ])
    # ÷ division obelus
    add("÷", "op", 78.0, lambda i: [
        _quad_pts((8, 52), (39, 49), (70, 47), 10),
        _dot_poly(40, 24, 3.6),
        _dot_poly(38, 76, 3.6),
    ])
    # ⊂ ⊆ ⊃ ⊇ set relations
    add("⊂", "op", 74.0, lambda i: [
        _quad_pts((64, 20), (14, 24), (10, 52), 10) + _quad_pts((10, 52), (14, 80), (65, 81), 10),
    ])
    add("⊆", "op", 74.0, lambda i: [
        _quad_pts((63, 14), (15, 18), (11, 44), 9) + _quad_pts((11, 44), (15, 68), (64, 69), 9),
        _quad_pts((10, 86), (38, 84), (65, 83), 8),
    ])
    add("⊃", "op", 74.0, lambda i: [
        _quad_pts((10, 20), (60, 24), (64, 52), 10) + _quad_pts((64, 52), (60, 80), (9, 81), 10),
    ])
    add("⊇", "op", 74.0, lambda i: [
        _quad_pts((10, 14), (59, 18), (63, 44), 9) + _quad_pts((63, 44), (59, 68), (11, 69), 9),
        _quad_pts((9, 86), (37, 84), (64, 83), 8),
    ])
    # ∉ not element of
    add("∉", "op", 78.0, lambda i: [
        _quad_pts((66, 20), (16, 24), (12, 52), 10) + _quad_pts((12, 52), (16, 80), (66, 81), 10),
        _quad_pts((13, 51), (36, 49), (59, 48), 8),
        _quad_pts((63, 10), (38, 50), (13, 91), 10),
    ])
    # ∓ minus-or-plus
    add("∓", "op", 74.0, lambda i: [
        _quad_pts((10, 24), (38, 22), (65, 20), 8),
        _quad_pts((11, 58), (38, 56), (64, 54), 8),
        _quad_pts((39, 34), (37, 60), (36, 86), 8),
    ])
    return bank


_PHYSICS_BANK: Dict[str, dict] | None = None


def ensure_physics_symbols(glyphs: Dict[str, dict]) -> None:
    """Inject calibrated vector physics/set symbols when absent from pack glyphs."""
    global _PHYSICS_BANK
    if _PHYSICS_BANK is None:
        _PHYSICS_BANK = _build_physics_bank()
    for ch, entry in _PHYSICS_BANK.items():
        if ch not in glyphs:
            glyphs[ch] = entry

