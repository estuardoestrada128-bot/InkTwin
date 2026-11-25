"""Pressure-preserving EDT stroke thickness calibrator and SVG/stroke normalizer."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import fitz
import numpy as np
from PIL import Image, ImageDraw

from ..synth.cjk_synth import _classify_stroke


def rdp(pts: List[Tuple[float, float]], eps: float = 0.32) -> List[Tuple[float, float]]:
    """Simplify polyline using Ramer-Douglas-Peucker while preserving sharp corners."""
    if len(pts) <= 2:
        return list(pts)
    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    denom = math.hypot(dx, dy)
    max_d = -1.0
    max_i = 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        if denom < 1e-9:
            d = math.hypot(px - x0, py - y0)
        else:
            d = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / denom
        if d > max_d:
            max_d = d
            max_i = i
    if max_d > eps:
        left = rdp(pts[: max_i + 1], eps)
        right = rdp(pts[max_i:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def measure_fitz_stroke_thickness(
    norm_polys: List[List[Tuple[float, float]]],
    vw: float,
    vh: float = 100.0,
) -> float:
    """Measure visual stroke thickness via exact SVG nonzero rasterization (handles inner holes)."""
    if not norm_polys:
        return 5.0
    ss = min(2.2, 360.0 / max(100.0, vw))
    ss = max(1.0, ss)
    pad = 6.0
    W = max(24.0, vw * ss + pad * 2)
    H = max(24.0, vh * ss + pad * 2)
    d_cmds = []
    for pts in norm_polys:
        if len(pts) >= 3:
            c = [f"M{pts[0][0]*ss+pad:.1f} {pts[0][1]*ss+pad:.1f}"]
            for x, y in pts[1:]:
                c.append(f"L{x*ss+pad:.1f} {y*ss+pad:.1f}")
            c.append("Z")
            d_cmds.append("".join(c))
    if not d_cmds:
        return 5.0
    svg_s = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}" height="{H:.1f}" viewBox="0 0 {W:.1f} {H:.1f}">'
        f'<rect width="{W:.1f}" height="{H:.1f}" fill="#fff"/>'
        f'<path d="{" ".join(d_cmds)}" fill="#000" fill-rule="nonzero"/></svg>'
    )
    with fitz.open(stream=svg_s.encode("utf-8"), filetype="svg") as doc:
        pix = doc[0].get_pixmap(dpi=72, colorspace=fitz.csGRAY, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 180
    ink = int(arr.sum())
    if ink < 6:
        return 5.0
    p = np.pad(arr, 1, mode="constant", constant_values=False)
    edges = int(
        (arr & ~p[:-2, 1:-1]).sum()
        + (arr & ~p[2:, 1:-1]).sum()
        + (arr & ~p[1:-1, :-2]).sum()
        + (arr & ~p[1:-1, 2:]).sum()
    )
    if edges < 4:
        return 5.0
    euclid_perim = float(edges) / 1.2732
    t_px_ss = 2.0 * float(ink) / max(1.0, euclid_perim)
    return round(t_px_ss / ss, 2)


def _edt_1d_rows(f: np.ndarray) -> np.ndarray:
    H, W = f.shape
    out = np.empty_like(f)
    for y in range(H):
        row = f[y]
        v = [0] * W
        z = [0.0] * (W + 1)
        k = 0
        v[0] = 0
        z[0] = -1e18
        z[1] = 1e18
        for q in range(1, W):
            fq = row[q]
            while True:
                vk = v[k]
                s = ((fq + q * q) - (row[vk] + vk * vk)) / (2.0 * (q - vk))
                if s <= z[k]:
                    k -= 1
                else:
                    break
            k += 1
            v[k] = q
            z[k] = s
            z[k + 1] = 1e18
        k = 0
        for q in range(W):
            while z[k + 1] < q:
                k += 1
            vk = v[k]
            d = q - vk
            out[y, q] = d * d + row[vk]
    return out


def _exact_edt(mask: np.ndarray) -> np.ndarray:
    f = np.where(mask, 1e9, 0.0)
    return np.sqrt(_edt_1d_rows(_edt_1d_rows(f.T).T))


def _zhang_suen_thin(mask: np.ndarray) -> np.ndarray:
    img = np.pad(mask.astype(np.uint8), 1, mode="constant", constant_values=0)
    while True:
        changed = False
        for step in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9, center = img[1:-1, :-2], img[:-2, :-2], img[1:-1, 1:-1]
            nb = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            trans = (
                ((p2 == 0) & (p3 == 1)).astype(np.uint8)
                + ((p3 == 0) & (p4 == 1)).astype(np.uint8)
                + ((p4 == 0) & (p5 == 1)).astype(np.uint8)
                + ((p5 == 0) & (p6 == 1)).astype(np.uint8)
                + ((p6 == 0) & (p7 == 1)).astype(np.uint8)
                + ((p7 == 0) & (p8 == 1)).astype(np.uint8)
                + ((p8 == 0) & (p9 == 1)).astype(np.uint8)
                + ((p9 == 0) & (p2 == 1)).astype(np.uint8)
            )
            if step == 0:
                cond = (center == 1) & (nb >= 2) & (nb <= 6) & (trans == 1) & ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                cond = (center == 1) & (nb >= 2) & (nb <= 6) & (trans == 1) & ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)
            if np.any(cond):
                center[cond] = 0
                changed = True
        if not changed:
            break
    return img[1:-1, 1:-1].astype(bool)


def _trace_binary_contours(mask: np.ndarray) -> List[List[Tuple[float, float]]]:
    m = np.pad(mask.astype(np.uint8), 1, mode="constant", constant_values=0)
    adj: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    def add_e(u, v):
        adj.setdefault(u, []).append(v)

    rs, cs = np.where((m[1:-1, 1:-1] == 1) & (m[:-2, 1:-1] == 0))
    for r, c in zip(rs + 1, cs + 1):
        add_e((int(c), int(r)), (int(c + 1), int(r)))
    rs, cs = np.where((m[1:-1, 1:-1] == 1) & (m[1:-1, 2:] == 0))
    for r, c in zip(rs + 1, cs + 1):
        add_e((int(c + 1), int(r)), (int(c + 1), int(r + 1)))
    rs, cs = np.where((m[1:-1, 1:-1] == 1) & (m[2:, 1:-1] == 0))
    for r, c in zip(rs + 1, cs + 1):
        add_e((int(c + 1), int(r + 1)), (int(c), int(r + 1)))
    rs, cs = np.where((m[1:-1, 1:-1] == 1) & (m[1:-1, :-2] == 0))
    for r, c in zip(rs + 1, cs + 1):
        add_e((int(c), int(r + 1)), (int(c), int(r)))

    loops: List[List[Tuple[float, float]]] = []
    while adj:
        start = next(iter(adj))
        pts = [start]
        cur = start
        prev_dir = None
        while True:
            nxts = adj.get(cur)
            if not nxts:
                break
            if len(nxts) == 1 or prev_dir is None:
                nxt = nxts.pop(0)
            else:
                dx0, dy0 = prev_dir
                best_i, best_score = 0, -9
                for idx, cand in enumerate(nxts):
                    dx1, dy1 = cand[0] - cur[0], cand[1] - cur[1]
                    cross = dx0 * dy1 - dy0 * dx1
                    dot = dx0 * dx1 + dy0 * dy1
                    score = 2 if cross > 0 else (1 if dot > 0 else 0)
                    if score > best_score:
                        best_score = score
                        best_i = idx
                nxt = nxts.pop(best_i)
            if not nxts:
                del adj[cur]
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                break
            pts.append(cur)
        if len(pts) >= 4:
            n = len(pts)
            loops.append([((pts[i][0] + pts[(i + 1) % n][0]) * 0.5 - 1.0, (pts[i][1] + pts[(i + 1) % n][1]) * 0.5 - 1.0) for i in range(n)])
    return loops


def pressure_preserve_thin_variant(v: dict, target_t: float = 4.8) -> dict:
    """Thin overly heavy glyph strokes via exact EDT while preserving pressure modulation & tips."""
    vw, vh, d_str = float(v["w"]), float(v["h"]), v["d"]
    ss = max(1.8, min(3.0, 300.0 / max(100.0, vw)))
    pad = 10.0
    W = int(math.ceil(vw * ss + pad * 2))
    H = int(math.ceil(vh * ss + pad * 2))

    svg_s = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<rect width="{W}" height="{H}" fill="#fff"/>'
        f'<g transform="translate({pad:.1f},{pad:.1f}) scale({ss:.4f})">'
        f'<path d="{d_str}" fill="#000" fill-rule="nonzero"/></g></svg>'
    )
    with fitz.open(stream=svg_s.encode("utf-8"), filetype="svg") as doc:
        pix = doc[0].get_pixmap(dpi=72, colorspace=fitz.csGRAY, alpha=False)
        mask = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 180
        pw, ph = pix.width, pix.height

    if int(mask.sum()) < 20:
        return v

    D = _exact_edt(mask)
    skel = _zhang_suen_thin(mask)
    ys, xs = np.where(skel)
    if len(ys) < 4:
        return v

    radii = D[ys, xs]
    med_r = max(1.0, float(np.median(radii)))
    target_r_ss = (target_t * 0.5) * ss
    rel = np.clip(radii / med_r, 0.48, 1.42)
    r_new = np.maximum(0.85, target_r_ss * (rel ** 0.78))

    im = Image.new("L", (pw, ph), 0)
    dr = ImageDraw.Draw(im)
    for y, x, rn in zip(ys, xs, r_new):
        rr = float(rn)
        dr.ellipse((x - rr, y - rr, x + rr, y + rr), fill=255)
    new_mask = np.array(im) > 127

    loops = _trace_binary_contours(new_mask)
    parts: List[str] = []
    norm_polys: List[List[Tuple[float, float]]] = []
    for lp in loops:
        if len(lp) < 6:
            continue
        norm_lp = [((px - pad) / ss, (py - pad) / ss) for px, py in lp]
        mid = len(norm_lp) // 2
        s1 = rdp(norm_lp[: mid + 1], 0.40)
        s2 = rdp(norm_lp[mid:] + [norm_lp[0]], 0.40)
        simp = s1[:-1] + s2[:-1]
        if len(simp) < 3:
            continue
        norm_polys.append(simp)
        cmd_s = [f"M{simp[0][0]:.1f} {simp[0][1]:.1f}"] + [f"L{px:.1f} {py:.1f}" for px, py in simp[1:]] + ["Z"]
        parts.append("".join(cmd_s))
    if not parts:
        return v
    t_new = measure_fitz_stroke_thickness(norm_polys, vw, vh)
    return {"w": v["w"], "h": v["h"], "d": " ".join(parts), "fill": True, "t": t_new}


def ribbonize_open_polyline(pts: List[Tuple[float, float]], half_w: float = 0.65) -> List[Tuple[float, float]]:
    """Convert an open center polyline into a subtly pressure-tapered closed contour polygon."""
    if len(pts) < 2:
        return list(pts)
    n = len(pts)
    left: List[Tuple[float, float]] = []
    right: List[Tuple[float, float]] = []
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
        taper = 0.70 + 0.38 * math.sin(u * math.pi)
        r = max(0.2, half_w * taper)
        left.append((px + nx * r, py + ny * r))
        right.append((px - nx * r, py - ny * r))
    return left + list(reversed(right))


def calibrate_variant_polygon(
    polys: List[List[Tuple[float, float]]],
    rect: Optional[Tuple[float, float, float, float]] = None,
    norm_mode: str = "height100",
    is_fill: bool = True,
    max_t: float = 5.8,
    target_t: float = 4.8,
) -> Optional[dict]:
    """Normalize collected contour polygons to standard InkTwin variant dict and calibrate thickness."""
    valid = [p for p in polys if len(p) >= 2]
    if not valid:
        return None
    if rect is None:
        xs = [pt[0] for p in valid for pt in p]
        ys = [pt[1] for p in valid for pt in p]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    else:
        x0, y0, x1, y1 = rect
    w = max(0.4, x1 - x0)
    h = max(0.4, y1 - y0)

    if norm_mode == "width100":
        scale = 100.0 / w
        vw = 100.0
        vh = round(h * scale, 1)
    else:
        scale = 100.0 / h
        vw = round(w * scale, 1)
        vh = 100.0

    path_parts: List[str] = []
    norm_polys: List[List[Tuple[float, float]]] = []
    for poly in valid:
        norm_pts = [((px - x0) * scale, (py - y0) * scale) for px, py in poly]
        simp = rdp(norm_pts, 0.32)
        if len(simp) < 3 and is_fill:
            continue
        if len(simp) < 2:
            continue
        norm_polys.append(simp)
        cmd = [f"M{simp[0][0]:.1f} {simp[0][1]:.1f}"] + [f"L{px:.1f} {py:.1f}" for px, py in simp[1:]]
        if is_fill:
            cmd.append("Z")
        path_parts.append("".join(cmd))

    if not path_parts:
        return None

    t_fitz = measure_fitz_stroke_thickness(norm_polys, vw, vh) if is_fill else target_t
    variant = {"w": vw, "h": vh, "d": " ".join(path_parts), "fill": is_fill, "t": t_fitz}
    if is_fill and norm_mode == "height100" and t_fitz > max_t:
        variant = pressure_preserve_thin_variant(variant, target_t=target_t)
    return variant


def calibrate_radical_item(polys: List[List[Tuple[float, float]]]) -> Optional[dict]:
    """Normalize collected radical contour polygons into height=100, width=100*asp format."""
    valid = [p for p in polys if len(p) >= 3]
    if not valid:
        return None
    xs = [pt[0] for p in valid for pt in p]
    ys = [pt[1] for p in valid for pt in p]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    w = max(0.4, x1 - x0)
    h = max(0.4, y1 - y0)
    scale = 100.0 / h
    asp = round(w / h, 3)
    norm_polys = []
    for p in valid:
        pts = rdp([((px - x0) * scale, (py - y0) * scale) for px, py in p], 0.35)
        if len(pts) >= 3:
            norm_polys.append([[round(x, 1), round(y, 1)] for x, y in pts])
    if not norm_polys:
        return None
    return {"asp": asp, "polys": norm_polys}


def polygon_to_stroke_dna_item(
    polys: List[List[Tuple[float, float]]],
    forced_type: Optional[str] = None,
) -> Optional[Tuple[str, dict]]:
    """Convert an isolated stroke ribbon into normalized (u, v) stroke DNA for CJK synthesis."""
    valid = [p for p in polys if len(p) >= 3]
    if not valid:
        return None
    all_pts = [pt for p in valid for pt in p]
    # Find farthest point pair to approximate stroke chord
    p_ref = all_pts[0]
    p_a = max(all_pts, key=lambda p: (p[0] - p_ref[0]) ** 2 + (p[1] - p_ref[1]) ** 2)
    p_b = max(all_pts, key=lambda p: (p[0] - p_a[0]) ** 2 + (p[1] - p_a[1]) ** 2)
    dx0, dy0 = p_b[0] - p_a[0], p_b[1] - p_a[1]
    if abs(dx0) >= abs(dy0):
        if dx0 < 0:
            p_a, p_b = p_b, p_a
    elif dy0 < 0:
        p_a, p_b = p_b, p_a

    # Rescale from PDF pt (~14-42pt cell) to canonical CJK 100-space chord length
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    scale_to_100 = 85.0 / span

    x0, y0 = p_a
    x1, y1 = p_b
    dx, dy = x1 - x0, y1 - y0
    chord_raw = math.hypot(dx, dy)
    if chord_raw < 1.5:
        return None

    L_100 = round(chord_raw * scale_to_100, 1)
    ang = round(math.degrees(math.atan2(dy, dx)), 1)
    tx, ty = dx / chord_raw, dy / chord_raw
    nx, ny = -ty, tx
    perp_scale = min(52.0, max(14.0, L_100)) * 0.76

    uv_polys = []
    for p in valid:
        uv_p = []
        for px, py in rdp(p, 0.25):
            ux = ((px - x0) * tx + (py - y0) * ty) / chord_raw
            vy = (((px - x0) * nx + (py - y0) * ny) * scale_to_100) / perp_scale
            uv_p.append([round(ux, 3), round(vy, 3)])
        if len(uv_p) >= 3:
            uv_polys.append(uv_p)
    if not uv_polys:
        return None

    stype = forced_type
    if not stype or stype not in ("H", "V", "P", "N", "D"):
        c_cls = _classify_stroke([(0.0, 0.0), (dx * scale_to_100, dy * scale_to_100)])
        stype = c_cls if c_cls in ("H", "V", "P", "N", "D") else "D"
    return stype, {"L": L_100, "ang": ang, "p": uv_polys}


def measure_style_dna(
    glyphs: Dict[str, dict],
    strokes: Optional[Dict[str, list]] = None,
) -> dict:
    """Measure global handwriting geometry & tilt DNA from harvested glyphs and strokes."""
    cjk_asps = []
    for entry in glyphs.values():
        if entry.get("cat") == "cjk":
            for v in entry.get("v", []):
                h = float(v.get("h", 100.0) or 100.0)
                if h > 0:
                    cjk_asps.append(float(v.get("w", 70.0)) / h)
    aspect_med = round(float(np.median(cjk_asps)), 3) if cjk_asps else 0.72

    h_angs = [float(s["ang"]) for s in (strokes or {}).get("H", []) if "ang" in s]
    v_angs = [float(s["ang"]) - 90.0 for s in (strokes or {}).get("V", []) if "ang" in s]
    horiz_tilt = round(float(np.median(h_angs)), 2) if h_angs else -8.5
    vert_lean = round(float(np.median(v_angs)), 2) if v_angs else 5.5

    return {
        "version": 7,
        "aspect_median": aspect_med,
        "horiz_tilt_deg": horiz_tilt,
        "vert_lean_deg": vert_lean,
        "connect_ratio": 0.48,
        "corner_round": 0.36,
        "na_shorten": 0.72,
        "ligature_max_dist": 38.0,
    }

