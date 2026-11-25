"""Page-anchored millimeter-coordinate physical pen texture layer."""

from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional

import fitz
import numpy as np
from PIL import Image, ImageFilter

from ..scene.renderer import _paper_svg_background, render_page_svg

PEN_TYPES = ("gel", "ballpoint", "fountain", "pencil")


def _page_mm_noise(h_px: int, w_px: int, dpi: float, wavelength_mm: float, seed: int) -> np.ndarray:
    """Sample deterministic [0..1] Perlin-like grid noise anchored to physical page millimeters."""
    mm_per_px = 25.4 / max(36.0, float(dpi))
    w_mm = max(1.0, w_px * mm_per_px)
    h_mm = max(1.0, h_px * mm_per_px)
    gw = max(2, int(round(w_mm / max(0.03, wavelength_mm))))
    gh = max(2, int(round(h_mm / max(0.03, wavelength_mm))))
    rng = np.random.RandomState(seed)
    grid = rng.uniform(0.0, 255.0, (gh, gw)).astype(np.uint8)
    im = Image.fromarray(grid, mode="L").resize((w_px, h_px), Image.Resampling.BICUBIC)
    return np.asarray(im, dtype=np.float32) / 255.0


def apply_pen_physics_to_rgba(
    rgba: np.ndarray,
    pen_type: str = "gel",
    dpi: float = 300,
    seed: int = 42,
) -> np.ndarray:
    """Apply physical pen ink/paper interaction onto a transparent RGBA stroke bitmap."""
    out = rgba.copy()
    pt = (pen_type or "gel").lower()
    if pt not in PEN_TYPES:
        raise ValueError(f"Unknown pen_type '{pen_type}'; choose from {PEN_TYPES}")

    h_px, w_px = out.shape[:2]
    alpha_u8 = out[:, :, 3]
    if not np.any(alpha_u8 > 2):
        return out

    alpha = alpha_u8.astype(np.float32) / 255.0
    rgb = out[:, :, :3].astype(np.float32)

    if pt == "gel":
        n_gel = _page_mm_noise(h_px, w_px, dpi, 2.5, seed + 5)
        alpha = alpha * (0.95 + 0.05 * n_gel)
    elif pt == "ballpoint":
        # 1.5 mm roller oil-ink concentration waves + 0.20 mm edge roller-skid micro-skipping
        n_wave = _page_mm_noise(h_px, w_px, dpi, 1.5, seed + 11)
        n_fine = _page_mm_noise(h_px, w_px, dpi, 0.20, seed + 23)
        edge_mask = (alpha > 0.0) & (alpha < 0.85)
        edge_skip = np.where(edge_mask & (n_fine < 0.42), 0.22 + 0.68 * n_fine, 1.0)
        alpha = alpha * (0.68 + 0.34 * n_wave) * edge_skip
        rgb = np.clip(rgb * (0.86 + 0.16 * n_wave[..., None]), 0.0, 255.0)
    elif pt == "fountain":
        # 0.055 mm capillary fiber micro-bleed + shading/pooling gradient
        blur_r = max(0.65, 0.055 * float(dpi) / 25.4)
        a_im = Image.fromarray(alpha_u8, mode="L").filter(ImageFilter.GaussianBlur(radius=blur_r))
        alpha_blur = np.asarray(a_im, dtype=np.float32) / 255.0
        n_fib = _page_mm_noise(h_px, w_px, dpi, 0.26, seed + 37)
        n_pool = _page_mm_noise(h_px, w_px, dpi, 1.0, seed + 53)
        bleed = alpha_blur * (0.48 + 0.90 * n_fib)
        alpha = np.maximum(alpha * 0.94, np.clip(bleed * 1.25, 0.0, 1.0))
        # Ink pooling contrast & fill RGB inside bleed halo from nearest solid ink color
        rgb_im = Image.fromarray(out[:, :, :3], mode="RGB").filter(ImageFilter.GaussianBlur(radius=blur_r * 1.05))
        rgb_blur = np.asarray(rgb_im, dtype=np.float32)
        rgb = np.where((alpha_u8 < 45)[..., None], rgb_blur, rgb * (0.78 + 0.26 * n_pool[..., None]))
    elif pt == "pencil":
        # High-frequency 0.09 mm + 0.24 mm porous paper-tooth graphite texture
        n_tooth = _page_mm_noise(h_px, w_px, dpi, 0.09, seed + 71)
        n_grain = _page_mm_noise(h_px, w_px, dpi, 0.24, seed + 89)
        tooth = 0.62 * n_tooth + 0.38 * n_grain
        alpha = alpha * np.clip((tooth - 0.14) * 1.42, 0.06, 1.0) * 0.88
        graphite = np.array([58.0, 62.0, 70.0], dtype=np.float32)
        rgb = rgb * 0.45 + graphite * 0.55

    out[:, :, :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    out[:, :, 3] = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
    return out


def render_scene_with_pen(
    resolved_scene: dict,
    out_pdf: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    fmt: str = "pdf",
    pen_type: str = "gel",
    dpi: int = 300,
    transparent: bool = False,
    seed: int = 42,
    base_pdf: Optional[Path] = None,
) -> List[str]:
    """Render InkScene pages with optional physical pen textures to transparent PNG or composited PDF."""
    n_pages = max(1, int(resolved_scene.get("pages_detected", 1)))
    paper = resolved_scene.get("paper") or {}
    w_mm = float(paper.get("width_mm") or 210.0)
    h_mm = float(paper.get("height_mm") or 297.0)
    written: List[str] = []

    png_bytes_per_page: List[bytes] = []
    for pi in range(n_pages):
        svg_str = render_page_svg(pi, resolved_scene, transparent=True)
        with fitz.open(stream=svg_str.encode("utf-8"), filetype="svg") as sdoc:
            pdf_bytes = sdoc.convert_to_pdf()
        with fitz.open("pdf", pdf_bytes) as pdoc:
            pix = pdoc[0].get_pixmap(dpi=dpi, alpha=True)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)
            styled = apply_pen_physics_to_rgba(arr, pen_type=pen_type, dpi=dpi, seed=seed + pi * 101)
            buf = io.BytesIO()
            Image.fromarray(styled, mode="RGBA").save(buf, format="PNG")
            png_bytes_per_page.append(buf.getvalue())

    if fmt == "png":
        target_dir = (out_dir or (out_pdf.parent if out_pdf else Path.cwd())).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        for pi, pb in enumerate(png_bytes_per_page):
            fp = target_dir / f"page-{pi + 1}.png"
            fp.write_bytes(pb)
            written.append(str(fp))

    if out_pdf is not None or fmt == "pdf":
        target_pdf = Path(out_pdf or "inktwin_pen_out.pdf").resolve()
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        if base_pdf is not None and Path(base_pdf).exists():
            with fitz.open(str(base_pdf)) as doc:
                for pi in range(min(len(doc), len(png_bytes_per_page))):
                    doc[pi].insert_image(doc[pi].rect, stream=png_bytes_per_page[pi], overlay=True)
                doc.save(str(target_pdf), deflate=True)
        else:
            out_doc = fitz.open()
            try:
                bg_elems = _paper_svg_background(paper, w_mm, h_mm)
                bg_svg = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_mm:.2f}mm" height="{h_mm:.2f}mm" viewBox="0 0 {w_mm:.2f} {h_mm:.2f}">'
                    + ("" if transparent else "".join(bg_elems))
                    + "</svg>"
                )
                with fitz.open(stream=bg_svg.encode("utf-8"), filetype="svg") as sdoc:
                    bg_pdf_bytes = sdoc.convert_to_pdf()
                for pb in png_bytes_per_page:
                    with fitz.open("pdf", bg_pdf_bytes) as pdoc:
                        out_doc.insert_pdf(pdoc)
                    page = out_doc[-1]
                    page.insert_image(page.rect, stream=pb, overlay=True)
                out_doc.save(str(target_pdf), deflate=True)
            finally:
                out_doc.close()
        written.append(str(target_pdf))

    return written

