"""Printable & tablet-ready fiducial calibration sheet generator (PDF + embedded spec)."""

from __future__ import annotations

import base64
import json
import os
import zlib
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz

from ..pack.schema import HwPack, load_pack
from ..preflight import run_preflight

MM_TO_PT = 72.0 / 25.4
A4_W_MM = 210.0
A4_H_MM = 297.0

# Exact template signature RGB colors (filtered out during zero-OCR vector harvesting)
COLOR_FIDUCIAL = (0.18, 0.62, 0.78)
COLOR_BOX = (0.76, 0.85, 0.94)
COLOR_GHOST = (0.86, 0.89, 0.92)
COLOR_HEADER = (0.38, 0.45, 0.54)
COLOR_LABEL = (0.45, 0.52, 0.60)

# Standard 4 corner fiducial centers on canonical A4 mm canvas
FIDUCIAL_CENTERS_MM: List[Tuple[float, float]] = [
    (9.0, 9.0),
    (201.0, 9.0),
    (9.0, 288.0),
    (201.0, 288.0),
]
FIDUCIAL_HALF_MM = 2.0

_TIER1_STEM_CJK = list(
    "的是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也"
    "能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量数函数"
)

_TIER2_STEM_CJK = list(
    "解设证求题答由得知因所故则即令记取当若假极限导数积分函阵阶秩维线性方程式组场波光磁热功熵态核"
)

_CORE_RADICALS = [
    "亻", "氵", "扌", "讠", "木", "口", "日", "月",
    "艹", "宀", "辶", "纟", "犭", "心", "阝", "土",
    "女", "禾", "钅", "火", "贝", "王", "门", "广",
]

_STROKE_LABELS = {
    "H": "横H",
    "V": "竖V",
    "P": "撇P",
    "N": "捺N",
    "D": "点D",
}


@dataclass
class CellSpec:
    """Single calibration cell on a handwriting collection template sheet."""

    page: int
    cell_id: str
    kind: str  # "glyph" | "stroke" | "radical"
    key: str
    cat: str
    norm_mode: str  # "height100" | "width100"
    box_mm: Tuple[float, float, float, float]  # x, y, w, h in mm
    prompt: str


def infer_char_cat(ch: str) -> str:
    """Infer baseline/metrics category matching handwrite_dom.js & packs schema."""
    if ch in ("__FRAC_SHORT__", "__FRAC_MED__", "__FRAC_LONG__", "—", "―"):
        return "frac_bar"
    if ch in ("__SQRT_HOOK__", "(", ")", "[", "]", "{", "}", "|", "∣", "∥", "‖", "⌈", "⌉", "⌊", "⌋", "⟨", "⟩"):
        return "delim"
    if ch == "__OVERLINE__":
        return "accent_bar"
    if ch in ("^", "ˆ"):
        return "accent_hat"
    if ch in ("ˉ", "˜"):
        return "accent_bar"
    if ch == "°":
        return "deg"
    if ch in ("-", "−"):
        return "op_minus"
    if ch in ("=", "~", "∼"):
        return "op_eq"
    if ch in ("→", "⟶", "←", "⟵", "↔", "⟷", "⇒", "⟹", "⇐", "⟸", "⇔", "⟺", "↦", "↑", "↓", "↗", "↘"):
        return "op_wide"
    if ch in ("∑", "∏", "∫", "∬", "∭", "∮"):
        return "bigop"
    if ch in ("…", "⋯"):
        return "dots_h"
    if ch in (".", ",", ":", ";", "!", "?", "'", '"', "·", "•"):
        return "dot" if ch in (".", "·", "•") else "punct"
    if ch in ("+", "±", "×", "÷", "*", "≠", "≈", "≃", "≅", "<", ">", "≤", "≥", "≪", "≫", "∈", "∉", "⊂", "⊆", "⊃", "⊇", "∩", "∪", "∧", "∨", "≡", "≢", "∝", "⊥", "⊕", "⊗", "⊙", "∗", "∘", "∖"):
        return "op"
    if ch in ("b", "d", "f", "h", "i", "k", "l", "t", "β", "δ", "ζ", "θ", "λ", "∂", "ℏ", "ℓ"):
        return "asc"
    if ch in ("g", "j", "p", "q", "y", "γ", "η", "μ", "ρ", "φ", "ϕ", "χ", "ψ"):
        return "desc"
    if "a" <= ch <= "z" or ch in ("α", "ε", "ϵ", "ι", "κ", "ν", "ξ", "π", "σ", "τ", "υ", "ω", "ϑ", "∞"):
        return "xh"
    if ("0" <= ch <= "9") or ("A" <= ch <= "Z") or ch in ("Δ", "Σ", "Ω", "Γ", "Θ", "Λ", "Ξ", "Π", "Υ", "Φ", "Ψ", "∀", "∃", "∇", "∅", "ℝ", "ℂ", "ℕ", "ℤ", "ℚ", "△", "□", "∠"):
        return "cap"
    if len(ch) == 1 and "\u4e00" <= ch <= "\u9fff":
        return "cjk"
    return "cap"


def _pack_items_to_cells(
    page: int,
    items: List[Tuple[str, str, str, str]],  # (kind, key, prompt, norm_mode)
    prefix: str,
    x_start: float = 12.0,
    y_start: float = 25.0,
    col_pitch: float = 15.6,
    row_pitch: float = 18.0,
    box_size: float = 14.2,
    cols: int = 12,
    max_rows: int = 14,
) -> List[CellSpec]:
    cells: List[CellSpec] = []
    for idx, (kind, key, prompt, norm_mode) in enumerate(items):
        r = idx // cols
        c = idx % cols
        if r >= max_rows:
            break
        x = round(x_start + c * col_pitch, 2)
        y = round(y_start + r * row_pitch + 3.2, 2)
        cells.append(
            CellSpec(
                page=page,
                cell_id=f"p{page}_{prefix}_{idx:03d}",
                kind=kind,
                key=key,
                cat=("stroke" if kind == "stroke" else ("radical" if kind == "radical" else infer_char_cat(key))),
                norm_mode=norm_mode,
                box_mm=(x, y, box_size, box_size),
                prompt=prompt,
            )
        )
    return cells


def build_template_cells(
    tier: str = "tier1",
    chars: Optional[str] = None,
    from_md: Optional[Path] = None,
    pack: Optional[HwPack] = None,
    char_copies: Optional[Dict[str, int]] = None,
    radicals: Optional[List[str]] = None,
) -> Tuple[List[CellSpec], Dict[int, str]]:
    """Build ordered CellSpecs and page titles for tier1 / tier2 / targeted templates."""
    tier_l = (tier or "tier1").lower()
    rad_list: List[str] = []
    if radicals:
        rad_seen = set()
        for r in radicals:
            rs = (r or "").strip()
            if rs and rs not in rad_seen:
                rad_seen.add(rs)
                rad_list.append(rs)

    if from_md is not None or chars is not None or rad_list or tier_l == "targeted":
        char_list: List[str] = []
        seen = set()

        def _push(ch: str):
            if ch and not ch.isspace() and ch not in seen:
                seen.add(ch)
                char_list.append(ch)

        if chars:
            for ch in chars:
                _push(ch)
        if from_md is not None:
            pk = pack or load_pack(None)
            pf = run_preflight(Path(from_md), pk)
            for ch in pf.get("cjk", {}).get("unseen_chars", []):
                _push(ch)
            for tk in pf.get("missing_glyphs", []):
                _push(tk)
        fallback_default = False
        if not char_list and not rad_list:
            fallback_default = True
            for ch in "0123456789xyz=+-":
                _push(ch)

        # Lay out across as many A4 pages as needed (respect per-char copy overrides or default 2 if <= 72 chars)
        default_copies = 2 if len(char_list) <= 72 else 1
        items: List[Tuple[str, str, str, str]] = []
        for ch in char_list:
            nm = "width100" if ch.startswith("__FRAC") else "height100"
            n_rep = max(1, min(4, int((char_copies or {}).get(ch, default_copies))))
            for _ in range(n_rep):
                items.append(("glyph", ch, ch, nm))
        rad_copies = 2 if len(rad_list) <= 30 else 1
        for rad in rad_list:
            for _ in range(rad_copies):
                items.append(("radical", rad, f"部{rad}", "height100"))

        per_page = 12 * 14
        all_cells: List[CellSpec] = []
        titles: Dict[int, str] = {}
        num_pages = max(1, (len(items) + per_page - 1) // per_page)
        for p in range(num_pages):
            chunk = items[p * per_page : (p + 1) * per_page]
            all_cells.extend(_pack_items_to_cells(p, chunk, prefix="tgt"))
            titles[p] = f"InkTwin Targeted Supplement Sheet ({p + 1}/{num_pages}) - Write with Dark Ink"
        if fallback_default and 0 in titles:
            titles[0] = "InkTwin Targeted Sheet (Fallback Default Set) - Write with Dark Ink"
        return all_cells, titles

    if tier_l in ("full", "all", "standard"):
        c1, _ = build_template_cells(tier="tier1")
        c2, _ = build_template_cells(tier="tier2")
        for c in c2:
            c.page = 3
            suffix = c.cell_id.split("_", 1)[1] if "_" in c.cell_id else c.cell_id
            c.cell_id = f"p3_{suffix}"
        return c1 + c2, {
            0: "InkTwin Standard Sheet (1/4): Digits, Lowercase & Uppercase Letters",
            1: "InkTwin Standard Sheet (2/4): Math Operators, Delimiters, Bars & Core Greek",
            2: "InkTwin Standard Sheet (3/4): Stroke DNA, 24 Radicals & Core STEM Hanzi",
            3: "InkTwin Standard Sheet (4/4): Extended Greek, Physics Symbols & STEM Hanzi",
        }

    if tier_l == "tier2":
        items = []
        greek_upper = "ΓΘΛΞΠΣΥΦΨΩ"
        greek_lower = "αβγδεζηθικλμνξπρστυφχψωϕϑ"
        phys = "ℏℓ∂∇∆√∝≡≢⊥∥∠△□∘•†∗⊕⊗⊙⟨⟩⌈⌉⌊⌋↑↓↗↘≈≃≅≪≫ℝℂℕℤℚ∅∖⊂⊆⊃⊇∉"
        for ch in greek_upper + greek_lower + phys:
            items.append(("glyph", ch, ch, "height100"))
        for ch in _TIER2_STEM_CJK:
            items.append(("glyph", ch, ch, "height100"))
        cells = _pack_items_to_cells(0, items, prefix="t2")
        return cells, {0: "InkTwin Tier-2 Extension Sheet (Greek, Physics Symbols & STEM Hanzi)"}

    # Default: tier1 (3 comprehensive pages with built-in multi-variant coverage)
    # Page 0: Digits (x3), Lowercase (x2-x3), Uppercase (x1-x2) -> 148 cells
    p0_items: List[Tuple[str, str, str, str]] = []
    for d in "0123456789":
        for _ in range(3):
            p0_items.append(("glyph", d, d, "height100"))
    high_lower = set("xyztnkijaeosrmfgduvp")
    for ch in "abcdefghijklmnopqrstuvwxyz":
        reps = 3 if ch in high_lower else 2
        for _ in range(reps):
            p0_items.append(("glyph", ch, ch, "height100"))
    high_upper = set("ABCDEFIMLNPRSTUVWXYZ")
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        reps = 2 if ch in high_upper else 1
        for _ in range(reps):
            p0_items.append(("glyph", ch, ch, "height100"))
    c0 = _pack_items_to_cells(0, p0_items, prefix="t1p0")

    # Page 1: Math Operators & Delimiters (x2-x3), Arrows & Structural Bars (x2), Core Greek & Calculus (x1-x2) -> 136 cells
    p1_items: List[Tuple[str, str, str, str]] = []
    for ch in "=+-(),.":
        for _ in range(3):
            p1_items.append(("glyph", ch, ch, "height100"))
    for ch in "*/≠≈<>≤≥[]{}|±×÷·:;!?":
        for _ in range(2):
            p1_items.append(("glyph", ch, ch, "height100"))
    for ch in "%'\"":
        p1_items.append(("glyph", ch, ch, "height100"))
    struct = [
        ("→", "→"),
        ("←", "←"),
        ("↔", "↔"),
        ("⇒", "⇒"),
        ("⇔", "⇔"),
        ("__FRAC_SHORT__", "短分线"),
        ("__FRAC_MED__", "中分线"),
        ("__FRAC_LONG__", "长分线"),
        ("__SQRT_HOOK__", "根号√"),
        ("__OVERLINE__", "上横ˉ"),
    ]
    for k, lbl in struct:
        nm = "width100" if k.startswith("__FRAC") else "height100"
        for _ in range(2):
            p1_items.append(("glyph", k, lbl, nm))
    for ch in "αβγδεθλμπρσφωΔΣΩ∫∑∂∇∞∈":
        for _ in range(2):
            p1_items.append(("glyph", ch, ch, "height100"))
    for ch in "∀∃∧∨∩∪":
        p1_items.append(("glyph", ch, ch, "height100"))
    c1 = _pack_items_to_cells(1, p1_items, prefix="t1p1")

    # Page 2: Stroke DNA (26), 24 Radicals, 80 Core STEM CJK (+ 16 high-freq extra variants) -> 146 cells
    p2_items: List[Tuple[str, str, str, str]] = []
    for st, cnt in (("H", 5), ("V", 5), ("P", 5), ("N", 5), ("D", 6)):
        for _ in range(cnt):
            p2_items.append(("stroke", st, _STROKE_LABELS[st], "height100"))
    for rad in _CORE_RADICALS:
        p2_items.append(("radical", rad, f"部{rad}", "height100"))
    for ch in _TIER1_STEM_CJK:
        p2_items.append(("glyph", ch, ch, "height100"))
    for ch in "的是在不了有和为中大对成分等数函":
        p2_items.append(("glyph", ch, ch, "height100"))
    c2 = _pack_items_to_cells(2, p2_items, prefix="t1p2")
    return c0 + c1 + c2, {
        0: "InkTwin Tier-1 Sheet (1/3): Multi-Variant Digits, Lowercase & Uppercase Letters",
        1: "InkTwin Tier-1 Sheet (2/3): Multi-Variant Math Operators, Delimiters, Bars & Greek",
        2: "InkTwin Tier-1 Sheet (3/3): Stroke DNA, 24 Core Radicals & Core STEM Hanzi",
    }


def _draw_fiducials(page: fitz.Page) -> None:
    for cx_mm, cy_mm in FIDUCIAL_CENTERS_MM:
        rect = fitz.Rect(
            (cx_mm - FIDUCIAL_HALF_MM) * MM_TO_PT,
            (cy_mm - FIDUCIAL_HALF_MM) * MM_TO_PT,
            (cx_mm + FIDUCIAL_HALF_MM) * MM_TO_PT,
            (cy_mm + FIDUCIAL_HALF_MM) * MM_TO_PT,
        )
        page.draw_rect(rect, color=COLOR_FIDUCIAL, fill=COLOR_FIDUCIAL, width=0.4)


_FALLBACK_ASCII_LABELS: Dict[str, str] = {
    "ϕ": "phi*",
    "ℂ": "C*",
    "ℏ": "hbar",
    "ℕ": "N*",
    "ℚ": "Q*",
    "ℝ": "R*",
    "ℤ": "Z*",
    "∆": "dlt",
    "∖": "\\",
    "∘": "o",
    "≃": "~=",
    "≢": "!==",
    "⌈": "lc",
    "⌉": "rc",
    "⌊": "lf",
    "⌋": "rf",
    "⟨": "<|",
    "⟩": "|>",
}


@lru_cache(maxsize=1)
def _get_label_fonts() -> Tuple[fitz.Font, fitz.Font, List[fitz.Font]]:
    """Return cached (ascii_font, cjk_font, extra_math_symbol_fonts) for clean template labels."""
    f_ascii = fitz.Font("helv")
    f_cjk = fitz.Font("china-ss")
    extras: List[fitz.Font] = []
    cands: List[Path] = []
    win_root = os.environ.get("WINDIR")
    if win_root:
        wf = Path(win_root) / "Fonts"
        cands.extend([wf / "seguisym.ttf", wf / "cambria.ttc", wf / "msyh.ttc"])
    cands.extend(
        [
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/System/Library/Fonts/STIXTwoMath.otf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/opentype/stix/STIXTwoMath-Regular.otf"),
        ]
    )
    for fp in cands:
        try:
            if fp.is_file():
                extras.append(fitz.Font(fontfile=str(fp)))
        except Exception:
            pass
    return f_ascii, f_cjk, extras


def _resolve_char_font(ch: str) -> Tuple[str, fitz.Font]:
    f_ascii, f_cjk, extras = _get_label_fonts()
    cp = ord(ch)
    if ch.isascii() and f_ascii.has_glyph(cp):
        return ch, f_ascii
    if f_cjk.has_glyph(cp):
        return ch, f_cjk
    for fx in extras:
        if fx.has_glyph(cp):
            return ch, fx
    if f_ascii.has_glyph(cp):
        return ch, f_ascii
    alt = _FALLBACK_ASCII_LABELS.get(ch, ch)
    return alt, f_ascii


def _draw_prompt_label(page: fitz.Page, cell: CellSpec) -> None:
    x, y, w, h = cell.box_mm
    box_rect = fitz.Rect(x * MM_TO_PT, y * MM_TO_PT, (x + w) * MM_TO_PT, (y + h) * MM_TO_PT)
    page.draw_rect(box_rect, color=COLOR_BOX, fill=None, width=0.55)

    # Subtle baseline & center guide cross inside writing cell (clean interior, no ghost character)
    mid_x = (x + w * 0.5) * MM_TO_PT
    base_y = (y + h * 0.78) * MM_TO_PT
    page.draw_line(
        fitz.Point(x * MM_TO_PT + 2, base_y),
        fitz.Point((x + w) * MM_TO_PT - 2, base_y),
        color=(0.90, 0.93, 0.96),
        width=0.35,
        dashes="[2 2]",
    )
    page.draw_line(
        fitz.Point(mid_x, y * MM_TO_PT + 2),
        fitz.Point(mid_x, (y + h) * MM_TO_PT - 2),
        color=(0.92, 0.94, 0.97),
        width=0.3,
        dashes="[2 2]",
    )

    # Centered prompt label directly above the cell top edge (with full Unicode Math/Symbol fallback)
    lbl = cell.prompt if len(cell.prompt) <= 4 else cell.prompt[:4]
    fs = 7.4
    runs: List[Tuple[str, fitz.Font, float]] = []
    total_w = 0.0
    for raw_ch in lbl:
        disp, fnt = _resolve_char_font(raw_ch)
        if not disp:
            continue
        cw = float(fnt.text_length(disp, fontsize=fs))
        runs.append((disp, fnt, cw))
        total_w += cw

    if runs:
        cur_x = (x + w * 0.5) * MM_TO_PT - total_w * 0.5
        lbl_y = (y - 0.95) * MM_TO_PT
        tw = fitz.TextWriter(page.rect)
        for disp, fnt, cw in runs:
            tw.append(fitz.Point(cur_x, lbl_y), disp, font=fnt, fontsize=fs)
            cur_x += cw
        tw.write_text(page, color=COLOR_LABEL)


def generate_template_pdf(
    out_pdf: Path,
    tier: str = "tier1",
    chars: Optional[str] = None,
    from_md: Optional[Path] = None,
    pack: Optional[HwPack] = None,
    char_copies: Optional[Dict[str, int]] = None,
    radicals: Optional[List[str]] = None,
) -> dict:
    """Render an A4 vector handwriting collection sheet PDF with embedded fiducial & cell spec."""
    cells, titles = build_template_cells(
        tier=tier, chars=chars, from_md=from_md, pack=pack, char_copies=char_copies, radicals=radicals
    )
    num_pages = max((c.page for c in cells), default=0) + 1

    doc = fitz.open()
    for pno in range(num_pages):
        page = doc.new_page(width=A4_W_MM * MM_TO_PT, height=A4_H_MM * MM_TO_PT)
        _draw_fiducials(page)
        title = titles.get(pno, f"InkTwin Collection Sheet ({pno + 1}/{num_pages})")
        hdr_rect = fitz.Rect(14.0 * MM_TO_PT, 6.5 * MM_TO_PT, 196.0 * MM_TO_PT, 14.5 * MM_TO_PT)
        page.insert_textbox(hdr_rect, title, fontsize=10.5, fontname="helv", color=COLOR_HEADER, align=fitz.TEXT_ALIGN_LEFT)
        sub_rect = fitz.Rect(14.0 * MM_TO_PT, 12.2 * MM_TO_PT, 196.0 * MM_TO_PT, 19.0 * MM_TO_PT)
        page.insert_textbox(
            sub_rect,
            "Write naturally inside each blue box with dark ink (black/dark-blue). Keep the 4 corner cyan squares untouched.",
            fontsize=7.8,
            fontname="helv",
            color=COLOR_LABEL,
            align=fitz.TEXT_ALIGN_LEFT,
        )
        for c in cells:
            if c.page == pno:
                _draw_prompt_label(page, c)

    spec_obj = {
        "schema": "inktwin-template-v1",
        "tier": tier,
        "page_size_mm": [A4_W_MM, A4_H_MM],
        "fiducial_centers_mm": FIDUCIAL_CENTERS_MM,
        "fiducial_color_rgb": list(COLOR_FIDUCIAL),
        "cells": [asdict(c) for c in cells],
    }
    spec_bytes = json.dumps(spec_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp_b64 = base64.b64encode(zlib.compress(spec_bytes, level=6)).decode("ascii")

    doc.embfile_add("inktwin_template_spec.json", spec_bytes, desc="InkTwin template cell & fiducial specification")
    doc.set_metadata(
        {
            "title": "InkTwin Handwriting Collection Sheet",
            "subject": "INKTWIN_TEMPLATE_V1",
            "creator": "InkTwin",
            "keywords": f"INKTWIN_TPL:{comp_b64}",
        }
    )

    try:
        doc.subset_fonts()
    except Exception:
        pass
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_pdf), deflate=True, garbage=3)
    doc.close()

    return {
        "pdf": str(out_pdf.resolve()),
        "tier": tier,
        "pages": num_pages,
        "cells": len(cells),
        "fallback_default": any("Fallback Default" in t for t in titles.values()),
    }


def read_template_spec_from_pdf(pdf_path: Path) -> dict:
    """Recover the canonical template specification from PDF embedded file or metadata keywords."""
    with fitz.open(str(pdf_path)) as doc:
        try:
            for i in range(doc.embfile_count()):
                info = doc.embfile_info(i)
                if "inktwin_template_spec" in (info.get("name") or ""):
                    raw = doc.embfile_get(i)
                    return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
        kw = (doc.metadata or {}).get("keywords") or ""
        if "INKTWIN_TPL:" in kw:
            token = kw.split("INKTWIN_TPL:", 1)[1].strip().split()[0]
            raw = zlib.decompress(base64.b64decode(token.encode("ascii")))
            return json.loads(raw.decode("utf-8"))
    raise ValueError(f"No InkTwin template metadata found inside {pdf_path}. Pass --tier/--spec explicitly.")

