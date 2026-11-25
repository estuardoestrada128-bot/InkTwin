"""Worksheet parsing and blank PDF layout overlay utilities."""

from .docx_parser import auto_find_docx, extract_week_number, parse_docx_worksheet, parse_markdown_answers
from .pdf_overlay import apply_marks_to_scene, detect_pdf_layout, stamp_scene_onto_pdf

__all__ = [
    "apply_marks_to_scene",
    "auto_find_docx",
    "detect_pdf_layout",
    "extract_week_number",
    "parse_docx_worksheet",
    "parse_markdown_answers",
    "stamp_scene_onto_pdf",
]
