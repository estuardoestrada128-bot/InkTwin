"""Self-healing operators (tighten, drift) and atomic edit builders for InkScene."""

from __future__ import annotations

import math
import random
import re
from typing import Dict, List, Optional

from .inspector import build_rigid_groups, inspect_scene
from .schema import resolve_scene_state


def next_batch_id(edits: List[dict]) -> int:
    if not edits:
        return 1
    return max(int(op.get("batch", 0)) for op in edits) + 1


def undo_last_batch(edits: List[dict]) -> tuple[List[dict], int]:
    if not edits:
        return [], 0
    last_b = int(edits[-1].get("batch", 0))
    kept = [op for op in edits if int(op.get("batch", 0)) != last_b]
    return kept, len(edits) - len(kept)


def make_atomic_ops(
    op_kind: str,
    *,
    ids: Optional[List[str]] = None,
    line: Optional[str] = None,
    page: Optional[int] = None,
    dx: float = 0.0,
    dy: float = 0.0,
    k: float = 1.0,
    about: str = "anchor",
    deg: float = 0.0,
    vidx: Optional[int] = None,
    color: Optional[str] = None,
    weight: Optional[float] = None,
    batch: int = 1,
) -> dict:
    """Create a validated atomic operation dictionary for edits.json."""
    target = {}
    if ids == ["all"]:
        target["ids"] = "all"
    elif ids:
        target["ids"] = list(ids)
    elif line:
        target["line"] = line
    elif page is not None:
        target["page"] = int(page)
    else:
        raise ValueError("Must specify target ids, line, or page")

    if op_kind == "move":
        return {"op": "move", **target, "dx": round(dx, 3), "dy": round(dy, 3), "batch": batch}
    if op_kind == "scale":
        return {"op": "scale", **target, "k": round(k, 3), "about": about, "batch": batch}
    if op_kind == "rotate":
        return {"op": "rotate", **target, "deg": round(deg, 2), "about": about, "batch": batch}
    if op_kind == "reroll":
        res = {"op": "reroll", **target, "batch": batch}
        if vidx is not None:
            res["vidx"] = int(vidx)
        return res
    if op_kind == "pen":
        pen: Dict[str, object] = {}
        if color:
            pen["color"] = color
        if weight is not None:
            pen["weight"] = float(weight)
        return {"op": "pen", **target, "pen": pen, "batch": batch}
    if op_kind in ("hide", "show"):
        return {"op": op_kind, **target, "batch": batch}
    raise ValueError(f"Unsupported op: {op_kind}")


def apply_tighten(
    scene: dict,
    edits: List[dict],
    line_ids: Optional[List[str]] = None,
    max_gap_ratio: float = 0.95,
    seed: int = 42,
) -> tuple[List[dict], List[dict]]:
    """Shrink abnormally wide gaps within lines cleanly while preserving downstream glyphs."""
    report = inspect_scene(scene, edits, max_gap_ratio=max_gap_ratio)
    resolved = resolve_scene_state(scene, edits)
    by_id = {g["id"]: g for g in resolved["glyphs"]}
    lines_map = {ln["id"]: ln for ln in resolved["lines"]}
    target_set = set(line_ids) if line_ids else None

    rng = random.Random(seed)
    gap_anomalies = [a for a in report["anomalies"] if a["kind"] == "gap"]
    if target_set is not None:
        gap_anomalies = [a for a in gap_anomalies if a["line"] in target_set]

    by_line_anoms: Dict[str, List[dict]] = {}
    for a in gap_anomalies:
        by_line_anoms.setdefault(a["line"], []).append(a)

    batch = next_batch_id(edits)
    new_ops: List[dict] = []
    for lid, anoms in by_line_anoms.items():
        ln = lines_map.get(lid)
        if not ln or ln.get("grid"):
            continue
        gids = ln["glyph_ids"]
        med_h = max(2.2, float(ln.get("med_h_mm") or 4.0))
        # Track cumulative left shift per glyph index on this line
        shifts = [0.0] * len(gids)
        gid_to_idx = {gid: i for i, gid in enumerate(gids)}
        for a in anoms:
            right_id = a["ids"][1]
            idx = gid_to_idx.get(right_id)
            if idx is None:
                continue
            want_gap = med_h * (0.20 + rng.random() * 0.14)
            delta = max(0.0, a["gap_mm"] - want_gap)
            if delta > 0.25:
                for k in range(idx, len(gids)):
                    shifts[k] -= delta
        for i, sh in enumerate(shifts):
            if abs(sh) > 0.2:
                new_ops.append({"op": "move", "ids": [gids[i]], "dx": round(sh, 3), "dy": 0.0, "batch": batch})

    if new_ops:
        new_ops.append({"op": "note", "text": f"tighten ({len(new_ops)} glyphs)", "batch": batch})
    return edits + new_ops, new_ops


def apply_drift(
    scene: dict,
    edits: List[dict],
    mode: str = "natural",
    amount: float = 1.0,
    seed: int = 42,
    force: bool = False,
) -> tuple[List[dict], List[dict]]:
    """Apply realistic organic left-margin horizontal drift across lines (never crossing page/margin bounds)."""
    resolved = resolve_scene_state(scene, edits)
    paper = resolved.get("paper") or {}
    if paper.get("grid_lock"):
        raise RuntimeError("Refusing drift on grid-locked paper (compo/tian).")
    if not force and any(op.get("op") == "note" and str(op.get("text", "")).startswith("drift (") for op in edits):
        raise RuntimeError("Page already drifted (pass --force or undo previous drift first).")

    page_w = float(paper.get("width_mm") or 210.0)
    margins = paper.get("margins_mm") or [13.0, 14.0, 13.0, 14.0]
    min_left = float(paper.get("margin_line_x_mm") or margins[3]) + 0.8
    max_right = page_w - float(margins[1]) * 0.55

    rng = random.Random(seed)
    amt = max(0.1, min(2.0, float(amount)))
    batch = next_batch_id(edits)
    new_ops: List[dict] = []
    q_start_re = re.compile(r"^(?:\d+[\.、\)]|第\d+题|\(\d+\))")

    by_id = {g["id"]: g for g in resolved["glyphs"] if not g.get("hidden")}
    rigid_groups = build_rigid_groups(resolved["glyphs"])
    group_members: Dict[str, List[str]] = {}
    for gid, grp in rigid_groups.items():
        group_members.setdefault(grp, []).append(gid)
    drifted_groups = set()

    for p in range(resolved["pages_detected"]):
        p_lines = [ln for ln in resolved["lines"] if ln["page"] == p and not ln.get("grid")]
        creep = 0.0
        for idx, ln in enumerate(p_lines):
            txt = ln.get("text", "").strip()
            if q_start_re.match(txt):
                creep *= 0.25
            else:
                creep = min(2.4 * amt, creep + (0.14 + rng.random() * 0.22) * amt)

            wave = math.sin(idx * 0.65 + rng.uniform(0, 0.5)) * 0.65 * amt
            noise = (rng.random() - 0.5) * 0.9 * amt
            base_dx = (wave + noise) if mode == "random" else (creep + wave * 0.7 + noise * 0.6)

            line_groups: List[str] = []
            seen_lg = set()
            free_gids: List[str] = []
            for gid in ln["glyph_ids"]:
                if gid not in by_id:
                    continue
                grp = rigid_groups.get(gid)
                if grp:
                    if grp not in seen_lg:
                        seen_lg.add(grp)
                        line_groups.append(grp)
                else:
                    free_gids.append(gid)

            for grp in line_groups:
                if grp in drifted_groups:
                    continue
                drifted_groups.add(grp)
                mids = [gid for gid in group_members.get(grp, []) if gid in by_id]
                if not mids:
                    continue
                g_left = min(by_id[gid]["x"] for gid in mids)
                g_right = max(by_id[gid]["x"] + by_id[gid]["w"] for gid in mids)
                gdx = base_dx
                if g_left + gdx < min_left:
                    gdx = min_left - g_left
                if g_right + gdx > max_right:
                    gdx = max_right - g_right
                if abs(gdx) >= 0.15:
                    new_ops.append({"op": "move", "ids": mids, "dx": round(gdx, 3), "dy": 0.0, "batch": batch})

            if free_gids:
                f_left = min(by_id[gid]["x"] for gid in free_gids)
                f_right = max(by_id[gid]["x"] + by_id[gid]["w"] for gid in free_gids)
                fdx = base_dx
                if f_left + fdx < min_left:
                    fdx = min_left - f_left
                if f_right + fdx > max_right:
                    fdx = max_right - f_right
                if abs(fdx) >= 0.15:
                    new_ops.append({"op": "move", "ids": free_gids, "dx": round(fdx, 3), "dy": 0.0, "batch": batch})

    if new_ops:
        new_ops.append({"op": "note", "text": f"drift ({mode}, amount={amt})", "batch": batch})
    return edits + new_ops, new_ops

