"""Compute normalized 0-100 handwriting style facets from glyphs & style DNA."""

from __future__ import annotations

from typing import Dict

import numpy as np


def _clamp_int(val: float) -> int:
    return int(max(0, min(100, round(val))))


def compute_facets(glyphs: Dict[str, dict], style_model: dict) -> Dict[str, int]:
    """Compute 9 style facets (0-100) describing stroke geometry and rhythm."""
    aspect = float(style_model.get("aspect_median", style_model.get("aspect", 0.70)))
    horiz_deg = float(style_model.get("horiz_tilt_deg", style_model.get("horiz_angle_deg", -10.0)))
    vert_deg = float(style_model.get("vert_lean_deg", style_model.get("vert_angle_deg", 6.0)))
    connect_ratio = float(style_model.get("connect_ratio", 0.50))

    ts = []
    cjk_ws = []
    for entry in glyphs.values():
        cat = entry.get("cat", "")
        for v in entry.get("v", []):
            if isinstance(v, dict) and isinstance(v.get("t"), (int, float)):
                ts.append(float(v["t"]))
            if cat == "cjk" and isinstance(v, dict) and v.get("h", 0) > 0:
                cjk_ws.append(float(v.get("w", 70)) / float(v["h"]))

    med_t = float(np.median(ts)) if ts else 4.8
    t_std = float(np.std(ts)) if len(ts) > 3 else 1.0
    cjk_asp_std = float(np.std(cjk_ws)) if len(cjk_ws) > 3 else 0.14

    # Aspect: narrow/tall (<0.65) -> higher score
    aspect_score = _clamp_int((1.05 - aspect) * 140.0)
    # Compactness: higher when glyphs are narrow & tight
    compact_score = _clamp_int((0.95 - aspect) * 160.0)
    # Slant: right-slanting vertical strokes + upward-tilting horizontals
    slant_score = _clamp_int(35.0 + abs(vert_deg) * 3.5 + max(0.0, -horiz_deg) * 1.4)
    # Joined: directly reflects measured stroke connection ratio
    joined_score = _clamp_int(connect_ratio * 100.0)
    # Weight: normalized stroke thickness t (~3..9 -> 15..85)
    weight_score = _clamp_int((med_t - 2.5) * 13.5)
    # Neatness: lower variance in stroke width & glyph proportions = neater
    neat_score = _clamp_int(82.0 - t_std * 10.0 - cjk_asp_std * 65.0)
    # Beauty: balanced proportions + consistent stroke weight
    beauty_score = _clamp_int(68.0 - abs(aspect - 0.72) * 40.0 - t_std * 4.5 + neat_score * 0.12)
    # Roundness: moderate curvature without sharp rigid corners
    round_score = _clamp_int(52.0 - abs(horiz_deg) * 0.6 + (1.0 - connect_ratio) * 14.0)
    # Ink darkness baseline (calibrated clear StarNote 0.74px)
    ink_score = _clamp_int(62.0 + (med_t - 4.5) * 4.0)

    return {
        "neat": neat_score,
        "beauty": beauty_score,
        "compact": compact_score,
        "joined": joined_score,
        "slant": slant_score,
        "aspect": aspect_score,
        "round": round_score,
        "weight": weight_score,
        "ink": ink_score,
    }
