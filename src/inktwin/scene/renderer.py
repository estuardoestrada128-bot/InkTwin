"""Fast native SVG & vector PDF exporter for InkScene (< 0.25s per render)."""

from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Dict, List, Optional

import fitz

from .schema import load_inkscene, resolve_scene_state


def _paper_svg_background(paper: dict, w_mm: float, h_mm: float) -> List[str]:
    kind = paper.get("kind", "blank")
    bg = paper.get("bg_color", "#ffffff")
    elems = [f'<rect x="0" y="0" width="{w_mm:.2f}" height="{h_mm:.2f}" fill="{bg}"/>']
    margins = paper.get("margins_mm") or [13.0, 14.0, 13.0, 14.0]
    m_top = float(margins[0])
    pitch = float(paper.get("pitch_mm") or 6.2)

    if kind == "grid":
        col = "#cfd5df"
        x = pitch
        while x < w_mm:
            elems.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{h_mm:.2f}" stroke="{col}" stroke-width="0.18"/>')
            x += pitch
        y = pitch
        while y < h_mm:
            elems.append(f'<line x1="0" y1="{y:.2f}" x2="{w_mm:.2f}" y2="{y:.2f}" stroke="{col}" stroke-width="0.18"/>')
            y += pitch
    elif kind == "ruled":
        p = float(paper.get("pitch_mm") or 8.0)
        mx = float(paper.get("margin_line_x_mm") or 22.0)
        col = "#a8bfd9"
        y = m_top + p
        while y < h_mm - 12.0:
            elems.append(f'<line x1="10" y1="{y:.2f}" x2="{w_mm - 10:.2f}" y2="{y:.2f}" stroke="{col}" stroke-width="0.22"/>')
            y += p
        elems.append(f'<line x1="{mx:.2f}" y1="12" x2="{mx:.2f}" y2="{h_mm - 12:.2f}" stroke="#e3a49b" stroke-width="0.32"/>')
    elif kind == "dots":
        p = float(paper.get("pitch_mm") or 5.0)
        y = p
        while y < h_mm:
            x = p
            while x < w_mm:
                elems.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.22" fill="#a8aeb8"/>')
                x += p
            y += p
    elif kind == "compo":
        p = float(paper.get("pitch_mm") or 9.0)
        m_left = float(margins[3])
        row_h = p * 1.33
        y = m_top
        for _ in range(20):
            if y + p > h_mm - 10:
                break
            elems.append(f'<rect x="{m_left:.2f}" y="{y:.2f}" width="{20 * p:.2f}" height="{p:.2f}" fill="none" stroke="#6c9f80" stroke-width="0.22"/>')
            for c in range(1, 20):
                cx = m_left + c * p
                elems.append(f'<line x1="{cx:.2f}" y1="{y:.2f}" x2="{cx:.2f}" y2="{y + p:.2f}" stroke="#6c9f80" stroke-width="0.18"/>')
            y += row_h
    return elems


def render_page_svg(page_idx: int, resolved: dict, transparent: bool = False) -> str:
    """Render one InkScene page into standalone physical-mm SVG."""
    paper = resolved.get("paper") or {}
    w_mm = float(paper.get("width_mm") or 210.0)
    h_mm = float(paper.get("height_mm") or 297.0)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_mm:.2f}mm" height="{h_mm:.2f}mm" viewBox="0 0 {w_mm:.2f} {h_mm:.2f}">'
    ]
    if not transparent:
        parts.extend(_paper_svg_background(paper, w_mm, h_mm))

    px_to_mm = 25.4 / 96.0
    for g in resolved.get("glyphs", []):
        if g.get("hidden") or int(g.get("page", 0)) != page_idx:
            continue
        vb = g.get("vb") or [0.0, 0.0, 100.0, 100.0]
        vw = max(1e-3, float(vb[2]))
        vh = max(1e-3, float(vb[3]))
        gw = max(0.05, float(g["w"]))
        gh = max(0.05, float(g["h"]))
        sx = gw / vw
        sy = gh / vh
        scale_avg = max(1e-4, (sx + sy) * 0.5)
        x = float(g["x"])
        y = float(g["y"])
        rot = float(g.get("rot", 0.0))
        color = html.escape(str(g.get("color") or "#111318"), quote=True)
        gid = html.escape(str(g.get("id", "")), quote=True)
        line_id = html.escape(str(g.get("line", "")), quote=True)
        ch_attr = html.escape(str(g.get("ch", "")), quote=True)
        sw_add = float(g.get("sw_add", 0.0))

        vx0 = float(vb[0])
        vy0 = float(vb[1])
        tf = f"translate({x:.3f},{y:.3f}) rotate({rot:.2f},{gw * 0.5:.3f},{gh * 0.5:.3f}) scale({sx:.5f},{sy:.5f})"
        if abs(vx0) > 1e-4 or abs(vy0) > 1e-4:
            tf += f" translate({-vx0:.3f},{-vy0:.3f})"

        parts.append(f'<g class="sc-glyph" id="{gid}" data-line="{line_id}" data-ch="{ch_attr}" transform="{tf}">')
        for p in g.get("paths", []):
            d = p.get("d", "")
            if not d:
                continue
            is_fill = bool(p.get("fill", True))
            raw_sw = max(0.03, float(p.get("sw", 0.04 if is_fill else 0.74)) + sw_add)
            sw_vb = (raw_sw * px_to_mm) / scale_avg
            fill_attr = color if is_fill else "none"
            p_col = html.escape(str(p.get("color") or color), quote=True)
            p_fill = p_col if is_fill else "none"
            parts.append(
                f'<path d="{d}" fill="{p_fill}" stroke="{p_col}" stroke-width="{sw_vb:.3f}" stroke-linecap="round" stroke-linejoin="round"/>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


def export_scene(
    scene_path: Path,
    out_pdf: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    fmt: str = "pdf",
    dpi: int = 300,
    transparent: bool = False,
    pen_type: str = "gel",
    seed: int = 42,
) -> dict:
    """Re-render an InkScene (applying edits.json) to vector PDF, SVG, or pen-textured PNG/PDF."""
    t0 = time.perf_counter()
    scene, edits, scene_dir = load_inkscene(scene_path)
    resolved = resolve_scene_state(scene, edits)
    n_pages = max(1, int(resolved.get("pages_detected", 1)))
    bg_pdf_raw = resolved.get("background_pdf")
    bg_pdf = Path(bg_pdf_raw) if (bg_pdf_raw and not transparent and Path(bg_pdf_raw).exists()) else None

    if pen_type != "gel" and fmt in ("pdf", "png"):
        from ..pen.physics import render_scene_with_pen
        target_pdf = (out_pdf or (scene_dir / "edited.pdf")).resolve() if (out_pdf is not None or fmt == "pdf") else None
        written = render_scene_with_pen(
            resolved,
            out_pdf=target_pdf,
            out_dir=out_dir or (scene_dir / "export"),
            fmt=fmt,
            pen_type=pen_type,
            dpi=dpi,
            transparent=transparent,
            seed=seed,
            base_pdf=bg_pdf,
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "ok": True,
            "scene_dir": str(scene_dir),
            "pages": n_pages,
            "edits_applied": len(edits),
            "format": fmt,
            "pen_type": pen_type,
            "transparent": transparent,
            "outputs": written,
            "elapsed_ms": elapsed_ms,
        }

    svgs = [render_page_svg(pi, resolved, transparent=(transparent or bg_pdf is not None)) for pi in range(n_pages)]

    written: List[str] = []
    if fmt in ("svg", "png"):
        target_dir = (out_dir or (out_pdf.parent if out_pdf else scene_dir / "export")).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        for pi, svg_str in enumerate(svgs):
            if fmt == "svg":
                fp = target_dir / f"page-{pi + 1}.svg"
                fp.write_text(svg_str, encoding="utf-8")
                written.append(str(fp))
            else:
                fp = target_dir / f"page-{pi + 1}.png"
                with fitz.open(stream=svg_str.encode("utf-8"), filetype="svg") as sdoc:
                    pdf_bytes = sdoc.convert_to_pdf()
                with fitz.open("pdf", pdf_bytes) as pdoc:
                    pix = pdoc[0].get_pixmap(dpi=dpi, alpha=transparent)
                    pix.save(str(fp))
                written.append(str(fp))

    if out_pdf is not None or fmt == "pdf":
        target_pdf = (out_pdf or (scene_dir / "edited.pdf")).resolve()
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        if bg_pdf is not None:
            from ..overlay.pdf_overlay import stamp_scene_onto_pdf
            stamp_scene_onto_pdf(bg_pdf, resolved, target_pdf, pen_type=pen_type, dpi=dpi, seed=seed)
        else:
            merged = fitz.open()
            try:
                for svg_str in svgs:
                    with fitz.open(stream=svg_str.encode("utf-8"), filetype="svg") as sdoc:
                        pdf_bytes = sdoc.convert_to_pdf()
                    with fitz.open("pdf", pdf_bytes) as pdoc:
                        merged.insert_pdf(pdoc)
                merged.save(str(target_pdf), deflate=True)
            finally:
                merged.close()
        written.append(str(target_pdf))

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return {
        "ok": True,
        "scene_dir": str(scene_dir),
        "pages": n_pages,
        "edits_applied": len(edits),
        "format": fmt,
        "pen_type": pen_type,
        "transparent": transparent,
        "outputs": written,
        "elapsed_ms": elapsed_ms,
    }

