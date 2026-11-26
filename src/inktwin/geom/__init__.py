"""Shared geometric primitives for InkTwin (pressure ribbons and elastic micro-deformation)."""

from __future__ import annotations

from .elastic import elastic_deform_loops
from .ribbon import chain_to_starnote_ribbon, pts_to_ribbon_d, ribbon_polygon_pts

__all__ = [
    "ribbon_polygon_pts",
    "pts_to_ribbon_d",
    "chain_to_starnote_ribbon",
    "elastic_deform_loops",
]

