"""InkScene schema, non-destructive edits.json stack, and state resolver."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..pack import HwPack
from ..paper import PaperSpec

PEN_COLOR_PRESETS: Dict[str, str] = {
    "black": "#111318",
    "blue": "#19378a",
    "darkblue": "#13244b",
    "pencil": "#30343d",
    "red": "#c6332f",
    "green": "#1f6f4a",
}


def resolve_pen_color(color: Optional[str]) -> Optional[str]:
    if not color:
        return None
    c = color.strip()
    if c.lower() in PEN_COLOR_PRESETS:
        return PEN_COLOR_PRESETS[c.lower()]
    if c.startswith("#") and len(c) in (4, 7):
        return c
    return None


def save_inkscene(
    scene_dir: Path,
    raw_scene: dict,
    paper_spec: PaperSpec,
    pack: HwPack,
    synth_cjk: Optional[Dict[str, dict]] = None,
    ink_key: str = "black",
    font_size_pt: float = 12.0,
    background_pdf: Optional[str] = None,
) -> Path:
    """Persist immutable scene.json + initial edits.json inside scene_dir."""
    scene_dir.mkdir(parents=True, exist_ok=True)
    glyphs = raw_scene.get("glyphs", [])
    used_chars = {g.get("ch", "") for g in glyphs if g.get("ch")}

    variants_pool: Dict[str, List[dict]] = {}
    for ch in sorted(used_chars):
        entry = pack.glyphs.get(ch) or (synth_cjk or {}).get(ch)
        if entry and entry.get("v"):
            variants_pool[ch] = [
                {
                    "w": float(v.get("w", 100.0)),
                    "h": float(v.get("h", 100.0)),
                    "fill": bool(v.get("fill", True)),
                    "t": float(v.get("t", 4.8)),
                    "d": v.get("d", ""),
                }
                for v in entry["v"]
                if v.get("d")
            ]

    scene_obj = {
        "schema_version": "1.0",
        "pack_id": pack.id,
        "ink": ink_key,
        "font_size_pt": font_size_pt,
        "paper": asdict(paper_spec),
        "pages_detected": max(1, int(raw_scene.get("pages_detected", 1))),
        "glyphs": glyphs,
        "lines": raw_scene.get("lines", []),
        "obstacles": raw_scene.get("obstacles", []),
        "variants_pool": variants_pool,
        "background_pdf": background_pdf,
    }
    (scene_dir / "scene.json").write_text(
        json.dumps(scene_obj, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    edits_path = scene_dir / "edits.json"
    if not edits_path.exists():
        edits_path.write_text('{"edits":[]}\n', encoding="utf-8")
    return scene_dir


def load_inkscene(path: Path) -> Tuple[dict, List[dict], Path]:
    """Load (scene_dict, edits_list, scene_dir) from scene directory or scene.json path."""
    p = path.resolve()
    scene_dir = p.parent if p.is_file() and p.name == "scene.json" else p
    scene_file = scene_dir / "scene.json"
    if not scene_file.exists():
        raise FileNotFoundError(f"InkScene not found: {scene_file}")
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    edits_file = scene_dir / "edits.json"
    edits: List[dict] = []
    if edits_file.exists():
        raw_e = json.loads(edits_file.read_text(encoding="utf-8"))
        edits = list(raw_e.get("edits", [])) if isinstance(raw_e, dict) else []
    return scene, edits, scene_dir


def save_edits(scene_dir: Path, edits: List[dict]) -> Path:
    edits_file = scene_dir / "edits.json"
    if edits_file.exists():
        (scene_dir / "edits.bak.json").write_text(edits_file.read_text(encoding="utf-8"), encoding="utf-8")
    edits_file.write_text(json.dumps({"edits": edits}, ensure_ascii=False, indent=2), encoding="utf-8")
    return edits_file


def _select_glyphs(glyphs: List[dict], op: dict) -> List[dict]:
    ids = op.get("ids")
    line_id = op.get("line")
    line_ids = set(op.get("lines", [])) if op.get("lines") else (({line_id} if line_id else set()))
    page = op.get("page")

    if ids == "all":
        return list(glyphs)
    if isinstance(ids, (list, tuple, set)):
        id_set = set(ids)
        return [g for g in glyphs if g.get("id") in id_set]
    if line_ids:
        return [g for g in glyphs if g.get("line") in line_ids]
    if page is not None:
        return [g for g in glyphs if g.get("page") == int(page)]
    return []


def resolve_scene_state(scene: dict, edits: List[dict]) -> dict:
    """Apply edits.json sequentially onto baseline scene.json without mutating inputs."""
    glyphs = []
    for g in scene.get("glyphs", []):
        gc = copy.deepcopy(g)
        gc.setdefault("sw_add", 0.0)
        gc.setdefault("hidden", False)
        gc.setdefault("rot", 0.0)
        glyphs.append(gc)

    variants_pool = scene.get("variants_pool", {})

    for op in edits:
        kind = op.get("op")
        if kind == "note":
            continue
        targets = _select_glyphs(glyphs, op)
        if not targets:
            continue

        if kind == "move":
            dx = float(op.get("dx", 0.0))
            dy = float(op.get("dy", 0.0))
            for g in targets:
                g["x"] = round(g["x"] + dx, 4)
                g["y"] = round(g["y"] + dy, 4)

        elif kind == "scale":
            k = max(0.1, min(10.0, float(op.get("k", 1.0))))
            about = op.get("about", "anchor")
            if about == "selection":
                cx = 0.5 * (min(g["x"] for g in targets) + max(g["x"] + g["w"] for g in targets))
                cy = 0.5 * (min(g["y"] for g in targets) + max(g["y"] + g["h"] for g in targets))
                for g in targets:
                    gcx = g["x"] + g["w"] * 0.5
                    gcy = g["y"] + g["h"] * 0.5
                    ncx = cx + (gcx - cx) * k
                    ncy = cy + (gcy - cy) * k
                    g["w"] = round(g["w"] * k, 4)
                    g["h"] = round(g["h"] * k, 4)
                    g["x"] = round(ncx - g["w"] * 0.5, 4)
                    g["y"] = round(ncy - g["h"] * 0.5, 4)
            else:
                for g in targets:
                    old_w, old_h = g["w"], g["h"]
                    nw, nh = old_w * k, old_h * k
                    if about == "anchor":
                        g["x"] = round(g["x"] + (old_w - nw) * 0.5, 4)
                        g["y"] = round(g["y"] + (old_h - nh), 4)
                    else:
                        g["x"] = round(g["x"] + (old_w - nw) * 0.5, 4)
                        g["y"] = round(g["y"] + (old_h - nh) * 0.5, 4)
                    g["w"] = round(nw, 4)
                    g["h"] = round(nh, 4)

        elif kind == "rotate":
            deg = float(op.get("deg", 0.0))
            for g in targets:
                g["rot"] = round(g.get("rot", 0.0) + deg, 2)

        elif kind == "reroll":
            for g in targets:
                pool = variants_pool.get(g.get("ch", ""))
                if not pool or len(pool) < 2:
                    continue
                cur_idx = int(g.get("vidx", 0))
                new_idx = int(op["vidx"]) % len(pool) if "vidx" in op else (cur_idx + 1) % len(pool)
                nv = pool[new_idx]
                old_bottom = g["y"] + g["h"]
                new_w = g["h"] * (float(nv["w"]) / max(1.0, float(nv["h"])))
                g["vidx"] = new_idx
                g["vb"] = [0.0, 0.0, float(nv["w"]), float(nv["h"])]
                g["w"] = round(new_w, 4)
                g["y"] = round(old_bottom - g["h"], 4)
                g["paths"] = [{"d": nv["d"], "fill": bool(nv.get("fill", True)), "sw": 0.04}]

        elif kind == "pen":
            pen = op.get("pen") or {}
            col = resolve_pen_color(pen.get("color"))
            w_val = pen.get("weight")
            for g in targets:
                if col:
                    g["color"] = col
                    for p in g.get("paths", []):
                        if isinstance(p, dict) and "color" in p:
                            p["color"] = col
                if w_val is not None:
                    g["sw_add"] = round(float(w_val) * 0.25, 3)

        elif kind == "hide":
            for g in targets:
                g["hidden"] = True

        elif kind == "show":
            for g in targets:
                g["hidden"] = False

    # Recompute line bounds dynamically after edits
    by_line: Dict[str, List[dict]] = {}
    for g in glyphs:
        if not g.get("hidden"):
            by_line.setdefault(g.get("line", ""), []).append(g)

    lines = []
    for ln in scene.get("lines", []):
        lid = ln["id"]
        items = by_line.get(lid, [])
        if not items:
            continue
        items_sorted = sorted(items, key=lambda it: it["x"])
        hs = sorted(it["h"] for it in items_sorted)
        lines.append(
            {
                "id": lid,
                "page": ln["page"],
                "glyph_ids": [it["id"] for it in items_sorted],
                "text": "".join(it.get("ch") or "?" for it in items_sorted),
                "left_mm": round(min(it["x"] for it in items_sorted), 2),
                "right_mm": round(max(it["x"] + it["w"] for it in items_sorted), 2),
                "baseline_y_mm": round(sum(it["y"] + it["h"] * 0.5 for it in items_sorted) / len(items_sorted), 2),
                "med_h_mm": round(hs[len(hs) // 2], 2),
                "grid": bool(ln.get("grid", False)),
            }
        )

    return {
        "schema_version": scene.get("schema_version", "1.0"),
        "pack_id": scene.get("pack_id", ""),
        "ink": scene.get("ink", "black"),
        "paper": scene.get("paper", {}),
        "pages_detected": max([1] + [int(g.get("page", 0)) + 1 for g in glyphs]),
        "glyphs": glyphs,
        "lines": lines,
        "obstacles": scene.get("obstacles", []),
        "background_pdf": scene.get("background_pdf"),
    }

