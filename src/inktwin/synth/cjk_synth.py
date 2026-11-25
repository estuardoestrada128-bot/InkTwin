"""Personal CJK synthesis engine with radical cloning, real stroke grafting, and mood presets."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import MEDIANS_GZ_PATH
from ..pack import HwPack

MOOD_PRESETS: Dict[str, dict] = {
    "neat": {
        "math_jitter": 0.28,
        "text_jitter": 0.52,
        "wobble": 0.32,
        "connect_mul": 0.55,
        "slant_add": -1.5,
        "quirks": False,
        "elastic_amp": 0.015,
    },
    "normal": {
        "math_jitter": 0.58,
        "text_jitter": 1.18,
        "wobble": 0.55,
        "connect_mul": 1.00,
        "slant_add": 0.0,
        "quirks": True,
        "elastic_amp": 0.026,
    },
    "rush": {
        "math_jitter": 0.88,
        "text_jitter": 1.65,
        "wobble": 0.78,
        "connect_mul": 1.40,
        "slant_add": -3.5,
        "quirks": True,
        "elastic_amp": 0.036,
    },
}


def load_medians_db(path: Optional[Path] = None) -> Dict[str, dict]:
    src = path or MEDIANS_GZ_PATH
    if not src.exists():
        raise FileNotFoundError(f"Stroke skeleton database not found: {src}")
    with gzip.open(src, "rt", encoding="utf-8") as f:
        return json.load(f)


def _graft_real_stroke_polygon(
    pts: List[Tuple[float, float]],
    stype: str,
    strokes_db: Dict[str, List[dict]],
    rng: random.Random,
) -> Optional[str]:
    """Map an authentic single stroke polygon from the user's pack onto a target stroke chord."""
    pool = strokes_db.get(stype)
    if not pool or len(pts) < 2:
        return None
    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 4.0:
        return None

    ranked = sorted(pool, key=lambda s: abs(float(s.get("L", 35.0)) - L))
    pick_pool = ranked[: max(1, min(6, len(ranked)))]
    chosen = pick_pool[rng.randrange(len(pick_pool))]
    uv_poly = chosen.get("p") or []
    if len(uv_poly) < 4:
        return None

    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux

    mid_pt = pts[len(pts) // 2]
    mid_dx, mid_dy = mid_pt[0] - (x0 + x1) * 0.5, mid_pt[1] - (y0 + y1) * 0.5
    mid_bend = max(-6.5, min(6.5, mid_dx * nx + mid_dy * ny))

    L_src = float(chosen.get("L", 35.0))
    perp_scale = min(52.0, max(14.0, L_src)) * (0.76 + rng.uniform(-0.05, 0.05))
    max_v_px = max(3.4, min(4.8, L * 0.34))

    polys_list = uv_poly if (isinstance(uv_poly[0], list) and uv_poly[0] and isinstance(uv_poly[0][0], list)) else [uv_poly]
    all_parts: List[str] = []
    for sub_poly in polys_list:
        if len(sub_poly) < 3:
            continue
        cmds: List[str] = []
        for idx, pt in enumerate(sub_poly):
            if len(pt) < 2:
                continue
            u_clamped = max(-0.08, min(1.08, float(pt[0])))
            bend_off = mid_bend * math.sin(max(0.0, min(1.0, u_clamped)) * math.pi)
            v_px = max(-max_v_px, min(max_v_px, float(pt[1]) * perp_scale)) + bend_off
            px = x0 + dx * u_clamped + nx * v_px
            py = y0 + dy * u_clamped + ny * v_px
            cmds.append(f"{'M' if idx == 0 else 'L'}{px:.1f} {py:.1f}")
        if cmds:
            cmds.append("Z")
            all_parts.append("".join(cmds))
    return " ".join(all_parts) if all_parts else None


def _parse_top_binary_ids(decomp: str) -> Optional[Tuple[str, str, str]]:
    if not decomp or len(decomp) < 3:
        return None
    op = decomp[0]
    if op not in ("⿰", "⿱", "⿺"):
        return None
    idc_arity = {"⿰": 2, "⿱": 2, "⿲": 3, "⿳": 3, "⿴": 2, "⿵": 2, "⿶": 2, "⿷": 2, "⿸": 2, "⿹": 2, "⿺": 2, "⿻": 2}

    def skip_node(pos: int) -> Tuple[Optional[str], int]:
        if pos >= len(decomp):
            return None, pos
        c = decomp[pos]
        if c in idc_arity:
            cur = pos + 1
            for _ in range(idc_arity[c]):
                _, cur = skip_node(cur)
            return None, cur
        return c, pos + 1

    c0, nxt = skip_node(1)
    c1, _ = skip_node(nxt)
    return (op, c0 or "", c1 or "")


def _resample_poly(pts: List[Tuple[float, float]], step: float = 7.5) -> List[Tuple[float, float]]:
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    acc = 0.0
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        cur = seg
        px, py = x0, y0
        while acc + cur >= step:
            rem = step - acc
            t = rem / cur
            nx = px + (x1 - px) * t
            ny = py + (y1 - py) * t
            out.append((nx, ny))
            px, py = nx, ny
            cur = math.hypot(x1 - px, y1 - py)
            acc = 0.0
        acc += cur
    if math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > 1.2:
        out.append(pts[-1])
    return out


def _chaikin_smooth(pts: List[Tuple[float, float]], iterations: int = 1, ratio: float = 0.22) -> List[Tuple[float, float]]:
    cur = list(pts)
    for _ in range(iterations):
        if len(cur) < 3:
            break
        nxt = [cur[0]]
        for i in range(len(cur) - 1):
            x0, y0 = cur[i]
            x1, y1 = cur[i + 1]
            if i > 0:
                nxt.append((x0 + (x1 - x0) * ratio, y0 + (y1 - y0) * ratio))
            if i < len(cur) - 2:
                nxt.append((x0 + (x1 - x0) * (1.0 - ratio), y0 + (y1 - y0) * (1.0 - ratio)))
        nxt.append(cur[-1])
        cur = nxt
    return cur


def _classify_stroke(pts: List[Tuple[float, float]]) -> str:
    if len(pts) < 2:
        return "D"
    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    chord = math.hypot(dx, dy)
    path_len = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
    if path_len < 14.0:
        return "D"
    if len(pts) >= 3 and path_len > chord * 1.15:
        return "B"
    if len(pts) >= 3:
        for i in range(1, len(pts) - 1):
            v1x, v1y = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
            v2x, v2y = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
            l1, l2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if l1 > 4.2 and l2 > 4.2:
                cos_a = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
                if math.degrees(math.acos(cos_a)) > 46.0:
                    return "B"
    ang = math.degrees(math.atan2(dy, dx))
    if -28 <= ang <= 26:
        return "H"
    if 65 <= ang <= 116:
        return "V"
    if 116 < ang <= 172:
        return "P"
    if 26 < ang < 65:
        return "N"
    return "B"


def _chain_to_starnote_ribbon(
    raw_pts: List[Tuple[float, float]],
    base_r: float = 1.68,
    phase: float = 0.0,
    end_style: str = "flick",
    valley_ranges: Optional[List[Tuple[float, float]]] = None,
) -> str:
    if len(raw_pts) < 2:
        return ""
    dense: List[Tuple[float, float]] = [raw_pts[0]]
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

    left_side: List[Tuple[float, float]] = []
    right_side: List[Tuple[float, float]] = []
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
    for px, py in poly[1:]:
        cmds.append(f"L{px:.1f} {py:.1f}")
    cmds.append("Z")
    return "".join(cmds)


def synthesize_personal_hanzi_variant(
    ch: str,
    entry: dict,
    style: dict,
    radicals_db: Dict[str, List[dict]],
    seed: int,
    strokes_db: Optional[Dict[str, List[dict]]] = None,
    connect_mul: float = 1.0,
) -> dict:
    """Synthesize a single CJK variant combining cloned real radicals, grafted strokes, and StarNote ribbons."""
    medians = entry.get("s") or []
    decomp = entry.get("d") or ""
    matches = entry.get("m") or []
    rng = random.Random(seed)

    def rsym(amp: float) -> float:
        return ((rng.random() + rng.random() + rng.random()) / 3.0 - 0.5) * 2.0 * amp

    all_x = [p[0] for st in medians for p in st]
    all_y = [p[1] for st in medians for p in st]
    if not all_x:
        return {"w": 74.0, "h": 100.0, "fill": True, "t": 4.0, "d": "", "d_hair": "", "d_fill": ""}

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    span_x = max(120.0, max_x - min_x)
    span_y = max(40.0, max_y - min_y)

    if span_y < 460.0:
        cy_raw = (min_y + max_y) * 0.5
        eff_h = max(span_y, 680.0 if len(medians) == 1 else 560.0)
        ref_y0 = cy_raw - eff_h * 0.5
        ref_y1 = cy_raw + eff_h * 0.5
    else:
        ref_y0, ref_y1 = float(min_y), float(max_y)
    raw_w = span_x
    raw_h = max(120.0, ref_y1 - ref_y0)
    raw_asp = raw_w / raw_h

    learned_asp = float(style.get("aspect_median", 0.692))
    if len(medians) == 1 and span_y < 240.0:
        target_asp = 0.86 + rsym(0.08)
    elif raw_asp < 0.55:
        target_asp = max(0.46, raw_asp * 1.04)
    else:
        target_asp = learned_asp * (0.76 + 0.24 * min(1.28, max(0.70, raw_asp))) + rsym(0.065)
    target_asp = max(0.48, min(1.04, target_asp))

    vw = round(100.0 * target_asp, 1)
    pad_x, pad_y = 4.5, 4.5
    inner_w = max(20.0, vw - pad_x * 2)
    inner_h = 100.0 - pad_y * 2

    base_h_tilt = float(style.get("horiz_tilt_deg", -12.7))
    h_tilt_deg = max(-13.2, min(-7.0, base_h_tilt * 0.88)) + rsym(2.6)
    h_shear = math.tan(math.radians(h_tilt_deg)) * 0.66

    base_v_lean = float(style.get("vert_lean_deg", 6.3))
    v_lean_deg = max(-7.0, min(7.5, base_v_lean * 0.72)) + rsym(2.5)
    v_shear = math.tan(math.radians(v_lean_deg)) * 0.52

    p1, p2 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    warp_amp_x = 3.2 + rng.random() * 1.8
    warp_amp_y = 2.8 + rng.random() * 1.6
    top_taper = rsym(0.12)

    parsed = _parse_top_binary_ids(decomp)
    is_lr = bool(parsed and parsed[0] == "⿰")
    comp_ids: List[int] = []
    if matches and len(matches) == len(medians):
        for m in matches:
            comp_ids.append(m[0] if isinstance(m, list) and m and isinstance(m[0], int) else -1)
    else:
        comp_ids = [-1] * len(medians)

    def map_pt(ix: float, iy: float, comp_idx: int = -1) -> Tuple[float, float]:
        nx = (ix - min_x) / raw_w
        ny = (iy - ref_y0) / raw_h
        if is_lr:
            if comp_idx == 0:
                nx = nx * 0.94 + 0.035
                ny = ny * 0.95 + 0.015
            elif comp_idx == 1:
                nx = nx * 0.97 - 0.032
        cx = nx - 0.5
        cy = ny - 0.5
        nx_t = 0.5 + cx * (1.0 + top_taper * (0.5 - ny))
        x = pad_x + nx_t * inner_w + cy * inner_h * v_shear
        y = pad_y + ny * inner_h + cx * inner_w * h_shear
        x += math.sin(ny * 3.1 + p1) * warp_amp_x
        y += math.cos(nx * 2.9 + p2) * warp_amp_y
        return (x, y)

    cloned_comp_indices = set()
    fill_parts: List[str] = []
    if parsed and radicals_db and len(matches) == len(medians):
        _op, c0, c1 = parsed
        cand_slots = []
        if c0 and c0 in radicals_db and radicals_db[c0]:
            cand_slots.append((0, c0))
        if c1 and c1 in radicals_db and radicals_db[c1]:
            cand_slots.append((1, c1))
        for slot_i, rad_ch in cand_slots:
            s_idxs = [k for k, cid in enumerate(comp_ids) if cid == slot_i]
            if len(s_idxs) < 2:
                continue
            rpts = [map_pt(p[0], p[1], slot_i) for k in s_idxs for p in medians[k]]
            rx0, rx1 = min(p[0] for p in rpts), max(p[0] for p in rpts)
            ry0, ry1 = min(p[1] for p in rpts), max(p[1] for p in rpts)
            rw, rh = max(10.0, rx1 - rx0), max(14.0, ry1 - ry0)
            pad_rx, pad_ry = rw * 0.05, rh * 0.04
            bx0, by0 = rx0 - pad_rx, ry0 - pad_ry
            bw, bh = rw + pad_rx * 2, rh + pad_ry * 2

            r_variants = radicals_db[rad_ch]
            rv = r_variants[rng.randrange(len(r_variants))]
            r_rot = math.radians(rsym(3.5))
            cos_r, sin_r = math.cos(r_rot), math.sin(r_rot)
            rcx, rcy = bx0 + bw * 0.5, by0 + bh * 0.5
            for poly in rv.get("polys", []):
                if len(poly) < 3:
                    continue
                cmds = []
                for pi, (px, py) in enumerate(poly):
                    tx = bx0 + (px / 100.0) * bw
                    ty = by0 + (py / 100.0) * bh
                    dxr, dyr = tx - rcx, ty - rcy
                    fx = rcx + dxr * cos_r - dyr * sin_r
                    fy = rcy + dxr * sin_r + dyr * cos_r
                    cmds.append(f"{'M' if pi == 0 else 'L'}{fx:.1f} {fy:.1f}")
                cmds.append("Z")
                fill_parts.append("".join(cmds))
            cloned_comp_indices.add(slot_i)

    transformed: List[Tuple[str, List[Tuple[float, float]]]] = []
    for s_idx, st in enumerate(medians):
        c_id = comp_ids[s_idx] if s_idx < len(comp_ids) else -1
        if c_id in cloned_comp_indices:
            continue
        pts = [map_pt(p[0], p[1], c_id) for p in st]
        if len(pts) < 2:
            continue
        stype = _classify_stroke(pts)
        if stype == "B":
            pts = _chaikin_smooth(pts, iterations=1, ratio=0.25)

        x0, y0 = pts[0]
        x1, y1 = pts[-1]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) + 1e-6

        if stype == "H":
            extra_rise = -ln * (0.055 + rng.random() * 0.055)
            arch = -(1.0 + rng.random() * 1.3)
            new_pts = []
            for idx, (px, py) in enumerate(pts):
                t = idx / max(1, len(pts) - 1)
                new_pts.append((px + rsym(1.1), py + extra_rise * (t - 0.35) + arch * math.sin(t * math.pi) + rsym(0.9)))
            if len(new_pts) >= 2:
                ux, uy = dx / ln, dy / ln
                new_pts[0] = (new_pts[0][0] - ux * rng.uniform(0.6, 2.2), new_pts[0][1] - uy * rng.uniform(0.4, 1.5))
                new_pts[-1] = (new_pts[-1][0] + ux * rng.uniform(1.0, 3.2), new_pts[-1][1] - rng.uniform(0.3, 1.4))
            pts = new_pts
        elif stype == "V":
            bow, lean_x = rsym(2.4), rsym(2.8)
            new_pts = []
            for idx, (px, py) in enumerate(pts):
                t = idx / max(1, len(pts) - 1)
                new_pts.append((px + bow * math.sin(t * math.pi) + lean_x * t + rsym(0.9), py + rsym(0.8)))
            if len(new_pts) >= 2:
                new_pts[-1] = (new_pts[-1][0] + rsym(1.2), new_pts[-1][1] + rng.uniform(0.8, 3.0))
            pts = new_pts
        elif stype == "N":
            shorten = float(style.get("na_shorten", 0.72)) + rsym(0.07)
            pts = [(x0 + (px - x0) * shorten + rsym(1.2), y0 + (py - y0) * (0.82 + 0.16 * shorten) + rsym(1.0)) for px, py in pts]
        elif stype == "P":
            bow = -(0.8 + rng.random() * 1.5)
            new_pts = []
            for idx, (px, py) in enumerate(pts):
                t = idx / max(1, len(pts) - 1)
                new_pts.append((px + bow * math.sin(t * math.pi) + rsym(1.0), py + t * rng.uniform(0.5, 2.2) + rsym(0.9)))
            pts = new_pts
        elif stype == "D":
            new_pts = [(px + rsym(1.3), py + rsym(1.3)) for px, py in pts]
            if len(new_pts) >= 2 and ln < 6.5:
                new_pts[-1] = (new_pts[0][0] + rng.uniform(4.0, 8.2), new_pts[0][1] + rng.uniform(3.5, 7.8))
            pts = new_pts
        else:
            pts = [(px + rsym(1.4), py + rsym(1.4)) for px, py in pts]

        pts = _resample_poly(pts, step=8.0)
        if len(pts) >= 3:
            pts = [pts[0]] + [(px + rsym(0.75), py + rsym(0.75)) for px, py in pts[1:-1]] + [pts[-1]]
        transformed.append((stype, pts))

    connect_rate = min(0.88, float(style.get("connect_ratio", 0.56)) * connect_mul)
    max_lig_dist = float(style.get("ligature_max_dist", 40.0))

    main_paths: List[str] = []
    i = 0
    n_st = len(transformed)
    while i < n_st:
        cur_type, cur_pts = transformed[i]
        chain_pts = list(cur_pts)
        chain_len = 1
        valleys: List[Tuple[int, int]] = []

        while i + 1 < n_st and chain_len < 5:
            nxt_type, nxt_pts = transformed[i + 1]
            ex, ey = chain_pts[-1]
            sx, sy = nxt_pts[0]
            dist = math.hypot(sx - ex, sy - ey)

            same_left = ex < vw * 0.48 and sx < vw * 0.52
            same_right = ex > vw * 0.36 and sx > vw * 0.34
            cross_radical_jump = ex < vw * 0.40 and sx > vw * 0.58 and dist > 25.0

            if cross_radical_jump or dist > max_lig_dist:
                break

            aff = 0.0
            if dist < 17.5:
                aff = 0.96
            elif cur_type == "D" and nxt_type in ("D", "B", "H", "P"):
                aff = 0.92 if dist < 32.0 else 0.58
            elif cur_type == "H" and nxt_type in ("H", "B", "V", "P"):
                aff = 0.88 if dist < 31.0 else 0.52
            elif cur_type == "V" and nxt_type in ("B", "H", "D"):
                aff = 0.86 if dist < 29.0 else 0.46
            elif cur_type == "B" and nxt_type in ("H", "B", "D", "P"):
                aff = 0.85 if dist < 30.0 else 0.44
            elif (same_left or same_right) and dist < 27.0:
                aff = 0.82
            elif dist < 24.0:
                aff = 0.68

            prob = min(0.97, aff * (0.85 + connect_rate * 0.78))
            if rng.random() > prob:
                break

            v_start_idx = len(chain_pts) - 1
            if len(chain_pts) >= 2:
                tx = chain_pts[-1][0] - chain_pts[-2][0]
                ty = chain_pts[-1][1] - chain_pts[-2][1]
                tl = math.hypot(tx, ty) + 1e-6
                ctrl_x = (ex + sx) * 0.5 + (tx / tl) * min(6.0, dist * 0.28) + rsym(1.8)
                ctrl_y = (ey + sy) * 0.5 + (ty / tl) * min(6.0, dist * 0.28) + rsym(1.8)
            else:
                ctrl_x = (ex + sx) * 0.5 + rsym(1.8)
                ctrl_y = (ey + sy) * 0.5 + rsym(1.8)
            chain_pts[-1] = (ex * 0.86 + sx * 0.14, ey * 0.86 + sy * 0.14)
            chain_pts.append((ctrl_x, ctrl_y))
            chain_pts.append((sx * 0.86 + ex * 0.14, sy * 0.86 + ey * 0.14))
            v_end_idx = len(chain_pts)
            valleys.append((v_start_idx, v_end_idx))
            chain_pts.extend(nxt_pts[1:])
            cur_type = nxt_type
            chain_len += 1
            i += 1

        grafted = None
        if chain_len == 1 and strokes_db and cur_type in ("H", "V", "P", "N", "D") and rng.random() < 0.85:
            grafted = _graft_real_stroke_polygon(chain_pts, cur_type, strokes_db, rng)

        if grafted:
            main_paths.append(grafted)
        else:
            n_cp = max(1, len(chain_pts) - 1)
            v_ranges = [(max(0.0, s_i / n_cp - 0.04), min(1.0, e_i / n_cp + 0.04)) for s_i, e_i in valleys]
            end_st = "press" if (cur_type in ("N", "D") or (i == n_st - 1 and cur_type in ("V", "H") and rng.random() < 0.55)) else "flick"
            d_ribbon = _chain_to_starnote_ribbon(
                chain_pts,
                base_r=1.68 + rsym(0.14),
                phase=i * 1.7 + (seed % 31) * 0.2,
                end_style=end_st,
                valley_ranges=v_ranges,
            )
            if d_ribbon:
                main_paths.append(d_ribbon)
        i += 1

    all_fill_d = " ".join(p for p in (fill_parts + main_paths) if p)
    return {
        "w": vw,
        "h": 100.0,
        "fill": True,
        "t": 4.0,
        "d": all_fill_d,
        "d_hair": "",
        "d_fill": "",
    }


def synthesize_variants_for_pack(
    chars: List[str],
    pack: HwPack,
    medians_db: Optional[Dict[str, dict]] = None,
    mood: str = "normal",
    n_variants: int = 4,
    base_seed: int = 20261125,
) -> Dict[str, dict]:
    """Synthesize unseen CJK variants from a loaded HwPack's style DNA, radicals, and strokes."""
    if not chars or not pack.has_synth_assets:
        return {}
    if medians_db is None:
        medians_db = load_medians_db()
    connect_mul = float(MOOD_PRESETS.get(mood, MOOD_PRESETS["normal"])["connect_mul"])
    out: Dict[str, dict] = {}
    for ch in chars:
        entry = medians_db.get(ch)
        if not entry or not entry.get("s"):
            continue
        ch_hash = int(hashlib.md5(ch.encode("utf-8")).hexdigest()[:8], 16)
        vlist = []
        for k in range(n_variants):
            v = synthesize_personal_hanzi_variant(
                ch,
                entry,
                pack.style_model,
                pack.radicals,
                seed=(base_seed ^ ch_hash) + k * 7919,
                strokes_db=pack.strokes,
                connect_mul=connect_mul,
            )
            if v["d"]:
                vlist.append(v)
        if vlist:
            out[ch] = {"cat": "cjk", "v": vlist}
    return out

