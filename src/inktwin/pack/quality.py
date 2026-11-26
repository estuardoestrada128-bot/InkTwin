"""Shared pack quality inspection, anomaly classification, cleanup planning, and local backups."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .. import LOCAL_PACKS_DIR
from .schema import HwPack

_SKIP_THICKNESS_CATS = frozenset({"frac_bar", "accent_bar", "dot", "punct"})


def pack_backup_path(pack_id: str) -> Path:
    """Return backup .hwpack path under packs/local/.backups/ (ignored by non-recursive list_packs)."""
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in (pack_id or "pack")) or "pack"
    return (LOCAL_PACKS_DIR / ".backups" / f"{safe}.bak.hwpack").resolve()


def pack_median_thickness(glyphs: Dict[str, dict]) -> float:
    """Compute pack-level median stroke thickness across non-bar/dot glyphs."""
    all_ts: List[float] = []
    for entry in glyphs.values():
        if entry.get("cat", "") in _SKIP_THICKNESS_CATS:
            continue
        for v in entry.get("v", []):
            if isinstance(v, dict) and isinstance(v.get("t"), (int, float)) and float(v["t"]) > 0:
                all_ts.append(float(v["t"]))
    return float(np.median(all_ts)) if all_ts else 4.8


def variant_anomaly_flags(v: dict, cat: str, med_t: float, char_med_asp: float = 0.0) -> List[str]:
    """Return localized anomaly tags (残缺 / 过粗 / 过细 / 比例偏离) for one variant."""
    w = float(v.get("w") or 70.0)
    h = float(v.get("h") or 100.0)
    t = float(v.get("t") or 0.0)
    d = str(v.get("d") or "")
    flags: List[str] = []
    if len(d.strip()) < 12:
        flags.append("残缺")
    if cat not in _SKIP_THICKNESS_CATS:
        if t > max(med_t * 1.55, 9.2):
            flags.append("过粗")
        elif 0.0 < t < min(med_t * 0.52, 2.1):
            flags.append("过细")
    if char_med_asp > 0.0 and w > 0.0 and h > 0.0:
        ratio = (w / h) / char_med_asp
        if ratio > 1.75 or ratio < 0.57:
            flags.append("比例偏离")
    return flags


def inspect_glyph_variants(ch: str, entry: dict, med_t: float) -> Tuple[List[dict], bool]:
    """Compute normalized metadata + anomaly flags for all variants of a single glyph."""
    cat = entry.get("cat", "cap")
    raw_vs = [v for v in entry.get("v", []) if isinstance(v, dict)]
    asps = [
        float(v["w"]) / float(v["h"])
        for v in raw_vs
        if float(v.get("h") or 0) > 0 and float(v.get("w") or 0) > 0
    ]
    char_med_asp = float(np.median(asps)) if len(asps) >= 2 else 0.0

    v_out: List[dict] = []
    char_has_flag = False
    for idx, v in enumerate(raw_vs):
        w = float(v.get("w") or 70.0)
        h = float(v.get("h") or 100.0)
        t = float(v.get("t") or 0.0)
        d = str(v.get("d") or "")
        flags = variant_anomaly_flags(v, cat, med_t, char_med_asp)
        if flags:
            char_has_flag = True
        score = 0.0
        if "残缺" not in flags:
            t_err = abs(t - med_t) / max(1.0, med_t) if t > 0 else 1.0
            asp_err = abs(((w / max(1.0, h)) / char_med_asp) - 1.0) if char_med_asp > 0 else 0.0
            score = round(t_err + asp_err, 4)
        else:
            score = 999.0
        v_out.append(
            {
                "vidx": idx,
                "w": round(w, 1),
                "h": round(h, 1),
                "t": round(t, 2),
                "fill": bool(v.get("fill", True)),
                "d": d,
                "flags": flags,
                "deviation_score": score,
            }
        )
    return v_out, char_has_flag


def build_cleanup_plan(pk: HwPack) -> dict:
    """Plan safe anomaly deletion with per-char fallback preservation & refill list."""
    med_t = pack_median_thickness(pk.glyphs)
    deletions: List[dict] = []
    kept_fallbacks: List[dict] = []
    flag_counts: Dict[str, int] = {"残缺": 0, "过粗": 0, "过细": 0, "比例偏离": 0}
    refill_priority3: List[str] = []
    refill_priority2: List[str] = []

    for ch, entry in pk.glyphs.items():
        cat = entry.get("cat", "cap")
        v_out, has_flag = inspect_glyph_variants(ch, entry, med_t)
        if not has_flag or not v_out:
            continue

        flagged = [v for v in v_out if v["flags"]]
        clean = [v for v in v_out if not v["flags"]]
        non_broken_flagged = [v for v in flagged if "残缺" not in v["flags"]]

        fallback_vidx: int | None = None
        if not clean and non_broken_flagged:
            # Keep the single closest-to-standard non-残缺 variant as temporary fallback
            best = min(non_broken_flagged, key=lambda x: (x["deviation_score"], x["vidx"]))
            fallback_vidx = best["vidx"]
            kept_fallbacks.append(
                {
                    "char": ch,
                    "cat": cat,
                    "vidx": best["vidx"],
                    "w": best["w"],
                    "h": best["h"],
                    "t": best["t"],
                    "fill": best["fill"],
                    "d": best["d"],
                    "flags": best["flags"],
                }
            )

        deleted_any_for_char = False
        for v in flagged:
            for fl in v["flags"]:
                flag_counts[fl] = flag_counts.get(fl, 0) + 1
            if v["vidx"] == fallback_vidx:
                continue
            deleted_any_for_char = True
            deletions.append(
                {
                    "char": ch,
                    "cat": cat,
                    "vidx": v["vidx"],
                    "w": v["w"],
                    "h": v["h"],
                    "t": v["t"],
                    "fill": v["fill"],
                    "d": v["d"],
                    "flags": v["flags"],
                }
            )

        # Classify refill priority for printable targeted sheet (skip internal struct placeholders only if empty)
        if not clean:
            refill_priority3.append(ch)
        elif deleted_any_for_char and len(clean) == 1:
            refill_priority2.append(ch)

    return {
        "pack": pk.id,
        "median_t": round(med_t, 2),
        "flag_counts": flag_counts,
        "deletions": deletions,
        "kept_fallbacks": kept_fallbacks,
        "refill_priority3": refill_priority3,
        "refill_priority2": refill_priority2,
    }
