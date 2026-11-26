"""Zero-font, zero-straight-line declarative math/physics sketch renderer (handplot)."""

from __future__ import annotations

import ast
import html
import math
import random
import re
from typing import Dict, List, Optional, Tuple

from ..geom.ribbon import pts_to_ribbon_d as _pts_to_ribbon_d

Point = Tuple[float, float]

# Compact pressure-ribbon fallback polylines (in 0..100 box) for axis/label chars when pack lacks a glyph
_FALLBACK_STROKES: Dict[str, Tuple[float, List[List[Point]]]] = {
    "0": (62, [[(32, 10), (54, 18), (54, 54), (48, 88), (24, 90), (10, 74), (10, 32), (32, 10)]]),
    "1": (44, [[(24, 12), (22, 50), (19, 90)]]),
    "2": (60, [[(12, 24), (36, 10), (52, 26), (34, 58), (12, 88), (54, 86)]]),
    "3": (58, [[(14, 18), (44, 12), (48, 34), (24, 48), (48, 62), (44, 88), (12, 86)]]),
    "4": (62, [[(38, 12), (10, 60), (56, 58)], [(44, 14), (40, 90)]]),
    "5": (60, [[(52, 14), (18, 16), (14, 46), (44, 44), (50, 70), (28, 90), (10, 84)]]),
    "6": (60, [[(44, 12), (16, 40), (12, 76), (34, 90), (52, 70), (42, 50), (16, 54)]]),
    "7": (58, [[(10, 14), (50, 12), (24, 90)]]),
    "8": (60, [[(34, 48), (14, 28), (32, 10), (50, 26), (14, 72), (32, 90), (52, 72), (34, 48)]]),
    "9": (60, [[(48, 30), (34, 10), (12, 24), (16, 48), (46, 46), (48, 16), (38, 90)]]),
    "-": (48, [[(8, 52), (42, 50)]]),
    "+": (56, [[(8, 52), (48, 50)], [(28, 28), (26, 74)]]),
    ".": (24, [[(10, 84), (14, 84), (14, 88), (10, 88)]]),
    "/": (48, [[(40, 10), (10, 92)]]),
    "x": (60, [[(12, 28), (48, 86)], [(48, 28), (10, 86)]]),
    "y": (60, [[(12, 28), (30, 62)], [(48, 26), (18, 96)]]),
    "O": (70, [[(38, 10), (62, 24), (60, 68), (34, 90), (10, 74), (12, 28), (38, 10)]]),
    "F": (64, [[(18, 10), (14, 90)], [(18, 12), (56, 10)], [(16, 48), (46, 46)]]),
    "N": (70, [[(14, 90), (16, 10), (52, 88), (56, 10)]]),
    "f": (46, [[(38, 14), (24, 10), (20, 90)], [(8, 44), (36, 42)]]),
    "m": (82, [[(10, 32), (10, 86)], [(10, 48), (28, 30), (42, 48), (42, 86)], [(42, 48), (62, 30), (74, 48), (74, 86)]]),
    "g": (60, [[(48, 34), (24, 28), (12, 50), (24, 70), (48, 60)], [(48, 32), (44, 92), (16, 94)]]),
    "T": (64, [[(8, 14), (56, 12)], [(34, 14), (30, 90)]]),
    "a": (58, [[(44, 34), (22, 30), (10, 56), (22, 84), (46, 72)], [(46, 32), (48, 86)]]),
    "b": (58, [[(16, 8), (14, 88)], [(16, 48), (44, 34), (50, 64), (30, 88), (14, 80)]]),
    "^": (46, [[(8, 52), (24, 24), (40, 50)]]),
    "θ": (58, [[(34, 12), (50, 28), (44, 72), (24, 90), (10, 70), (16, 26), (34, 12)], [(14, 52), (46, 48)]]),
}

_SAFE_FUNCS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "exp": math.exp,
    "log": math.log,
    "sqrt": math.sqrt,
    "abs": abs,
}


def _safe_eval_math(expr: str, x_val: float) -> float:
    """Safely evaluate simple single-variable math expression via AST whitelist."""
    tree = ast.parse(expr.strip().replace("^", "**"), mode="eval")

    def _ev(node) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in ("x", "t"):
                return float(x_val)
            if node.id == "pi":
                return math.pi
            if node.id == "e":
                return math.e
            raise ValueError(f"Unsupported variable: {node.id}")
        if isinstance(node, ast.UnaryOp):
            v = _ev(node.operand)
            if isinstance(node.op, ast.UAdd):
                return v
            if isinstance(node.op, ast.USub):
                return -v
            raise ValueError("Unsupported unary op")
        if isinstance(node, ast.BinOp):
            l = _ev(node.left)
            r = _ev(node.right)
            if isinstance(node.op, ast.Add):
                return l + r
            if isinstance(node.op, ast.Sub):
                return l - r
            if isinstance(node.op, ast.Mult):
                return l * r
            if isinstance(node.op, ast.Div):
                return l / r
            if isinstance(node.op, ast.Pow):
                return l ** r
            raise ValueError("Unsupported binary op")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
            args = [_ev(a) for a in node.args]
            return float(_SAFE_FUNCS[node.func.id](*args))
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    return _ev(tree)


def _wobble_segment(p0: Point, p1: Point, rng: random.Random, amp: float = 0.7, step: float = 5.0) -> List[Point]:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    if L < 1e-3:
        return [p0, p1]
    nx, ny = -dy / L, dx / L
    n_steps = max(2, int(math.ceil(L / step)))
    phase = rng.uniform(0, math.pi * 2)
    arch = rng.uniform(-amp, amp) * 0.75
    pts: List[Point] = []
    for i in range(n_steps + 1):
        u = i / n_steps
        bend = math.sin(u * math.pi) * arch + math.sin(u * math.pi * 2.3 + phase) * amp * 0.38
        pts.append((p0[0] + dx * u + nx * bend, p0[1] + dy * u + ny * bend))
    return pts


def _arrow_paths(p0: Point, p1: Point, rng: random.Random, half_w: float = 0.82, amp: float = 0.65, head_len: float = 5.8) -> List[str]:
    shaft = _wobble_segment(p0, p1, rng, amp=amp)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    h1 = (p1[0] - ux * head_len + nx * (head_len * 0.56), p1[1] - uy * head_len + ny * (head_len * 0.56))
    h2 = (p1[0] - ux * (head_len * 0.92) - nx * (head_len * 0.54), p1[1] - uy * (head_len * 0.92) - ny * (head_len * 0.54))
    head_pts = [h1, p1, h2]
    return [_pts_to_ribbon_d(shaft, half_w), _pts_to_ribbon_d(head_pts, half_w * 0.88)]


def _transform_svg_path(d_str: str, tx: float, ty: float, sx: float, sy: float) -> str:
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+", d_str)
    out: List[str] = []
    i = 0
    is_x = True
    while i < len(tokens):
        tk = tokens[i]
        if tk.isalpha():
            out.append(tk)
            i += 1
            is_x = True
        else:
            v = float(tk)
            out.append(f"{(tx + v * sx if is_x else ty + v * sy):.2f}")
            is_x = not is_x
            i += 1
    return " ".join(out)


def _draw_label_paths(
    text: str,
    x_px: float,
    y_px: float,
    h_px: float,
    pack,
    rng: random.Random,
    color: Optional[str] = None,
) -> List[Tuple[str, Optional[str]]]:
    """Place handwritten glyph paths from pack (or vector ribbon fallback) with zero <text> fonts."""
    out: List[Tuple[str, Optional[str]]] = []
    cur_x = x_px
    glyphs = pack.glyphs if pack is not None else {}

    for ch in str(text):
        if ch.isspace():
            cur_x += h_px * 0.35
            continue
        entry = glyphs.get(ch)
        if entry and entry.get("v") and entry.get("cat") not in ("op_minus", "frac_bar", "accent_hat", "accent_bar"):
            vs = [v for v in entry["v"] if v.get("d")]
            if vs:
                v = rng.choice(vs)
                vw = max(10.0, float(v.get("w", 60.0)))
                vh = max(10.0, float(v.get("h", 100.0)))
                cat = entry.get("cat", "cap")
                target_h = h_px * (0.76 if cat == "xh" else 0.98)
                sc = target_h / vh
                y_off = y_px + (h_px - target_h if cat == "xh" else 0.0) + rng.uniform(-0.35, 0.35)
                if v.get("fill", True):
                    td = _transform_svg_path(v["d"], cur_x, y_off, sc, sc)
                    out.append((td, color))
                cur_x += vw * sc + h_px * 0.08
                continue

        fb = _FALLBACK_STROKES.get(ch)
        if fb:
            fw, strokes = fb
            sc = h_px / 100.0
            for sp in strokes:
                pts = [(cur_x + px * sc + rng.uniform(-0.25, 0.25), y_px + py * sc + rng.uniform(-0.25, 0.25)) for px, py in sp]
                d = _pts_to_ribbon_d(pts, half_w=max(0.45, h_px * 0.048))
                if d:
                    out.append((d, color))
            cur_x += fw * sc + h_px * 0.08
        elif "\u4e00" <= ch <= "\u9fff":
            placed_cjk = False
            try:
                from ..synth.cjk_synth import load_medians_db, synthesize_personal_hanzi_variant

                mdb = load_medians_db()
                m_entry = mdb.get(ch)
                if m_entry:
                    st_model = pack.style_model if pack is not None else {}
                    rads = pack.radicals if pack is not None else {}
                    strks = pack.strokes if pack is not None else {}
                    sv = synthesize_personal_hanzi_variant(
                        ch, m_entry, st_model, rads, seed=rng.randint(1, 99999), strokes_db=strks,
                    )
                    if sv and sv.get("d"):
                        vw = max(10.0, float(sv.get("w", 92.0)))
                        vh = max(10.0, float(sv.get("h", 100.0)))
                        sc = h_px / vh
                        out.append((_transform_svg_path(sv["d"], cur_x, y_px, sc, sc), color))
                        cur_x += vw * sc + h_px * 0.06
                        placed_cjk = True
            except Exception:
                placed_cjk = False
            if not placed_cjk:
                cur_x += h_px * 0.65
        else:
            cur_x += h_px * 0.45
    return out


def _render_number_line(spec: dict, pack, rng: random.Random) -> Tuple[float, float, List[Tuple[str, Optional[str]]]]:
    w_mm = float(spec.get("width_mm", 96.0))
    h_mm = float(spec.get("height_mm", 22.0))
    W, H = w_mm * 3.0, h_mm * 3.0
    xmin, xmax = [float(v) for v in spec.get("range", [-4, 4])]
    if xmax <= xmin:
        xmax = xmin + 2.0

    pad_l, pad_r = 18.0, 22.0
    ax_y = H * 0.62
    paths: List[Tuple[str, Optional[str]]] = []

    def x_to_px(v: float) -> float:
        return pad_l + (v - xmin) / (xmax - xmin) * (W - pad_l - pad_r)

    # Axis + arrow
    for d in _arrow_paths((pad_l - 8.0, ax_y), (W - pad_r + 10.0, ax_y), rng, half_w=0.88, amp=0.55):
        paths.append((d, None))
    if spec.get("label"):
        paths.extend(_draw_label_paths(str(spec["label"]), W - pad_r + 11.0, ax_y - 10.0, 12.0, pack, rng))

    # Ticks
    ticks = spec.get("ticks")
    if ticks is None:
        step = max(1.0, round((xmax - xmin) / 6.0))
        ticks = []
        v = math.ceil(xmin)
        while v <= xmax + 1e-6:
            ticks.append(int(v) if abs(v - round(v)) < 1e-6 else round(v, 1))
            v += step

    for tv in ticks:
        tx = x_to_px(float(tv))
        tk = _wobble_segment((tx, ax_y - 3.6), (tx, ax_y + 1.8), rng, amp=0.3)
        paths.append((_pts_to_ribbon_d(tk, 0.72), None))
        lbl = str(tv)
        paths.extend(_draw_label_paths(lbl, tx - len(lbl) * 3.2, ax_y + 4.5, 11.0, pack, rng))

    # Intervals
    for idx, iv in enumerate(spec.get("intervals", [])):
        col = iv.get("color")
        f_raw = iv.get("from", xmin)
        t_raw = iv.get("to", xmax)
        inf_l = str(f_raw).lower() in ("-inf", "-infinity")
        inf_r = str(t_raw).lower() in ("inf", "+inf", "infinity")
        x0 = (pad_l - 5.0) if inf_l else x_to_px(float(f_raw))
        x1 = (W - pad_r + 4.0) if inf_r else x_to_px(float(t_raw))
        arch_h = ax_y - (18.0 + idx * 10.0)

        # Interval bridge
        left_stem = _wobble_segment((x0, ax_y), (x0, arch_h), rng, amp=0.45) if not inf_l else []
        top_bar = _wobble_segment((x0, arch_h), (x1, arch_h), rng, amp=0.55)
        right_stem = _wobble_segment((x1, arch_h), (x1, ax_y), rng, amp=0.45) if not inf_r else []
        for seg in (left_stem, top_bar, right_stem):
            if seg:
                paths.append((_pts_to_ribbon_d(seg, 0.78), col))

        if iv.get("shade", True):
            hx = x0 + 4.0
            while hx < x1 - 3.0:
                sh_line = _wobble_segment((hx, ax_y - 1.0), (min(x1, hx + 5.5), arch_h + 1.0), rng, amp=0.3)
                paths.append((_pts_to_ribbon_d(sh_line, 0.55), col))
                hx += 5.5

        for is_fin, ex, is_open in ((not inf_l, x0, bool(iv.get("open_left", False))), (not inf_r, x1, bool(iv.get("open_right", False)))):
            if not is_fin:
                continue
            r = 3.0
            ring = [(ex + math.cos(k / 14.0 * 2 * math.pi) * r, ax_y + math.sin(k / 14.0 * 2 * math.pi) * r) for k in range(15)]
            if is_open:
                cmds_w = [f"M{ring[0][0]:.2f} {ring[0][1]:.2f}"] + [f"L{x:.2f} {y:.2f}" for x, y in ring[1:]] + ["Z"]
                paths.append(("".join(cmds_w), "#ffffff"))
                paths.append((_pts_to_ribbon_d(ring, 0.85), col))
            else:
                cmds = [f"M{ring[0][0]:.2f} {ring[0][1]:.2f}"] + [f"L{x:.2f} {y:.2f}" for x, y in ring[1:]] + ["Z"]
                paths.append(("".join(cmds), col))

    return w_mm, h_mm, paths


def _render_func(spec: dict, pack, rng: random.Random) -> Tuple[float, float, List[Tuple[str, Optional[str]]]]:
    w_mm = float(spec.get("width_mm", 68.0))
    h_mm = float(spec.get("height_mm", 46.0))
    W, H = w_mm * 3.0, h_mm * 3.0
    x0, x1 = [float(v) for v in spec.get("x_range", [-2.5, 3.5])]
    y0, y1 = [float(v) for v in spec.get("y_range", [-1.5, 4.5])]
    pad = 16.0
    paths: List[Tuple[str, Optional[str]]] = []

    def mx(x: float) -> float:
        return pad + (x - x0) / max(1e-6, x1 - x0) * (W - pad * 2)

    def my(y: float) -> float:
        return H - pad - (y - y0) / max(1e-6, y1 - y0) * (H - pad * 2)

    ox = mx(max(x0, min(x1, 0.0)))
    oy = my(max(y0, min(y1, 0.0)))

    # Axes
    for d in _arrow_paths((pad - 4.0, oy), (W - pad + 6.0, oy), rng, half_w=0.82, amp=0.55):
        paths.append((d, None))
    for d in _arrow_paths((ox, H - pad + 4.0), (ox, pad - 6.0), rng, half_w=0.82, amp=0.55):
        paths.append((d, None))
    paths.extend(_draw_label_paths("x", W - pad + 6.5, oy - 7.0, 11.0, pack, rng))
    paths.extend(_draw_label_paths("y", ox + 5.0, pad - 12.0, 11.0, pack, rng))
    if x0 <= 0 <= x1 and y0 <= 0 <= y1:
        paths.extend(_draw_label_paths("O", ox - 11.0, oy + 3.0, 10.0, pack, rng))

    # Optional integral shading
    shades = spec.get("shades") or ([spec["shade"]] if spec.get("shade") else [])
    for sh in shades:
        expr = str(sh.get("expr", "x"))
        sa = float(sh.get("from", x0))
        sb = float(sh.get("to", x1))
        col = sh.get("color")
        n_bars = max(6, int(abs(mx(sb) - mx(sa)) / 4.2))
        for k in range(n_bars + 1):
            xv = sa + (sb - sa) * (k / max(1, n_bars))
            try:
                yv = _safe_eval_math(expr, xv)
            except Exception:
                continue
            if not math.isfinite(yv):
                continue
            yv_c = max(y0, min(y1, yv))
            seg = _wobble_segment((mx(xv), my(0.0 if y0 <= 0 <= y1 else y0)), (mx(xv), my(yv_c)), rng, amp=0.35)
            paths.append((_pts_to_ribbon_d(seg, 0.56), col))

    # Curves
    for cv in spec.get("curves", []):
        expr = str(cv.get("expr", "x"))
        dom = cv.get("domain") or [x0, x1]
        da, db = float(dom[0]), float(dom[1])
        col = cv.get("color")
        dashed = bool(cv.get("dashed", False))
        pts: List[Point] = []
        steps = 84
        for k in range(steps + 1):
            xv = da + (db - da) * (k / steps)
            try:
                yv = _safe_eval_math(expr, xv)
            except Exception:
                yv = float("nan")
            if math.isfinite(yv) and (y0 - 0.5) <= yv <= (y1 + 0.5):
                pts.append((mx(xv), my(max(y0, min(y1, yv)))))
            else:
                if len(pts) >= 2:
                    if dashed:
                        for i in range(0, len(pts) - 1, 3):
                            paths.append((_pts_to_ribbon_d(pts[i : i + 2], 0.85), col))
                    else:
                        paths.append((_pts_to_ribbon_d(pts, 0.92), col))
                pts = []
        if len(pts) >= 2:
            if dashed:
                for i in range(0, len(pts) - 1, 3):
                    paths.append((_pts_to_ribbon_d(pts[i : i + 2], 0.85), col))
            else:
                paths.append((_pts_to_ribbon_d(pts, 0.92), col))
            if cv.get("label"):
                lx, ly = pts[-1]
                paths.extend(_draw_label_paths(str(cv["label"]), min(W - 22.0, lx - 14.0), max(4.0, ly - 14.0), 10.5, pack, rng, col))

    # Annotated points
    for pt in spec.get("points", []):
        px, py = float(pt["x"]), float(pt["y"])
        sx, sy = mx(px), my(py)
        col = pt.get("color")
        if pt.get("dashed_proj"):
            for target in ((sx, oy), (ox, sy)):
                seg = _wobble_segment((sx, sy), target, rng, amp=0.25, step=5.0)
                for i in range(0, len(seg) - 1, 2):
                    paths.append((_pts_to_ribbon_d(seg[i : i + 2], 0.55), col))
        dot = [(sx + math.cos(k / 10.0 * 2 * math.pi) * 2.1, sy + math.sin(k / 10.0 * 2 * math.pi) * 2.1) for k in range(11)]
        paths.append(("M" + "L".join(f"{p[0]:.2f} {p[1]:.2f}" for p in dot) + "Z", col))
        if pt.get("label"):
            paths.extend(_draw_label_paths(str(pt["label"]), sx + 3.5, sy - 11.0, 10.0, pack, rng, col))

    return w_mm, h_mm, paths


def _render_fbd(spec: dict, pack, rng: random.Random) -> Tuple[float, float, List[Tuple[str, Optional[str]]]]:
    w_mm = float(spec.get("width_mm", 56.0))
    h_mm = float(spec.get("height_mm", 46.0))
    W, H = w_mm * 3.0, h_mm * 3.0
    body = spec.get("body") or {}
    shape = str(body.get("shape", "block")).lower()
    incline_deg = float(body.get("incline_deg", 28.0)) if shape == "incline" else 0.0
    paths: List[Tuple[str, Optional[str]]] = []

    cx, cy = W * 0.5, H * 0.52

    if shape == "incline":
        rad = math.radians(incline_deg)
        p_bl = (14.0, H - 16.0)
        p_br = (W - 14.0, H - 16.0)
        rise = min(H - 36.0, (p_br[0] - p_bl[0]) * math.tan(rad))
        p_top = (p_br[0], p_br[1] - rise)
        for a, b in ((p_bl, p_br), (p_br, p_top), (p_bl, p_top)):
            paths.append((_pts_to_ribbon_d(_wobble_segment(a, b, rng, amp=0.55), 0.84), None))
        # Mass block resting on slope midpoint
        mx_mid = (p_bl[0] + p_top[0]) * 0.5
        my_mid = (p_bl[1] + p_top[1]) * 0.5
        ux, uy = math.cos(rad), -math.sin(rad)
        nx, ny = math.sin(rad), math.cos(rad)  # upward slope normal has y < 0? wait: slope goes up-right so normal is (-sin, -cos)
        nx, ny = -math.sin(rad), -math.cos(rad)
        bw, bh = 22.0, 14.0
        cx, cy = mx_mid + nx * (bh * 0.55), my_mid + ny * (bh * 0.55)
        c0 = (cx - ux * bw * 0.5 - nx * bh * 0.5, cy - uy * bw * 0.5 - ny * bh * 0.5)
        c1 = (cx + ux * bw * 0.5 - nx * bh * 0.5, cy + uy * bw * 0.5 - ny * bh * 0.5)
        c2 = (cx + ux * bw * 0.5 + nx * bh * 0.5, cy + uy * bw * 0.5 + ny * bh * 0.5)
        c3 = (cx - ux * bw * 0.5 + nx * bh * 0.5, cy - uy * bw * 0.5 + ny * bh * 0.5)
        for a, b in ((c0, c1), (c1, c2), (c2, c3), (c3, c0)):
            paths.append((_pts_to_ribbon_d(_wobble_segment(a, b, rng, amp=0.4), 0.80), None))
        paths.extend(_draw_label_paths("θ", p_bl[0] + 15.0, p_bl[1] - 13.0, 9.5, pack, rng))
    elif shape == "block":
        bw, bh = 24.0, 16.0
        c0, c1, c2, c3 = (cx - bw * 0.5, cy - bh * 0.5), (cx + bw * 0.5, cy - bh * 0.5), (cx + bw * 0.5, cy + bh * 0.5), (cx - bw * 0.5, cy + bh * 0.5)
        for a, b in ((c0, c1), (c1, c2), (c2, c3), (c3, c0)):
            paths.append((_pts_to_ribbon_d(_wobble_segment(a, b, rng, amp=0.45), 0.80), None))

    dot = [(cx + math.cos(k / 10.0 * 2 * math.pi) * 2.0, cy + math.sin(k / 10.0 * 2 * math.pi) * 2.0) for k in range(11)]
    paths.append(("M" + "L".join(f"{p[0]:.2f} {p[1]:.2f}" for p in dot) + "Z", None))
    if body.get("label"):
        paths.extend(_draw_label_paths(str(body["label"]), cx - 4.0, cy - 5.0, 9.5, pack, rng))

    base_len = min(W, H) * 0.34
    for frc in spec.get("forces", []):
        ang = math.radians(float(frc.get("angle_deg", -90.0)))
        flen = base_len * float(frc.get("len", 1.0))
        col = frc.get("color")
        ex = cx + math.cos(ang) * flen
        ey = cy - math.sin(ang) * flen
        for d in _arrow_paths((cx, cy), (ex, ey), rng, half_w=0.88, amp=0.55):
            paths.append((d, col))
        if frc.get("label"):
            lx = ex + math.cos(ang) * 4.0 - 4.0
            ly = ey - math.sin(ang) * 4.0 - 5.0
            paths.extend(_draw_label_paths(str(frc["label"]), max(2.0, min(W - 18.0, lx)), max(2.0, min(H - 12.0, ly)), 10.5, pack, rng, col))

    return w_mm, h_mm, paths


def render_handplot_svg(spec: dict, pack=None, seed: int = 42) -> str:
    """Render a handplot YAML/JSON specification into a zero-font vector SVG."""
    if not isinstance(spec, dict):
        return ""
    rng = random.Random(seed)
    kind = str(spec.get("type", "func")).lower()
    if kind in ("number_line", "numberline", "axis"):
        w_mm, h_mm, paths = _render_number_line(spec, pack, rng)
    elif kind in ("fbd", "vectors", "force"):
        w_mm, h_mm, paths = _render_fbd(spec, pack, rng)
    else:
        w_mm, h_mm, paths = _render_func(spec, pack, rng)

    W, H = w_mm * 3.0, h_mm * 3.0
    p_elems: List[str] = []
    for d, col in paths:
        if not d:
            continue
        if col:
            c_esc = html.escape(str(col), quote=True)
            p_elems.append(f'<path d="{d}" data-color="{c_esc}" style="fill:{c_esc};stroke:{c_esc};"/>')
        else:
            p_elems.append(f'<path d="{d}" fill="currentColor" stroke="currentColor"/>')

    return (
        f'<svg class="pg-svg hw-sketch-svg" data-cat="struct" data-ch="sketch" '
        f'viewBox="0 0 {W:.1f} {H:.1f}" '
        f'style="width:{w_mm:.2f}mm;height:{h_mm:.2f}mm;display:block;margin:3px 0 5px 6px;">'
        + "".join(p_elems)
        + "</svg>"
    )

