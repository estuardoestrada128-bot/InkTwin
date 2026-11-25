"""Geometry & layout inspector for InkScene (gaps, overlaps, margin overflow, prompt collisions)."""

from __future__ import annotations

import statistics
from typing import Dict, List

from .schema import resolve_scene_state


def _overlap_area(a: dict, b: dict) -> float:
    x0 = max(a["x"], b["x"])
    y0 = max(a["y"], b["y"])
    x1 = min(a["x"] + a["w"], b["x"] + b["w"])
    y1 = min(a["y"] + a["h"], b["y"] + b["h"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def inspect_scene(scene: dict, edits: List[dict], max_gap_ratio: float = 0.95) -> dict:
    """Scan resolved InkScene for spacing gaps, glyph overlaps, margin/page violations, and ruler-straight left edges."""
    resolved = resolve_scene_state(scene, edits)
    paper = resolved.get("paper") or {}
    page_w = float(paper.get("width_mm") or 210.0)
    page_h = float(paper.get("height_mm") or 297.0)
    margin_line_x = paper.get("margin_line_x_mm")
    grid_lock = bool(paper.get("grid_lock", False))

    by_id: Dict[str, dict] = {g["id"]: g for g in resolved["glyphs"] if not g.get("hidden")}
    obstacles = resolved.get("obstacles", [])

    anomalies: List[dict] = []

    # 1. Line-level gap & overlap checks
    for ln in resolved.get("lines", []):
        g_list = [by_id[gid] for gid in ln["glyph_ids"] if gid in by_id]
        if len(g_list) < 2:
            continue
        med_h = max(2.2, float(ln.get("med_h_mm") or 4.0))

        # Horizontal gaps
        if not ln.get("grid", False):
            for idx in range(len(g_list) - 1):
                a, b = g_list[idx], g_list[idx + 1]
                # Skip vertical/math sub/superscript pairs or accent decorations over same x
                if a.get("cat") == "struct" or b.get("cat") == "struct":
                    continue
                gap_mm = b["x"] - (a["x"] + a["w"])
                # Ignore wide intentional multi-column gaps (>= 11 mm) or negative kerning
                punct_extra = 0.35 if (a.get("ch") in "，。；：,.;:") else 0.0
                limit = med_h * (max_gap_ratio + punct_extra)
                if limit < gap_mm < 11.0:
                    anomalies.append(
                        {
                            "kind": "gap",
                            "page": ln["page"],
                            "line": ln["id"],
                            "ids": [a["id"], b["id"]],
                            "chars": f"{a.get('ch', '')}{b.get('ch', '')}",
                            "gap_mm": round(gap_mm, 2),
                            "limit_mm": round(limit, 2),
                        }
                    )

        # Excessive overlaps (excluding sub/superscript or structural symbols)
        for i in range(len(g_list)):
            a = g_list[i]
            if a.get("cat") == "struct" or a.get("in_math"):
                continue
            area_a = max(0.01, a["w"] * a["h"])
            for j in range(i + 1, min(len(g_list), i + 4)):
                b = g_list[j]
                if b.get("cat") == "struct" or b.get("in_math"):
                    continue
                area_b = max(0.01, b["w"] * b["h"])
                inter = _overlap_area(a, b)
                if inter > 0.42 * min(area_a, area_b):
                    anomalies.append(
                        {
                            "kind": "overlap",
                            "page": ln["page"],
                            "line": ln["id"],
                            "ids": [a["id"], b["id"]],
                            "chars": f"{a.get('ch', '')}{b.get('ch', '')}",
                            "ratio": round(inter / min(area_a, area_b), 2),
                        }
                    )

    # 2. Boundary, margin line & prompt collision checks
    for g in by_id.values():
        gid = g["id"]
        gx, gy, gw, gh = g["x"], g["y"], g["w"], g["h"]
        if gx < 2.5 or (gx + gw) > page_w - 2.5 or gy < 2.5 or (gy + gh) > page_h - 2.5:
            anomalies.append(
                {
                    "kind": "outside_margin",
                    "page": g["page"],
                    "line": g.get("line", ""),
                    "ids": [gid],
                    "chars": g.get("ch", ""),
                    "box_mm": [round(gx, 2), round(gy, 2), round(gw, 2), round(gh, 2)],
                }
            )
        elif margin_line_x is not None and gx < float(margin_line_x) - 8.5 and gw > 1.5:
            anomalies.append(
                {
                    "kind": "outside_margin",
                    "page": g["page"],
                    "line": g.get("line", ""),
                    "ids": [gid],
                    "chars": g.get("ch", ""),
                    "reason": "crossed_red_margin_line",
                }
            )

        for obs in obstacles:
            if obs.get("page") != g["page"]:
                continue
            inter = _overlap_area(g, obs)
            if inter > 0.8 and gw * gh > 0.5:
                anomalies.append(
                    {
                        "kind": "prompt_collision",
                        "page": g["page"],
                        "line": g.get("line", ""),
                        "ids": [gid],
                        "chars": g.get("ch", ""),
                        "overlap_mm2": round(inter, 2),
                    }
                )
                break

    # 3. Check ruler-straight left edges across pages
    aligned_pages: List[int] = []
    hints: List[str] = []
    if not grid_lock:
        for p in range(resolved["pages_detected"]):
            p_lines = [ln for ln in resolved["lines"] if ln["page"] == p and len(ln["glyph_ids"]) >= 4]
            if len(p_lines) >= 4:
                lefts = [ln["left_mm"] for ln in p_lines]
                if statistics.pstdev(lefts) < 0.35:
                    aligned_pages.append(p)
        if aligned_pages:
            hints.append("run `inktwin scene drift` for organic left-edge variation")

    counts: Dict[str, int] = {}
    for a in anomalies:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1

    return {
        "ok": True,
        "pages": resolved["pages_detected"],
        "glyph_count": len(by_id),
        "line_count": len(resolved["lines"]),
        "edits_count": len(edits),
        "anomaly_counts": counts,
        "anomalies": anomalies,
        "left_edges_aligned_pages": aligned_pages,
        "hints": hints,
        "lines": [
            {
                "id": ln["id"],
                "page": ln["page"],
                "text": ln["text"][:48],
                "left_mm": ln["left_mm"],
                "right_mm": ln["right_mm"],
                "glyphs": len(ln["glyph_ids"]),
            }
            for ln in resolved["lines"]
        ],
    }

