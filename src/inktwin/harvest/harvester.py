"""Zero-OCR vector template harvester: extracts written ink from filled PDFs into .hwpack."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz
import numpy as np

from ..pack.facets import compute_facets
from ..pack.schema import HwPack, load_pack, save_pack_zip
from .calibrator import (
    calibrate_radical_item,
    calibrate_variant_polygon,
    measure_style_dna,
    polygon_to_stroke_dna_item,
    ribbonize_open_polyline,
)
from .templates import (
    COLOR_BOX,
    COLOR_FIDUCIAL,
    COLOR_GHOST,
    COLOR_HEADER,
    COLOR_LABEL,
    FIDUCIAL_CENTERS_MM,
    MM_TO_PT,
    read_template_spec_from_pdf,
)

PT_TO_MM = 25.4 / 72.0

_TEMPLATE_RGBS = (
    COLOR_FIDUCIAL,
    COLOR_BOX,
    COLOR_GHOST,
    COLOR_HEADER,
    COLOR_LABEL,
    (0.90, 0.93, 0.96),
    (0.92, 0.94, 0.97),
)


def _is_near_rgb(col: Optional[Tuple[float, ...]], ref: Tuple[float, float, float], tol: float = 0.08) -> bool:
    if not col or len(col) < 3:
        return False
    return max(abs(col[0] - ref[0]), abs(col[1] - ref[1]), abs(col[2] - ref[2])) <= tol


def _is_dark_ink_color(fill_col, stroke_col) -> bool:
    """Accept dark black / blue / dark-gray ink; reject white backgrounds & template guides."""
    for c in (fill_col, stroke_col):
        if c and len(c) >= 3:
            r, g, b = float(c[0]), float(c[1]), float(c[2])
            if r > 0.94 and g > 0.94 and b > 0.94:
                return False
            for tc in _TEMPLATE_RGBS:
                if max(abs(r - tc[0]), abs(g - tc[1]), abs(b - tc[2])) <= 0.07:
                    return False
            # Dark or saturated blue/indigo/black pen ink
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum < 0.72 or (b > r + 0.12 and r < 0.55):
                return True
            return False
    return True


def _sample_bezier_c(p0, p1, p2, p3, steps: int = 8) -> List[Tuple[float, float]]:
    pts = []
    for k in range(1, steps + 1):
        t = k / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _sample_quad_q(p0, p1, p2, steps: int = 6) -> List[Tuple[float, float]]:
    pts = []
    for k in range(1, steps + 1):
        t = k / steps
        mt = 1.0 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _extract_subpaths_from_drawing(dr: dict) -> List[List[Tuple[float, float]]]:
    """Split concatenated PDF drawing items into independent subpaths when jump > 0.12*pt (~0.35pt)."""
    items = dr.get("items") or []
    is_fill = dr.get("type") in ("f", "fs")
    stroke_w = float(dr.get("width") or 1.2)
    raw_subpaths: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = []

    def flush():
        nonlocal cur
        if len(cur) >= 2:
            raw_subpaths.append(cur)
        cur = []

    for it in items:
        op = it[0]
        if op == "l":
            p0 = (float(it[1].x) * PT_TO_MM, float(it[1].y) * PT_TO_MM)
            p1 = (float(it[2].x) * PT_TO_MM, float(it[2].y) * PT_TO_MM)
            if not cur:
                cur = [p0, p1]
            else:
                if math.hypot(p0[0] - cur[-1][0], p0[1] - cur[-1][1]) > 0.12:
                    flush()
                    cur = [p0, p1]
                else:
                    cur.append(p1)
        elif op == "c":
            p0 = (float(it[1].x) * PT_TO_MM, float(it[1].y) * PT_TO_MM)
            p1 = (float(it[2].x) * PT_TO_MM, float(it[2].y) * PT_TO_MM)
            p2 = (float(it[3].x) * PT_TO_MM, float(it[3].y) * PT_TO_MM)
            p3 = (float(it[4].x) * PT_TO_MM, float(it[4].y) * PT_TO_MM)
            if cur and math.hypot(p0[0] - cur[-1][0], p0[1] - cur[-1][1]) > 0.12:
                flush()
            if not cur:
                cur = [p0]
            cur.extend(_sample_bezier_c(p0, p1, p2, p3, steps=8))
        elif op == "qu":
            q = it[1]
            pts = [
                (float(q.ul.x) * PT_TO_MM, float(q.ul.y) * PT_TO_MM),
                (float(q.ur.x) * PT_TO_MM, float(q.ur.y) * PT_TO_MM),
                (float(q.lr.x) * PT_TO_MM, float(q.lr.y) * PT_TO_MM),
                (float(q.ll.x) * PT_TO_MM, float(q.ll.y) * PT_TO_MM),
            ]
            flush()
            raw_subpaths.append(pts)
        elif op == "re":
            r = it[1]
            pts = [
                (float(r.x0) * PT_TO_MM, float(r.y0) * PT_TO_MM),
                (float(r.x1) * PT_TO_MM, float(r.y0) * PT_TO_MM),
                (float(r.x1) * PT_TO_MM, float(r.y1) * PT_TO_MM),
                (float(r.x0) * PT_TO_MM, float(r.y1) * PT_TO_MM),
            ]
            flush()
            raw_subpaths.append(pts)
        elif op == "v" or op == "y":
            pass
    flush()

    if not raw_subpaths:
        return []
    if is_fill:
        return [sp for sp in raw_subpaths if len(sp) >= 3]
    # Stroked open paths (e.g. from pencil/stylus PDF annotators that do not bake ribbons)
    stroke_mm = max(0.28, min(1.2, stroke_w * PT_TO_MM * 0.5))
    return [ribbonize_open_polyline(sp, half_w=stroke_mm) for sp in raw_subpaths if len(sp) >= 2]


def _detect_fiducial_transform(drawings: List[dict], canonical_centers: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    """Detect the 4 cyan fiducial squares and solve linear (sx, tx, sy, ty) back to canonical mm."""
    cands: List[Tuple[float, float]] = []
    for dr in drawings:
        fill_c = dr.get("fill")
        stroke_c = dr.get("color")
        if not (_is_near_rgb(fill_c, COLOR_FIDUCIAL, 0.10) or _is_near_rgb(stroke_c, COLOR_FIDUCIAL, 0.10)):
            continue
        r = dr.get("rect")
        if r is None:
            continue
        w_mm = (r.x1 - r.x0) * PT_TO_MM
        h_mm = (r.y1 - r.y0) * PT_TO_MM
        if 1.5 <= w_mm <= 9.0 and 1.5 <= h_mm <= 9.0:
            cands.append(((r.x0 + r.x1) * 0.5 * PT_TO_MM, (r.y0 + r.y1) * 0.5 * PT_TO_MM))

    if len(cands) < 4 or len(canonical_centers) < 4:
        return 1.0, 0.0, 1.0, 0.0

    # Match nearest detected square to each of the 4 canonical corners
    src_pts = []
    dst_pts = []
    for cx, cy in canonical_centers[:4]:
        best = min(cands, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        if math.hypot(best[0] - cx, best[1] - cy) <= 25.0:
            src_pts.append(best)
            dst_pts.append((cx, cy))

    if len(src_pts) < 4:
        return 1.0, 0.0, 1.0, 0.0

    sx_arr = np.array([p[0] for p in src_pts], dtype=float)
    sy_arr = np.array([p[1] for p in src_pts], dtype=float)
    dx_arr = np.array([p[0] for p in dst_pts], dtype=float)
    dy_arr = np.array([p[1] for p in dst_pts], dtype=float)

    Ax = np.column_stack([sx_arr, np.ones_like(sx_arr)])
    Ay = np.column_stack([sy_arr, np.ones_like(sy_arr)])
    ax, tx = np.linalg.lstsq(Ax, dx_arr, rcond=None)[0]
    ay, ty = np.linalg.lstsq(Ay, dy_arr, rcond=None)[0]
    return float(ax), float(tx), float(ay), float(ty)


def harvest_template_pdf(
    filled_pdf: Path,
    out_pack: Path,
    pack_id: Optional[str] = None,
    merge_into: Optional[Path] = None,
    spec_override: Optional[dict] = None,
) -> dict:
    """Zero-OCR extraction of filled vector handwriting templates into a calibrated .hwpack."""
    filled_pdf = Path(filled_pdf)
    spec = spec_override or read_template_spec_from_pdf(filled_pdf)
    canonical_fiducials = [tuple(p) for p in spec.get("fiducial_centers_mm", FIDUCIAL_CENTERS_MM)]
    cells = spec.get("cells", [])

    cells_by_page: Dict[int, List[dict]] = defaultdict(list)
    for c in cells:
        cells_by_page[int(c["page"])].append(c)

    cell_polys: Dict[str, List[List[Tuple[float, float]]]] = defaultdict(list)

    with fitz.open(str(filled_pdf)) as doc:
        for pno in range(len(doc)):
            page = doc[pno]
            page_cells = cells_by_page.get(pno, [])
            if not page_cells:
                continue
            drawings = page.get_drawings()
            ax, tx, ay, ty = _detect_fiducial_transform(drawings, canonical_fiducials)

            for dr in drawings:
                if not _is_dark_ink_color(dr.get("fill"), dr.get("color")):
                    continue
                r = dr.get("rect")
                if r is None:
                    continue
                # Skip tiny dust or huge full-page border boxes
                w_mm = (r.x1 - r.x0) * PT_TO_MM * ax
                h_mm = (r.y1 - r.y0) * PT_TO_MM * ay
                if max(w_mm, h_mm) < 0.35 or w_mm > 65.0 or h_mm > 65.0:
                    continue

                subpaths = _extract_subpaths_from_drawing(dr)
                for sp in subpaths:
                    mapped = [(pt[0] * ax + tx, pt[1] * ay + ty) for pt in sp]
                    xs = [p[0] for p in mapped]
                    ys = [p[1] for p in mapped]
                    cx = (min(xs) + max(xs)) * 0.5
                    cy = (min(ys) + max(ys)) * 0.5
                    # Assign by centroid against padded cell bounding box
                    for c in page_cells:
                        bx, by, bw, bh = c["box_mm"]
                        if (bx - 1.2) <= cx <= (bx + bw + 1.2) and (by - 1.2) <= cy <= (by + bh + 1.2):
                            cell_polys[c["cell_id"]].append(mapped)
                            break

    # Load base pack if merging, else initialize clean empty pack
    base_pack: Optional[HwPack] = None
    if merge_into is not None:
        base_pack = load_pack(str(merge_into))
        glyphs = dict(base_pack.glyphs)
        radicals = dict(base_pack.radicals)
        strokes = {k: list(v) for k, v in base_pack.strokes.items()}
    else:
        glyphs = {}
        radicals = {}
        strokes = {}

    seen_d_hashes: Dict[str, set] = {
        k: {v.get("d", "") for v in entry.get("v", [])}
        for k, entry in glyphs.items()
    }

    added_glyphs = 0
    added_variants = 0
    added_strokes = 0
    added_radicals = 0
    filled_cells = 0

    for c in cells:
        polys = cell_polys.get(c["cell_id"])
        if not polys:
            continue
        filled_cells += 1
        kind = c["kind"]
        key = c["key"]

        if kind == "stroke":
            # Every isolated subpath inside a stroke cell contributes a stroke DNA sample
            for sp in polys:
                res = polygon_to_stroke_dna_item([sp], forced_type=key)
                if res:
                    stype, item = res
                    strokes.setdefault(stype, []).append(item)
                    added_strokes += 1
        elif kind == "radical":
            item = calibrate_radical_item(polys)
            if item:
                radicals.setdefault(key, []).append(item)
                added_radicals += 1
        else:
            var = calibrate_variant_polygon(polys, norm_mode=c.get("norm_mode", "height100"), is_fill=True)
            if not var or not var.get("d"):
                continue
            d_str = var["d"]
            if d_str in seen_d_hashes.get(key, set()):
                continue
            seen_d_hashes.setdefault(key, set()).add(d_str)
            if key not in glyphs:
                glyphs[key] = {"cat": c.get("cat", "cap"), "v": []}
                added_glyphs += 1
            glyphs[key]["v"].append(var)
            added_variants += 1

    style_model = measure_style_dna(glyphs, strokes)
    facets = compute_facets(glyphs, style_model)
    pid = pack_id or (base_pack.id if base_pack else Path(out_pack).stem)
    manifest = {
        "schema": "inktwin-hwpack-v1",
        "id": pid,
        "name": f"{pid} Handwriting Pack",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "privacy": "local",
        "facets": facets,
        "stats": {
            "total_chars": len(glyphs),
            "real_cjk_chars": sum(1 for v in glyphs.values() if v.get("cat") == "cjk"),
            "variants": sum(len(v.get("v", [])) for v in glyphs.values()),
            "radicals": len(radicals),
            "strokes": sum(len(v) for v in strokes.values()),
        },
        "source": f"template:{filled_pdf.name}",
    }
    save_pack_zip(Path(out_pack), manifest, glyphs, style_model, radicals, strokes)

    return {
        "pack": str(Path(out_pack).resolve()),
        "id": pid,
        "filled_cells": filled_cells,
        "total_cells": len(cells),
        "added_glyphs": added_glyphs,
        "added_variants": added_variants,
        "added_strokes": added_strokes,
        "added_radicals": added_radicals,
        "total_chars": len(glyphs),
        "total_variants": sum(len(v.get("v", [])) for v in glyphs.values()),
        "facets": facets,
    }

