"""Build true .ttf / .otf fonts with GSUB calt contextual alternates from a .hwpack."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen

from ..pack.schema import HwPack, load_pack

UPM = 1000
ASCENDER = 880
DESCENDER = -120

_TOKEN_RE = re.compile(r"[MLQCZ]|-?\d*\.?\d+")


def _signed_area(pts: List[Tuple[float, float]]) -> float:
    area = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def _point_in_poly(pt: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if ((y0 > y) != (y1 > y)) and (x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0):
            inside = not inside
    return inside


def _parse_svg_loops(d_str: str) -> List[List[Tuple[float, float]]]:
    """Parse SVG path into closed loops, flattening quadratic/cubic curves smoothly."""
    tokens = _TOKEN_RE.findall(d_str or "")
    idx = 0
    cx = cy = sx = sy = 0.0
    loops: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = []
    cmd: Optional[str] = None

    def flush():
        nonlocal cur
        if len(cur) >= 3:
            # Strip closing duplicate point if matches start
            if math.hypot(cur[-1][0] - cur[0][0], cur[-1][1] - cur[0][1]) < 0.05:
                cur = cur[:-1]
            if len(cur) >= 3:
                loops.append(cur)
        cur = []

    while idx < len(tokens):
        tk = tokens[idx]
        if tk.isalpha():
            cmd = tk
            idx += 1
            if cmd == "Z":
                flush()
            continue
        if cmd == "M":
            flush()
            cx, cy = float(tokens[idx]), float(tokens[idx + 1])
            sx, sy = cx, cy
            cur = [(cx, cy)]
            idx += 2
            cmd = "L"
        elif cmd == "L":
            cx, cy = float(tokens[idx]), float(tokens[idx + 1])
            cur.append((cx, cy))
            idx += 2
        elif cmd == "Q":
            qx, qy = float(tokens[idx]), float(tokens[idx + 1])
            ex, ey = float(tokens[idx + 2]), float(tokens[idx + 3])
            p0x, p0y = cx, cy
            for k in range(1, 7):
                t = k / 6.0
                mt = 1.0 - t
                cur.append((mt * mt * p0x + 2 * mt * t * qx + t * t * ex, mt * mt * p0y + 2 * mt * t * qy + t * t * ey))
            cx, cy = ex, ey
            idx += 4
        elif cmd == "C":
            c1x, c1y = float(tokens[idx]), float(tokens[idx + 1])
            c2x, c2y = float(tokens[idx + 2]), float(tokens[idx + 3])
            ex, ey = float(tokens[idx + 4]), float(tokens[idx + 5])
            p0x, p0y = cx, cy
            for k in range(1, 9):
                t = k / 8.0
                mt = 1.0 - t
                cur.append(
                    (
                        mt**3 * p0x + 3 * mt**2 * t * c1x + 3 * mt * t**2 * c2x + t**3 * ex,
                        mt**3 * p0y + 3 * mt**2 * t * c1y + 3 * mt * t**2 * c2y + t**3 * ey,
                    )
                )
            cx, cy = ex, ey
            idx += 6
        else:
            idx += 1
    flush()
    return loops


def _metric_box_for_cat(cat: str, vw: float, vh: float) -> Tuple[float, float, int]:
    """Return (scale, y_bottom_upm, lsb_upm) mapping SVG (0..vw, 0..vh) to Y-up font UPM."""
    safe_h = max(1.0, vh)
    safe_w = max(1.0, vw)
    if cat == "cjk":
        return 800.0 / safe_h, -60.0, 40
    if cat in ("asc", "tall"):
        return 740.0 / safe_h, 0.0, 50
    if cat == "desc":
        return 720.0 / safe_h, -180.0, 50
    if cat in ("xh",):
        return 520.0 / safe_h, 0.0, 50
    if cat == "op_minus":
        return 460.0 / safe_w, 260.0 - (safe_h * (460.0 / safe_w)) * 0.5, 65
    if cat == "op_eq":
        return 220.0 / safe_h, 150.0, 65
    if cat in ("op", "op_wide"):
        target_h = 460.0 if safe_w / safe_h < 1.35 else 420.0
        return target_h / safe_h, 260.0 - target_h * 0.5, 60
    if cat in ("dot",):
        return 130.0 / safe_h, 0.0, 60
    if cat == "dots_h":
        return 480.0 / safe_w, 40.0, 60
    if cat == "punct":
        return 300.0 / safe_h, -40.0, 55
    if cat in ("deg", "accent_hat", "accent_bar"):
        return 220.0 / safe_h, 520.0, 45
    if cat == "bigop":
        return 760.0 / safe_h, -30.0, 55
    return 720.0 / safe_h, 0.0, 50


def _normalize_contours_for_font(
    loops: List[List[Tuple[float, float]]],
    vw: float,
    vh: float,
    cat: str,
    is_cff: bool,
) -> Tuple[List[List[Tuple[int, int]]], int]:
    """Map SVG loops into Y-up font coords, deduplicate points, and enforce TTF/CFF contour winding."""
    sc, y_bot, lsb = _metric_box_for_cat(cat, vw, vh)
    adv_w = max(180, int(round(vw * sc + lsb * 2)))

    mapped: List[List[Tuple[float, float]]] = []
    for lp in loops:
        pts_u = [(lsb + x * sc, y_bot + (vh - y) * sc) for x, y in lp]
        if abs(_signed_area(pts_u)) >= 1.5 and len(pts_u) >= 3:
            mapped.append(pts_u)

    if not mapped:
        return [], adv_w

    # Compute topological nesting depth: even = outer shell, odd = inner hole
    depths: List[int] = []
    for i, lp in enumerate(mapped):
        probe = lp[0]
        d = 0
        for j, other in enumerate(mapped):
            if i != j and _point_in_poly(probe, other):
                d += 1
        depths.append(d)

    out: List[List[Tuple[int, int]]] = []
    for lp, depth in zip(mapped, depths):
        area = _signed_area(lp)
        is_hole = (depth % 2) == 1
        # TTF requires CW outer (area < 0) & CCW hole (area > 0)
        # CFF (.otf) requires CCW outer (area > 0) & CW hole (area < 0)
        want_positive = (not is_hole) if is_cff else is_hole
        ordered = list(reversed(lp)) if ((area > 0) != want_positive) else list(lp)

        rounded: List[Tuple[int, int]] = []
        for x, y in ordered:
            ix, iy = int(round(x)), int(round(y))
            if not rounded or (ix, iy) != rounded[-1]:
                rounded.append((ix, iy))
        if len(rounded) >= 2 and rounded[-1] == rounded[0]:
            rounded.pop()
        if len(rounded) >= 3:
            out.append(rounded)

    return out, adv_w


def _draw_contours_to_pen(pen, contours: List[List[Tuple[int, int]]]) -> None:
    for pts in contours:
        pen.moveTo(pts[0])
        for pt in pts[1:]:
            pen.lineTo(pt)
        pen.closePath()


def _glyph_name_for_char(ch: str) -> str:
    cp = ord(ch)
    return f"u{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"


def build_opentype_font(
    pack_or_path: Optional[HwPack | str | Path],
    out_path: Path,
    fmt: str = "ttf",
    family_name: Optional[str] = None,
    max_variants: int = 3,
) -> dict:
    """Compile a calibrated .hwpack into a standard .ttf or .otf font with GSUB calt alternates."""
    pack = pack_or_path if isinstance(pack_or_path, HwPack) else load_pack(str(pack_or_path) if pack_or_path else None)
    ext = fmt.lower().lstrip(".")
    if ext not in ("ttf", "otf"):
        raise ValueError(f"Unsupported font format '{fmt}'; use 'ttf' or 'otf'.")
    is_cff = ext == "otf"

    fam = family_name or f"InkTwin-{pack.id}"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    glyph_order: List[str] = [".notdef", "space"]
    cmap: Dict[int, str] = {0x0020: "space"}
    metrics: Dict[str, Tuple[int, int]] = {".notdef": (500, 0), "space": (320, 0)}
    glyf_data: Dict[str, object] = {}

    base_2: List[str] = []
    alt1_2: List[str] = []
    base_3: List[str] = []
    alt1_3: List[str] = []
    alt2_3: List[str] = []

    def _compile_empty(gname: str, adv: int):
        if is_cff:
            pen = T2CharStringPen(adv, None)
            glyf_data[gname] = pen.getCharString()
        else:
            pen = TTGlyphPen(None)
            glyf_data[gname] = pen.glyph()
        metrics[gname] = (adv, 0)

    # Simple hollow box for .notdef + empty space
    if is_cff:
        p_nd = T2CharStringPen(500, None)
        p_nd.moveTo((60, 0)); p_nd.lineTo((440, 0)); p_nd.lineTo((440, 700)); p_nd.lineTo((60, 700)); p_nd.closePath()
        p_nd.moveTo((110, 50)); p_nd.lineTo((110, 650)); p_nd.lineTo((390, 650)); p_nd.lineTo((390, 50)); p_nd.closePath()
        glyf_data[".notdef"] = p_nd.getCharString()
    else:
        p_nd = TTGlyphPen(None)
        p_nd.moveTo((60, 0)); p_nd.lineTo((60, 700)); p_nd.lineTo((440, 700)); p_nd.lineTo((440, 0)); p_nd.closePath()
        p_nd.moveTo((110, 50)); p_nd.lineTo((390, 50)); p_nd.lineTo((390, 650)); p_nd.lineTo((110, 650)); p_nd.closePath()
        glyf_data[".notdef"] = p_nd.glyph()
    _compile_empty("space", 320)

    def _add_variant_glyph(gname: str, vdict: dict, cat: str) -> bool:
        loops = _parse_svg_loops(vdict.get("d", ""))
        contours, adv_w = _normalize_contours_for_font(
            loops, float(vdict.get("w", 70.0)), float(vdict.get("h", 100.0)), cat, is_cff=is_cff
        )
        if not contours:
            return False
        if is_cff:
            pen = T2CharStringPen(adv_w, None)
            _draw_contours_to_pen(pen, contours)
            glyf_data[gname] = pen.getCharString()
        else:
            pen = TTGlyphPen(None)
            _draw_contours_to_pen(pen, contours)
            glyf_data[gname] = pen.glyph()
        lsb_actual = min(pt[0] for c in contours for pt in c)
        metrics[gname] = (adv_w, lsb_actual)
        glyph_order.append(gname)
        return True

    compiled_chars = 0
    for ch, entry in sorted(pack.glyphs.items(), key=lambda kv: ord(kv[0][0]) if len(kv[0]) == 1 else 999999):
        if len(ch) != 1:
            continue
        variants = [v for v in entry.get("v", []) if isinstance(v, dict) and v.get("d")][:max(1, max_variants)]
        if not variants:
            continue
        cat = entry.get("cat", "cap")
        gbase = _glyph_name_for_char(ch)
        if not _add_variant_glyph(gbase, variants[0], cat):
            continue
        cmap[ord(ch)] = gbase
        compiled_chars += 1

        if len(variants) >= 2:
            g1 = f"{gbase}.alt1"
            if _add_variant_glyph(g1, variants[1], cat):
                base_2.append(gbase)
                alt1_2.append(g1)
                if len(variants) >= 3:
                    g2 = f"{gbase}.alt2"
                    if _add_variant_glyph(g2, variants[2], cat):
                        base_3.append(gbase)
                        alt1_3.append(g1)
                        alt2_3.append(g2)

    fb = FontBuilder(UPM, isTTF=(not is_cff))
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    if is_cff:
        fb.setupCFF(
            psName=re.sub(r"[^A-Za-z0-9-]", "", fam),
            fontInfo={"FullName": fam, "FamilyName": fam},
            charStringsDict=glyf_data,
            privateDict={"defaultWidthX": 500, "nominalWidthX": 0},
        )
    else:
        fb.setupGlyf(glyf_data)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ASCENDER, descent=DESCENDER)
    fb.setupNameTable({"familyName": fam, "styleName": "Regular"})
    fb.setupOS2(
        sTypoAscender=ASCENDER,
        sTypoDescender=DESCENDER,
        sTypoLineGap=0,
        usWinAscent=ASCENDER + 40,
        usWinDescent=abs(DESCENDER) + 40,
        sxHeight=520,
        sCapHeight=720,
    )
    fb.setupPost(isFixedPitch=0)
    if not is_cff:
        fb.setupMaxp()

    # Build OpenType GSUB calt contextual alternates (cycles BASE -> ALT1 -> ALT2 -> BASE)
    fea_lines = [
        "languagesystem DFLT dflt;",
        "languagesystem latn dflt;",
        "languagesystem hani dflt;",
    ]
    if base_2:
        fea_lines.append(f"@G_BASE = [{' '.join(base_2)}];")
        fea_lines.append(f"@G_ALT1 = [{' '.join(alt1_2)}];")
    if base_3:
        fea_lines.append(f"@G3_BASE = [{' '.join(base_3)}];")
        fea_lines.append(f"@G3_ALT1 = [{' '.join(alt1_3)}];")
        fea_lines.append(f"@G3_ALT2 = [{' '.join(alt2_3)}];")

    if base_2:
        fea_lines.append("feature calt {")
        fea_lines.append("    lookup calt_cycle_1 {")
        fea_lines.append("        sub @G_BASE @G_BASE' by @G_ALT1;")
        fea_lines.append("    } calt_cycle_1;")
        if base_3:
            fea_lines.append("    lookup calt_cycle_2 {")
            fea_lines.append("        sub @G3_ALT1 @G3_BASE' by @G3_ALT2;")
            fea_lines.append("    } calt_cycle_2;")
        fea_lines.append("} calt;")
        addOpenTypeFeaturesFromString(fb.font, "\n".join(fea_lines))

    fb.save(str(out_path))
    return {
        "font": str(out_path.resolve()),
        "format": ext,
        "family": fam,
        "pack": pack.id,
        "glyph_count": len(glyph_order),
        "mapped_chars": compiled_chars,
        "calt_alt1_pairs": len(base_2),
        "calt_alt2_triples": len(base_3),
    }

