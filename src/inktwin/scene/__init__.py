"""InkScene editable vector package, inspector, self-healing ops, and SVG/PDF exporter."""

from .inspector import build_rigid_groups, inspect_scene
from .ops import (
    apply_align,
    apply_drift,
    apply_spacing,
    apply_tighten,
    make_atomic_ops,
    next_batch_id,
    undo_last_batch,
)
from .renderer import export_scene, render_page_svg
from .schema import load_inkscene, resolve_pen_color, resolve_scene_state, save_edits, save_inkscene

__all__ = [
    "apply_align",
    "apply_drift",
    "apply_spacing",
    "apply_tighten",
    "build_rigid_groups",
    "export_scene",
    "inspect_scene",
    "load_inkscene",
    "make_atomic_ops",
    "next_batch_id",
    "render_page_svg",
    "resolve_pen_color",
    "resolve_scene_state",
    "save_edits",
    "save_inkscene",
    "undo_last_batch",
]

