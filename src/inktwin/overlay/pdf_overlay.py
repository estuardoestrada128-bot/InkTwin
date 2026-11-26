"""Blank PDF worksheet whitespace slot detection, declarative layout boxes/marks & vector overlay."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz

from ..scene.renderer import render_page_svg
from ..scene.schema import resolve_pen_color

PT_TO_MM = 25.4 / 72.0

_Q_PATTERNS = [
    re.compile(r"第\s*(\d+)\s*题"),
    re.compile(r"【\s*题\s*(\d+)\s*】"),
    re.compile(r"^\s*(?:Problem|Question|Q)\.?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"^\s*(\d+)\s*[\.、\)）]"),
]


def _match_qnum(text: str) -> Optional[int]:
    t = text.strip()
    if not t:
        return None
    for pat in _Q_PATTERNS:
        m = pat.search(t)
        if m:
            try:
                val = int(m.group(1))
                if 1 <= val <= 99:
                    return val
            except ValueError:
                pass
    return None


def _merge_vertical_bands(intervals: List[Tuple[float, float]], merge_gap: float = 4.0) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    srt = sorted(intervals, key=lambda x: x[0])
    merged = [srt[0]]
    for y0, y1 in srt[1:]:
        py0, py1 = merged[-1]
        if y0 <= py1 + merge_gap:
            merged[-1] = (py0, max(py1, y1))
        else:
            merged.append((y0, y1))
    return merged


def detect_pdf_layout(pdf_path: Path, min_slot_h_mm: float = 15.0) -> dict:
    """Inspect a blank worksheet/exam PDF and detect obstacles, question numbers, and blank answer boxes."""
    pdf_path = Path(pdf_path)
    pages_meta: List[dict] = []
    obstacles: List[dict] = []
    boxes: List[dict] = []
    active_qnum = 0

    with fitz.open(str(pdf_path)) as doc:
        for pno in range(len(doc)):
            page = doc[pno]
            w_mm = round(float(page.rect.width) * PT_TO_MM, 2)
            h_mm = round(float(page.rect.height) * PT_TO_MM, 2)
            pages_meta.append({"page": pno, "width_mm": w_mm, "height_mm": h_mm})

            page_obs: List[dict] = []
            page_anchors: List[Tuple[float, int]] = []

            # 1. Text blocks & question anchors
            tdict = page.get_text("dict")
            for blk in tdict.get("blocks", []):
                btype = blk.get("type", 0)
                bbox = blk.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = [float(v) * PT_TO_MM for v in bbox]
                if btype == 1:
                    page_obs.append({
                        "page": pno,
                        "kind": "image",
                        "x": round(x0, 2),
                        "y": round(y0, 2),
                        "w": round(max(0.5, x1 - x0), 2),
                        "h": round(max(0.5, y1 - y0), 2),
                    })
                    continue

                lines_txt: List[str] = []
                for ln in blk.get("lines", []):
                    ltxt = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
                    if ltxt:
                        lines_txt.append(ltxt)
                        lb = ln.get("bbox")
                        ly0 = float(lb[1]) * PT_TO_MM if lb else y0
                        if 6.0 <= ly0 <= h_mm - 12.0:
                            qn = _match_qnum(ltxt)
                            if qn is not None:
                                page_anchors.append((ly0, qn))
                full_txt = " ".join(lines_txt).strip()
                if full_txt:
                    page_obs.append({
                        "page": pno,
                        "kind": "text",
                        "text": full_txt[:120],
                        "x": round(x0, 2),
                        "y": round(y0, 2),
                        "w": round(max(0.5, x1 - x0), 2),
                        "h": round(max(0.5, y1 - y0), 2),
                    })

            # 2. Vector drawings: distinguish diagrams (obstacles) vs empty answer frames
            framed_slots: List[Tuple[float, float, float, float]] = []
            for dr in page.get_drawings():
                r = dr.get("rect")
                if r is None:
                    continue
                x0, y0, x1, y1 = r.x0 * PT_TO_MM, r.y0 * PT_TO_MM, r.x1 * PT_TO_MM, r.y1 * PT_TO_MM
                bw, bh = x1 - x0, y1 - y0
                # Ignore full-page paper fills and tiny divider rules
                if bw > w_mm * 0.90 and bh > h_mm * 0.90:
                    continue
                if bh < 1.2 and bw > 5.0:
                    continue
                # Large empty bordered rectangle = explicit framed answer slot!
                if dr.get("fill") is None and bw > w_mm * 0.45 and bh >= min_slot_h_mm and len(dr.get("items", [])) <= 6:
                    framed_slots.append((x0, y0, bw, bh))
                    continue
                if max(bw, bh) >= 3.0:
                    page_obs.append({
                        "page": pno,
                        "kind": "diagram",
                        "x": round(x0, 2),
                        "y": round(y0, 2),
                        "w": round(max(0.5, bw), 2),
                        "h": round(max(0.5, bh), 2),
                    })

            obstacles.extend(page_obs)
            page_anchors.sort(key=lambda a: a[0])

            # Compute open vertical bands avoiding all page obstacles
            occ_intervals = [
                (obs["y"], obs["y"] + obs["h"])
                for obs in page_obs
                if obs["y"] + obs["h"] > 10.0 and obs["y"] < h_mm - 10.0
            ]
            merged_occ = _merge_vertical_bands(occ_intervals, merge_gap=3.8)

            # Candidate blank gaps between merged obstacle bands + trailing bottom blank
            gaps: List[Tuple[float, float]] = []
            for i in range(len(merged_occ) - 1):
                g_top = merged_occ[i][1] + 2.2
                g_bot = merged_occ[i + 1][0] - 2.2
                if g_bot - g_top >= min_slot_h_mm:
                    gaps.append((g_top, g_bot))
            if merged_occ:
                last_bot = merged_occ[-1][1] + 2.5
                if (h_mm - 14.0) - last_bot >= min_slot_h_mm:
                    gaps.append((last_bot, h_mm - 14.0))
            elif page_anchors:
                gaps.append((18.0, h_mm - 14.0))

            first_anchor_y = page_anchors[0][0] if page_anchors else (h_mm + 10.0 if active_qnum == 0 else -1.0)
            slot_idx = 0
            for gy0, gy1 in gaps:
                if gy0 < first_anchor_y and active_qnum == 0:
                    # Whitespace inside top document header before Q1 starts: do not place answers there
                    continue
                q_own = active_qnum
                for ay, qn in page_anchors:
                    if ay <= gy0:
                        q_own = qn
                if q_own <= 0:
                    q_own = 1
                boxes.append({
                    "id": f"p{pno}_q{q_own}_{slot_idx}",
                    "page": pno,
                    "qnum": q_own,
                    "kind": "rect",
                    "x_mm": 16.0,
                    "y_mm": round(gy0, 2),
                    "w_mm": round(max(40.0, w_mm - 32.0), 2),
                    "h_mm": round(gy1 - gy0, 2),
                })
                slot_idx += 1

            for fx, fy, fw, fh in framed_slots:
                fcx, fcy = fx + fw * 0.5, fy + fh * 0.5
                already_covered = any(
                    b["page"] == pno
                    and b["x_mm"] <= fcx <= (b["x_mm"] + b["w_mm"])
                    and b["y_mm"] <= fcy <= (b["y_mm"] + b["h_mm"])
                    for b in boxes
                )
                if already_covered:
                    continue
                q_own = active_qnum
                for ay, qn in page_anchors:
                    if ay <= fy:
                        q_own = qn
                if q_own <= 0:
                    q_own = 1
                boxes.append({
                    "id": f"p{pno}_q{q_own}_{slot_idx}",
                    "page": pno,
                    "qnum": q_own,
                    "kind": "frame",
                    "x_mm": round(fx + 1.5, 2),
                    "y_mm": round(fy + 1.5, 2),
                    "w_mm": round(max(20.0, fw - 3.0), 2),
                    "h_mm": round(max(10.0, fh - 3.0), 2),
                })
                slot_idx += 1

            # Update carry-over qnum for next page
            if page_anchors:
                active_qnum = page_anchors[-1][1]

    return {
        "schema": "inktwin-layout-v1",
        "source_pdf": str(pdf_path.resolve()),
        "pages": pages_meta,
        "boxes": boxes,
        "marks": [],
        "obstacles": obstacles,
    }


def apply_marks_to_scene(scene_data: dict, marks: Optional[List[dict]]) -> None:
    """Apply declarative layout marks (color / scale by line, qnum, or text match) onto captured scene."""
    if not marks:
        return
    line_by_id = {ln["id"]: ln for ln in scene_data.get("lines", [])}
    for m in marks:
        if not isinstance(m, dict):
            continue
        col = resolve_pen_color(m.get("color"))
        scale = m.get("scale")
        target_q = m.get("qnum")
        target_line = m.get("line")
        match_str = m.get("match")

        for g in scene_data.get("glyphs", []):
            if target_q is not None and str(g.get("qnum", "")) != str(target_q):
                continue
            if target_line and g.get("line") != target_line:
                continue
            if match_str:
                ln_txt = (line_by_id.get(g.get("line", "")) or {}).get("text", "")
                if match_str not in ln_txt and match_str != g.get("ch", ""):
                    continue
            if col:
                g["color"] = col
            if scale is not None:
                k = float(scale)
                g["w"] = round(g["w"] * k, 3)
                g["h"] = round(g["h"] * k, 3)


def stamp_scene_onto_pdf(
    base_pdf: Path,
    resolved_scene: dict,
    out_pdf: Path,
    pen_type: str = "gel",
    dpi: int = 300,
    seed: int = 42,
    nib_mm: float = 0.5,
    ink_override: Optional[str] = None,
) -> dict:
    """Composite transparent handwritten ink from InkScene cleanly onto base_pdf."""
    base_pdf = Path(base_pdf)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    if pen_type != "gel" or abs(float(nib_mm or 0.5) - 0.5) > 0.02:
        from ..pen.physics import render_scene_with_pen
        written = render_scene_with_pen(
            resolved_scene,
            out_pdf=out_pdf,
            fmt="pdf",
            pen_type=pen_type,
            dpi=dpi,
            transparent=False,
            seed=seed,
            base_pdf=base_pdf,
            nib_mm=float(nib_mm or 0.5),
            ink_override=ink_override,
        )
        return {"ok": True, "pdf": str(out_pdf.resolve()), "pen_type": pen_type, "nib_mm": float(nib_mm or 0.5), "vector_overlay": False, "outputs": written}

    with fitz.open(str(base_pdf)) as doc:
        n_pages = len(doc)
        for pno in range(n_pages):
            svg_str = render_page_svg(pno, resolved_scene, transparent=True, ink_override=ink_override)
            with fitz.open(stream=svg_str.encode("utf-8"), filetype="svg") as sdoc:
                ov_pdf_bytes = sdoc.convert_to_pdf()
            with fitz.open("pdf", ov_pdf_bytes) as ov_doc:
                doc[pno].show_pdf_page(doc[pno].rect, ov_doc, 0, overlay=True)
        doc.save(str(out_pdf), deflate=True)

    return {"ok": True, "pdf": str(out_pdf.resolve()), "pen_type": pen_type, "vector_overlay": True, "pages": n_pages}

