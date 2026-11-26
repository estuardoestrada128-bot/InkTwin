"""Page-anchored millimeter-coordinate physical pen texture layer."""

from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import fitz
import numpy as np
from PIL import Image, ImageFilter

from ..scene.renderer import _paper_svg_background, render_page_svg

PEN_TYPES = ("gel", "ballpoint", "fountain", "pencil")


def _crop_mm_noise(
    full_h: int,
    full_w: int,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    dpi: float,
    wavelength_mm: float,
    seed: int,
) -> np.ndarray:
    """Sample deterministic [0..1] Perlin-like noise anchored to physical page mm, cropped to [y0:y1, x0:x1]."""
    mm_per_px = 25.4 / max(36.0, float(dpi))
    w_mm = max(1.0, full_w * mm_per_px)
    h_mm = max(1.0, full_h * mm_per_px)
    gw = max(2, int(round(w_mm / max(0.03, wavelength_mm))))
    gh = max(2, int(round(h_mm / max(0.03, wavelength_mm))))
    rng = np.random.RandomState(seed)
    grid = rng.randint(0, 256, size=(gh, gw), dtype=np.uint8)
    cw, ch = max(1, x1 - x0), max(1, y1 - y0)
    if x0 == 0 and y0 == 0 and x1 == full_w and y1 == full_h:
        im = Image.fromarray(grid, mode="L").resize((cw, ch), Image.Resampling.BICUBIC)
        return np.asarray(im, dtype=np.float32) / 255.0
    gy0 = max(0, int(math.floor(y0 * gh / full_h)) - 2)
    gy1 = min(gh, int(math.ceil(y1 * gh / full_h)) + 2)
    gx0 = max(0, int(math.floor(x0 * gw / full_w)) - 2)
    gx1 = min(gw, int(math.ceil(x1 * gw / full_w)) + 2)
    sub = grid[gy0:gy1, gx0:gx1]
    box = (
        max(0.0, (x0 * gw / full_w) - gx0),
        max(0.0, (y0 * gh / full_h) - gy0),
        max(1.0, (x1 * gw / full_w) - gx0),
        max(1.0, (y1 * gh / full_h) - gy0),
    )
    im = Image.fromarray(sub, mode="L").resize((cw, ch), Image.Resampling.BICUBIC, box=box)
    return np.asarray(im, dtype=np.float32) / 255.0


def _sheared_diagonal_noise(
    full_h: int,
    full_w: int,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    dpi: float,
    pitch_mm: float,
    angle_deg: float,
    seed: int,
) -> np.ndarray:
    """Fast vectorized directional streak noise anchored to millimeter pitch along angle_deg."""
    base = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, pitch_mm, seed)
    ch, cw = base.shape
    slope = float(np.tan(np.deg2rad(angle_deg)))
    ys = np.arange(y0, y1, dtype=np.float32)
    sy = np.rint((ys - y0) * 0.65).astype(np.int32) % max(1, ch)
    x_off = np.rint(0.35 * slope * ys).astype(np.int32)[:, None]
    x_idx = np.arange(cw, dtype=np.int32)[None, :]
    sx = (x_idx + x_off) % max(1, cw)
    return 0.55 * base + 0.45 * base[sy[:, None], sx]


def _spread_ink_rgb(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Propagate local ink color outward onto nearby semi-transparent pixels so dilation never pulls black(0,0,0)."""
    pm = rgb * alpha[..., None]
    w_im = Image.fromarray(np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8), mode="L").filter(
        ImageFilter.GaussianBlur(radius=1.6)
    )
    w_arr = np.asarray(w_im, dtype=np.float32) / 255.0
    chans = []
    for c in range(3):
        cim = Image.fromarray(np.clip(pm[:, :, c], 0.0, 255.0).astype(np.uint8), mode="L").filter(
            ImageFilter.GaussianBlur(radius=1.6)
        )
        chans.append(np.asarray(cim, dtype=np.float32))
    blurred_pm = np.stack(chans, axis=-1)
    est_rgb = blurred_pm / np.maximum(w_arr[..., None], 1e-4)
    return np.where((alpha < 0.35)[..., None] & (w_arr > 0.02)[..., None], np.clip(est_rgb, 0.0, 255.0), rgb)


def _apply_nib_width(alpha: np.ndarray, rgb: np.ndarray, nib_mm: float, dpi: float) -> Tuple[np.ndarray, np.ndarray]:
    """Adjust stroke thickness around baseline 0.50 mm tip using crisp morphological edge shaping."""
    nib = float(nib_mm or 0.5)
    if abs(nib - 0.5) <= 0.02:
        return alpha, rgb
    a_u8 = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
    scale_r = max(1, int(round((dpi / 300.0))))
    ksize = 3 if scale_r <= 1 else 5
    if nib < 0.5:
        eroded = np.asarray(Image.fromarray(a_u8, mode="L").filter(ImageFilter.MinFilter(ksize)), dtype=np.float32) / 255.0
        strength = min(0.82, max(0.0, (0.5 - nib) / 0.14) * 0.72)
        # Keep minimum faint spine so 1px hairline accents never vanish completely
        thinned = np.maximum(eroded, alpha * 0.28)
        alpha = (1.0 - strength) * alpha + strength * thinned
        return np.clip(alpha, 0.0, 1.0), rgb
    spread_rgb = _spread_ink_rgb(rgb, alpha)
    dilated = np.asarray(Image.fromarray(a_u8, mode="L").filter(ImageFilter.MaxFilter(ksize)), dtype=np.float32) / 255.0
    strength = min(0.85, max(0.0, (nib - 0.5) / 0.22) * 0.78)
    alpha = np.maximum(alpha, dilated * strength)
    return np.clip(alpha, 0.0, 1.0), spread_rgb


def apply_pen_physics_to_rgba(
    rgba: np.ndarray,
    pen_type: str = "gel",
    dpi: float = 300,
    seed: int = 42,
    nib_mm: float = 0.5,
) -> np.ndarray:
    """Apply physical pen nib geometry & ink/paper interaction onto a tight ink bbox within an RGBA bitmap."""
    out = rgba.copy()
    pt = (pen_type or "gel").lower()
    if pt not in PEN_TYPES:
        raise ValueError(f"Unknown pen_type '{pen_type}'; choose from {PEN_TYPES}")

    full_h, full_w = out.shape[:2]
    alpha_u8 = out[:, :, 3]
    nz_y, nz_x = np.where(alpha_u8 > 2)
    if len(nz_y) == 0:
        return out

    pad = max(12, int(round(float(dpi) / 25.4 * 1.5)))
    y0 = max(0, int(nz_y.min()) - pad)
    y1 = min(full_h, int(nz_y.max()) + pad + 1)
    x0 = max(0, int(nz_x.min()) - pad)
    x1 = min(full_w, int(nz_x.max()) + pad + 1)

    alpha = alpha_u8[y0:y1, x0:x1].astype(np.float32) / 255.0
    rgb = out[y0:y1, x0:x1, :3].astype(np.float32)
    alpha, rgb = _apply_nib_width(alpha, rgb, nib_mm=nib_mm, dpi=dpi)

    if pt == "gel":
        n_gel = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 2.5, seed + 5)
        alpha = alpha * (0.96 + 0.04 * n_gel)
    elif pt == "ballpoint":
        rgb = _spread_ink_rgb(rgb, alpha)
        a_u8_n = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
        eroded = np.asarray(Image.fromarray(a_u8_n, mode="L").filter(ImageFilter.MinFilter(3)), dtype=np.float32) / 255.0
        alpha = 0.72 * alpha + 0.28 * np.maximum(eroded, alpha * 0.45)

        n_wave = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 1.35, seed + 11)
        n_streak = _sheared_diagonal_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.24, 35.0, seed + 23)
        n_fine = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.16, seed + 31)
        edge_mask = (alpha > 0.04) & (alpha < 0.88)
        edge_skip = np.where(edge_mask & (n_fine < 0.44), 0.18 + 0.72 * n_fine, 1.0)
        mid_groove = np.where((alpha > 0.45) & (n_streak < 0.26), 0.56 + 1.2 * n_streak, 1.0)
        alpha = alpha * (0.72 + 0.28 * n_wave) * edge_skip * mid_groove

        gy = np.abs(alpha - np.roll(alpha, 1, axis=0))
        gx = np.abs(alpha - np.roll(alpha, 1, axis=1))
        corner_pool = np.clip((gx + gy) * 0.45, 0.0, 0.28)
        sheen = np.where(alpha > 0.55, 10.0 * n_wave, 0.0)[..., None]
        rgb = np.clip(rgb * (0.86 + 0.14 * n_wave - 0.18 * corner_pool)[..., None] + sheen, 0.0, 255.0)
    elif pt == "fountain":
        rgb = _spread_ink_rgb(rgb, alpha)
        down_full = np.maximum(alpha, 0.52 * np.roll(alpha, 1, axis=0))
        down_full = np.maximum(down_full, 0.34 * np.roll(alpha, 1, axis=1))
        alpha_nib = 0.68 * alpha + 0.32 * down_full

        pool_r = max(1.1, 0.28 * float(dpi) / 25.4)
        a_u8_n = np.clip(alpha_nib * 255.0, 0.0, 255.0).astype(np.uint8)
        pool_map = np.asarray(Image.fromarray(a_u8_n, mode="L").filter(ImageFilter.GaussianBlur(radius=pool_r)), dtype=np.float32) / 255.0
        n_pool = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.95, seed + 53)
        n_fib = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.22, seed + 37)

        rim_r = max(0.55, 0.036 * float(dpi) / 25.4)
        rim_blur = np.asarray(Image.fromarray(a_u8_n, mode="L").filter(ImageFilter.GaussianBlur(radius=rim_r)), dtype=np.float32) / 255.0
        capillary_halo = np.clip(rim_blur * (0.28 + 0.55 * n_fib), 0.0, 0.65)
        alpha = np.maximum(alpha_nib, capillary_halo)
        meniscus = np.clip((pool_map - alpha_nib) * 0.45, 0.0, 0.22)
        alpha = np.clip(alpha * (0.94 + 0.08 * n_pool) + meniscus * 0.12, 0.0, 1.0)

        pool_dark = np.clip(pool_map * (0.85 + 0.30 * n_pool), 0.0, 1.0)
        shade_mul = (1.14 - 0.44 * pool_dark)[..., None]
        rgb = np.clip(rgb * shade_mul, 0.0, 255.0)
    elif pt == "pencil":
        rgb = _spread_ink_rgb(rgb, alpha)
        a_u8_n = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
        soft_edge = np.asarray(Image.fromarray(a_u8_n, mode="L").filter(ImageFilter.MaxFilter(3)), dtype=np.float32) / 255.0
        alpha = np.maximum(alpha, soft_edge * 0.18)

        n_tooth = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.085, seed + 71)
        n_hatch = _sheared_diagonal_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.18, 45.0, seed + 83)
        n_grain = _crop_mm_noise(full_h, full_w, y0, y1, x0, x1, dpi, 0.26, seed + 89)
        tooth = 0.46 * n_tooth + 0.34 * n_hatch + 0.20 * n_grain
        alpha = alpha * np.clip((tooth - 0.14) * 1.48, 0.05, 1.0) * 0.88

        graphite = np.array([56.0, 60.0, 68.0], dtype=np.float32)
        is_neutral = (np.max(rgb, axis=-1) - np.min(rgb, axis=-1)) < 18.0
        rgb = np.where(
            is_neutral[..., None],
            rgb * 0.35 + graphite * 0.65 + (1.0 - tooth)[..., None] * 18.0,
            rgb * 0.58 + graphite * 0.42 + (1.0 - tooth)[..., None] * 14.0,
        )

    out[y0:y1, x0:x1, :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    out[y0:y1, x0:x1, 3] = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
    return out


def _process_page_rgba(args: Tuple[np.ndarray, str, int, int, float]) -> bytes:
    arr, pen_type, dpi, p_seed, nib_mm = args
    styled = apply_pen_physics_to_rgba(arr, pen_type=pen_type, dpi=dpi, seed=p_seed, nib_mm=nib_mm)
    buf = io.BytesIO()
    Image.fromarray(styled, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


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
    nib_mm: float = 0.5,
    ink_override: Optional[str] = None,
) -> List[str]:
    """Render InkScene pages with optional physical pen textures to transparent PNG or composited PDF."""
    n_pages = max(1, int(resolved_scene.get("pages_detected", 1)))
    paper = resolved_scene.get("paper") or {}
    w_mm = float(paper.get("width_mm") or 210.0)
    h_mm = float(paper.get("height_mm") or 297.0)
    written: List[str] = []

    raw_pages: List[Tuple[np.ndarray, str, int, int, float]] = []
    for pi in range(n_pages):
        svg_str = render_page_svg(pi, resolved_scene, transparent=True, ink_override=ink_override)
        with fitz.open(stream=svg_str.encode("utf-8"), filetype="svg") as sdoc:
            pdf_bytes = sdoc.convert_to_pdf()
        with fitz.open("pdf", pdf_bytes) as pdoc:
            pix = pdoc[0].get_pixmap(dpi=dpi, alpha=True)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).copy()
            raw_pages.append((arr, pen_type, dpi, seed + pi * 101, nib_mm))

    if len(raw_pages) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(raw_pages))) as pool:
            png_bytes_per_page = list(pool.map(_process_page_rgba, raw_pages))
    else:
        png_bytes_per_page = [_process_page_rgba(item) for item in raw_pages]

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

