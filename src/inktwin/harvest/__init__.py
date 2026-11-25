"""Phase 3 personal handwriting collection templates, vector harvester, and EDT calibrator."""

from .calibrator import (
    calibrate_radical_item,
    calibrate_variant_polygon,
    measure_fitz_stroke_thickness,
    measure_style_dna,
    polygon_to_stroke_dna_item,
    pressure_preserve_thin_variant,
    rdp,
)
from .harvester import harvest_template_pdf
from .templates import CellSpec, build_template_cells, generate_template_pdf, read_template_spec_from_pdf

__all__ = [
    "CellSpec",
    "build_template_cells",
    "calibrate_radical_item",
    "calibrate_variant_polygon",
    "generate_template_pdf",
    "harvest_template_pdf",
    "measure_fitz_stroke_thickness",
    "measure_style_dna",
    "polygon_to_stroke_dna_item",
    "pressure_preserve_thin_variant",
    "rdp",
    "read_template_spec_from_pdf",
]

