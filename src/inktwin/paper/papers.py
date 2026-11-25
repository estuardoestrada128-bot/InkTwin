"""Physical millimeter (mm) paper presets and CSS generators."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

PAGE_SIZES_MM: Dict[str, Tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "B5": (176.0, 250.0),
    "16K": (185.0, 260.0),
    "LETTER": (215.9, 279.4),
}


@dataclass(frozen=True)
class PaperSpec:
    """Physical page geometry specification in millimeters (mm)."""

    id: str
    name: str
    width_mm: float = 210.0
    height_mm: float = 297.0
    margins_mm: Tuple[float, float, float, float] = (13.0, 14.0, 13.0, 14.0)  # top, right, bottom, left
    pitch_mm: Optional[float] = None
    margin_line_x_mm: Optional[float] = None
    bg_color: str = "#ffffff"
    line_color: str = "rgba(175, 182, 194, 0.36)"
    has_header: bool = False
    grid_lock: bool = False
    kind: str = "blank"  # blank | grid | ruled | dots | cream | compo


PAPERS: Dict[str, PaperSpec] = {
    "white": PaperSpec(id="white", name="Blank White", margins_mm=(13.0, 14.0, 13.0, 14.0), kind="blank"),
    "blank": PaperSpec(id="blank", name="Blank White", margins_mm=(13.0, 14.0, 13.0, 14.0), kind="blank"),
    "grid": PaperSpec(
        id="grid",
        name="Squared Grid (6.2mm)",
        margins_mm=(10.0, 11.0, 11.0, 11.0),
        pitch_mm=6.2,
        line_color="rgba(175, 182, 194, 0.36)",
        kind="grid",
    ),
    "grid6": PaperSpec(
        id="grid6",
        name="Squared Grid (6.2mm)",
        margins_mm=(10.0, 11.0, 11.0, 11.0),
        pitch_mm=6.2,
        line_color="rgba(175, 182, 194, 0.36)",
        kind="grid",
    ),
    "grid5": PaperSpec(
        id="grid5",
        name="Squared Grid (5.0mm)",
        margins_mm=(10.0, 11.0, 11.0, 11.0),
        pitch_mm=5.0,
        line_color="rgba(170, 178, 192, 0.38)",
        kind="grid",
    ),
    "ruled8": PaperSpec(
        id="ruled8",
        name="Exercise Ruled (8.0mm + Red Margin)",
        margins_mm=(18.0, 14.0, 16.0, 26.0),
        pitch_mm=8.0,
        margin_line_x_mm=22.0,
        line_color="rgba(152, 179, 211, 0.62)",
        has_header=True,
        kind="ruled",
    ),
    "lined": PaperSpec(
        id="lined",
        name="Ruled Lines (8.0mm + Red Margin)",
        margins_mm=(18.0, 14.0, 16.0, 26.0),
        pitch_mm=8.0,
        margin_line_x_mm=22.0,
        line_color="rgba(152, 179, 211, 0.62)",
        has_header=True,
        kind="ruled",
    ),
    "ruled7": PaperSpec(
        id="ruled7",
        name="Exercise Ruled (7.0mm + Red Margin)",
        margins_mm=(18.0, 14.0, 16.0, 26.0),
        pitch_mm=7.0,
        margin_line_x_mm=22.0,
        line_color="rgba(152, 179, 211, 0.60)",
        has_header=True,
        kind="ruled",
    ),
    "dots": PaperSpec(
        id="dots",
        name="Dot Matrix (5.0mm)",
        margins_mm=(12.0, 13.0, 12.0, 13.0),
        pitch_mm=5.0,
        line_color="rgba(155, 162, 175, 0.55)",
        kind="dots",
    ),
    "cream": PaperSpec(
        id="cream",
        name="Warm Cream Paper",
        margins_mm=(14.0, 15.0, 14.0, 15.0),
        bg_color="#faf8f3",
        kind="cream",
    ),
    "paper": PaperSpec(
        id="paper",
        name="Warm Cream Paper",
        margins_mm=(14.0, 15.0, 14.0, 15.0),
        bg_color="#faf8f3",
        kind="cream",
    ),
    "compo": PaperSpec(
        id="compo",
        name="Chinese Manuscript Grid (9.0mm)",
        margins_mm=(24.0, 15.0, 18.0, 15.0),
        pitch_mm=9.0,
        line_color="rgba(108, 159, 128, 0.55)",
        grid_lock=True,
        kind="compo",
    ),
}


def resolve_paper(paper_id: str, page_size: str = "A4") -> PaperSpec:
    """Resolve a paper preset with optional physical page size override."""
    spec = PAPERS.get(paper_id.lower().strip(), PAPERS["white"])
    ps = page_size.upper().strip()
    if ps in PAGE_SIZES_MM:
        w, h = PAGE_SIZES_MM[ps]
        if (w, h) != (spec.width_mm, spec.height_mm):
            spec = replace(spec, width_mm=w, height_mm=h)
    return spec


def usable_width_mm(spec: PaperSpec) -> float:
    """Return horizontal writable area width in mm."""
    return max(60.0, spec.width_mm - spec.margins_mm[1] - spec.margins_mm[3])


def paper_to_css(spec: PaperSpec) -> str:
    """Generate exact @page and background CSS rules using physical mm units."""
    t, r, b, l = spec.margins_mm
    page_rule = f"@page {{ size: {spec.width_mm:.1f}mm {spec.height_mm:.1f}mm; margin: {t:.1f}mm {r:.1f}mm {b:.1f}mm {l:.1f}mm; }}"

    if spec.kind == "grid":
        p = spec.pitch_mm or 6.2
        return f"""
        {page_rule}
        html {{
          background-color: {spec.bg_color};
          background-image:
            linear-gradient(to right, {spec.line_color} 0.75px, transparent 0.75px),
            linear-gradient(to bottom, {spec.line_color} 0.75px, transparent 0.75px);
          background-size: {p:.2f}mm {p:.2f}mm;
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }}
        body {{ background: transparent !important; }}
        """
    if spec.kind == "ruled":
        p = spec.pitch_mm or 8.0
        mx = spec.margin_line_x_mm or 22.0
        page_l = 10.0
        mx_in_page = max(2.0, mx - page_l)
        pad_l = max(0.0, l - page_l)
        ruled_page_rule = f"@page {{ size: {spec.width_mm:.1f}mm {spec.height_mm:.1f}mm; margin: {t:.1f}mm {r:.1f}mm {b:.1f}mm {page_l:.1f}mm; }}"
        return f"""
        {ruled_page_rule}
        html {{
          background-color: {spec.bg_color};
          background-image:
            linear-gradient(to right, transparent {mx_in_page - 0.35:.2f}mm, rgba(227, 164, 155, 0.80) {mx_in_page - 0.35:.2f}mm, rgba(227, 164, 155, 0.80) {mx_in_page:.2f}mm, transparent {mx_in_page:.2f}mm),
            linear-gradient(to bottom, transparent calc({p:.2f}mm - 0.75px), {spec.line_color} {p:.2f}mm);
          background-size: 100% 100%, 100% {p:.2f}mm;
          background-position: 0 0, 0 0;
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }}
        body {{ background: transparent !important; }}
        main#page-root {{ padding-left: {pad_l:.1f}mm; }}
        """
    if spec.kind == "dots":
        p = spec.pitch_mm or 5.0
        return f"""
        {page_rule}
        html {{
          background-color: {spec.bg_color};
          background-image: radial-gradient(circle, {spec.line_color} 0.75px, transparent 0.75px);
          background-size: {p:.2f}mm {p:.2f}mm;
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }}
        body {{ background: transparent !important; }}
        """
    if spec.kind == "compo":
        p = spec.pitch_mm or 9.0
        return f"""
        {page_rule}
        html {{
          background-color: {spec.bg_color};
          background-image:
            linear-gradient(to right, {spec.line_color} 0.75px, transparent 0.75px),
            linear-gradient(to bottom, {spec.line_color} 0.75px, transparent 0.75px),
            linear-gradient(to bottom, transparent calc({p * 1.33:.2f}mm - 1px), rgba(108, 159, 128, 0.25) {p * 1.33:.2f}mm);
          background-size: {p:.2f}mm {p * 1.33:.2f}mm, {p:.2f}mm {p * 1.33:.2f}mm, 100% {p * 1.33:.2f}mm;
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }}
        body {{ background: transparent !important; }}
        """
    if spec.kind == "cream":
        return f"""
        {page_rule}
        html {{
          background:
            radial-gradient(ellipse at 22% 18%, rgba(255,255,255,0.65), rgba(244,241,234,0.45) 70%, rgba(232,228,218,0.42)),
            {spec.bg_color};
          -webkit-print-color-adjust: exact;
          print-color-adjust: exact;
        }}
        body {{ background: transparent !important; }}
        """
    return f"""
    {page_rule}
    html, body {{
      background: {spec.bg_color};
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    """

