"""Shared pressure-tapered ink ribbon polygon & SVG path builders."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

Point = Tuple[float, float]


def ribbon_polygon_pts(
    pts: List[Point],
    half_w: float = 0.65,
    *,
    taper_base: float = 0.70,
    taper_amp: float = 0.38,
    min_r: float = 0.2,
) -> List[Point]:
    """Convert an open centerline polyline into a closed pressure-tapered contour polygon."""
    if len(pts) < 2:
        return list(pts)
    n = len(pts)
    left: List[Point] = []
    right: List[Point] = []
    for i, (px, py) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - px, pts[1][1] - py
        elif i == n - 1:
            dx, dy = px - pts[n - 2][0], py - pts[n - 2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        u = i / max(1, n - 1)
        taper = taper_base + taper_amp * math.sin(u * math.pi)
        r = max(min_r, half_w * taper)
        left.append((px + nx * r, py + ny * r))
        right.append((px - nx * r, py - ny * r))
    return left + list(reversed(right))


def pts_to_ribbon_d(
    pts: List[Point],
    half_w: float = 0.82,
    *,
    taper_base: float = 0.72,
    taper_amp: float = 0.36,
    min_r: float = 0.32,
    decimals: int = 2,
) -> str:
    """Convert an open centerline polyline into a closed SVG M/L/Z pressure-ribbon path."""
    if len(pts) < 2:
        return ""
    poly = ribbon_polygon_pts(pts, half_w, taper_base=taper_base, taper_amp=taper_amp, min_r=min_r)
    fmt = f".{decimals}f"
    cmds = [f"M{format(poly[0][0], fmt)} {format(poly[0][1], fmt)}"]
    cmds.extend(f"L{format(x, fmt)} {format(y, fmt)}" for x, y in poly[1:])
    cmds.append("Z")
    return "".join(cmds)


def chain_to_starnote_ribbon(
    raw_pts: List[Point],
    base_r: float = 1.68,
    phase: float = 0.0,
    end_style: str = "flick",
    valley_ranges: Optional[List[Tuple[float, float]]] = None,
) -> str:
    """Synthesize a StarNote-style variable-width solid ribbon contour from raw stroke control points."""
    if len(raw_pts) < 2:
        return ""
    dense: List[Point] = [raw_pts[0]]
    if len(raw_pts) == 2:
        x0, y0 = raw_pts[0]
        x1, y1 = raw_pts[1]
        steps = max(5, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / 2.6)))
        for k in range(1, steps + 1):
            t = k / steps
            dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    else:
        p_prev = raw_pts[0]
        for i in range(1, len(raw_pts) - 1):
            cx, cy = raw_pts[i]
            nx, ny = raw_pts[i + 1]
            mx, my = (cx + nx) * 0.5, (cy + ny) * 0.5
            approx = math.hypot(cx - p_prev[0], cy - p_prev[1]) + math.hypot(mx - cx, my - cy)
            steps = max(4, int(math.ceil(approx / 2.6)))
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1.0 - t
                dense.append((
                    mt * mt * p_prev[0] + 2.0 * mt * t * cx + t * t * mx,
                    mt * mt * p_prev[1] + 2.0 * mt * t * cy + t * t * my,
                ))
            p_prev = (mx, my)
        dense.append(raw_pts[-1])

    clean = [dense[0]]
    for p in dense[1:]:
        if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 0.40:
            clean.append(p)
    if len(clean) < 2:
        return ""

    cum = [0.0]
    for i in range(1, len(clean)):
        cum.append(cum[-1] + math.hypot(clean[i][0] - clean[i - 1][0], clean[i][1] - clean[i - 1][1]))
    total = max(1e-6, cum[-1])
    n = len(clean)

    left_side: List[Point] = []
    right_side: List[Point] = []
    for i in range(n):
        u = cum[i] / total
        p0 = clean[max(0, i - 1)]
        p1 = clean[min(n - 1, i + 1)]
        tx, ty = p1[0] - p0[0], p1[1] - p0[1]
        tl = math.hypot(tx, ty) + 1e-6
        tx, ty = tx / tl, ty / tl
        nx_v, ny_v = -ty, tx

        curv_boost = 1.0
        if 0 < i < n - 1:
            v1x, v1y = clean[i][0] - clean[i - 1][0], clean[i][1] - clean[i - 1][1]
            v2x, v2y = clean[i + 1][0] - clean[i][0], clean[i + 1][1] - clean[i][1]
            l1 = math.hypot(v1x, v1y) + 1e-6
            l2 = math.hypot(v2x, v2y) + 1e-6
            cos_a = (v1x * v2x + v1y * v2y) / (l1 * l2)
            if cos_a < 0.78:
                curv_boost = 1.0 + 0.28 * (0.78 - cos_a)

        if total < 16.0:
            taper = 0.58 + 0.56 * math.sin(min(1.0, u * 1.15) * math.pi * 0.85)
        else:
            if u < 0.08:
                taper = 0.64 + 0.52 * (u / 0.08)
            elif u < 0.20:
                taper = 1.16 - 0.24 * ((u - 0.08) / 0.12)
            elif u <= 0.76:
                mid_u = (u - 0.20) / 0.56
                taper = 0.92 - 0.10 * math.sin(mid_u * math.pi)
            else:
                tail_u = (u - 0.76) / 0.24
                if end_style == "press":
                    taper = 0.94 + 0.22 * math.sin(tail_u * math.pi * 0.75) - 0.48 * (tail_u ** 2.2)
                else:
                    taper = 0.92 * (1.0 - 0.76 * (tail_u ** 1.08))

        valley_mul = 1.0
        if valley_ranges:
            for vu0, vu1 in valley_ranges:
                if vu0 <= u <= vu1:
                    vm = (u - vu0) / max(1e-4, vu1 - vu0)
                    valley_mul = min(valley_mul, 1.0 - 0.54 * math.sin(vm * math.pi))

        dir_factor = 0.86 + 0.28 * max(0.0, ty) - 0.11 * max(0.0, -ty)
        ripple = 1.0 + 0.09 * math.sin(u * 9.0 + phase)
        r = max(0.42, base_r * taper * dir_factor * curv_boost * valley_mul * ripple)
        px, py = clean[i]
        left_side.append((px + nx_v * r, py + ny_v * r))
        right_side.append((px - nx_v * r, py - ny_v * r))

    poly = left_side + list(reversed(right_side))
    if len(poly) < 3:
        return ""
    cmds = [f"M{poly[0][0]:.1f} {poly[0][1]:.1f}"]
    cmds.extend(f"L{px:.1f} {py:.1f}" for px, py in poly[1:])
    cmds.append("Z")
    return "".join(cmds)

