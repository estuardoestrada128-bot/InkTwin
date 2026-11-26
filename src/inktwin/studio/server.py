"""InkTwin Studio backend: forge packs, detect worksheet boxes, render, inspect, edit, and export.

Pure Python stdlib HTTP server bound to 127.0.0.1 (100% offline, zero build step).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import fitz
import numpy as np

from .. import LOCAL_PACKS_DIR, TEMPLATES_DIR, __version__
from ..fontgen import build_opentype_font
from ..harvest import generate_template_pdf, harvest_template_pdf
from ..harvest.templates import _CORE_RADICALS
from ..overlay import detect_pdf_layout
from ..pack import list_packs, load_pack
from ..pack.facets import compute_facets
from ..pack.quality import build_cleanup_plan, inspect_glyph_variants, pack_backup_path, pack_median_thickness
from ..pack.schema import save_pack_zip
from ..preflight import run_doctor
from ..scene import export_scene, inspect_scene, load_inkscene
from ..scene.ops import apply_drift, apply_tighten, make_atomic_ops, next_batch_id, undo_last_batch
from ..scene.schema import save_edits

STUDIO_HTML_PATH = Path(__file__).resolve().parent / "studio.html"

_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".hwpack": "application/zip",
    ".md": "text/markdown; charset=utf-8",
}


def _parse_json_tail(text: str) -> Optional[dict]:
    """Return the last top-level JSON object printed to stdout."""
    dec = json.JSONDecoder()
    objs: List[dict] = []
    i = 0
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text, j)
            if isinstance(obj, dict):
                objs.append(obj)
            i = end
        except ValueError:
            i = j + 1
    return objs[-1] if objs else None


def _run_cli(argv: List[str]) -> Tuple[int, Optional[dict], str]:
    from ..cli import main as cli_main

    buf = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = int(cli_main(argv))
    except SystemExit as se:
        rc = int(se.code) if isinstance(se.code, int) else 1
    out = buf.getvalue()
    return rc, _parse_json_tail(out), out + err.getvalue()


def _b64_bytes(data: str) -> bytes:
    if "," in data[:120] and data.lstrip().startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def _pdf_previews(pdf: Path, dpi: int = 96, max_pages: int = 10) -> List[str]:
    out: List[str] = []
    with fitz.open(str(pdf)) as doc:
        for i in range(min(len(doc), max_pages)):
            pix = doc[i].get_pixmap(dpi=dpi)
            out.append("data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii"))
    return out


def _pdf_vector_previews(st: StudioState, pdf: Path, prefix: str, max_pages: int = 12) -> List[str]:
    """Export crisp vector SVG page previews (sharp at 400%+ zoom) and return /files/ URLs."""
    pdir = st.workdir / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    tag = st.next_id()
    urls: List[str] = []
    with fitz.open(str(pdf)) as doc:
        for i in range(min(len(doc), max_pages)):
            svg_txt = doc[i].get_svg_image(text_as_path=True)
            fp = pdir / f"{prefix}_{tag}_p{i + 1}.svg"
            fp.write_text(svg_txt, encoding="utf-8")
            urls.append(st.url_for(fp))
    return urls


class StudioState:
    def __init__(self, workdir: Path, dev_reload: bool = False):
        self.workdir = workdir.resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.dev_reload = bool(dev_reload)
        self.boot_id = uuid.uuid4().hex[:10]
        self.counter = 0
        self.current: Optional[dict] = None
        self.lock = threading.Lock()
        self.editor_servers: List[ThreadingHTTPServer] = []
        self._load_current()

    def next_id(self) -> str:
        self.counter += 1
        return f"{self.counter:03d}"

    def url_for(self, p: Path) -> str:
        rel = Path(p).resolve().relative_to(self.workdir)
        return "/files/" + rel.as_posix()

    def save_current(self) -> None:
        meta_path = self.workdir / "current.json"
        if not self.current:
            if meta_path.exists():
                meta_path.unlink(missing_ok=True)
            return
        cur = self.current
        payload = {
            "counter": self.counter,
            "run": cur["run"],
            "dir": str(cur["dir"]),
            "scene": str(cur["scene"]),
            "pdf": str(cur["pdf"]),
            "pen": cur.get("pen", "gel"),
            "ink": cur.get("ink", "black"),
            "prev_ink": cur.get("prev_ink", "black"),
            "nib": float(cur.get("nib", 0.5)),
            "seed": cur.get("seed", 42),
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_current(self) -> None:
        meta_path = self.workdir / "current.json"
        if not meta_path.is_file():
            return
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            rdir = Path(data["dir"])
            sdir = Path(data["scene"])
            pdf_p = Path(data["pdf"])
            if (sdir / "scene.json").is_file() and pdf_p.is_file():
                self.counter = max(self.counter, int(data.get("counter", 0)))
                self.current = {
                    "run": str(data["run"]),
                    "dir": rdir,
                    "scene": sdir,
                    "pdf": pdf_p,
                    "pen": str(data.get("pen") or "gel"),
                    "ink": str(data.get("ink") or "black"),
                    "prev_ink": str(data.get("prev_ink") or "black"),
                    "nib": float(data.get("nib") or 0.5),
                    "seed": int(data.get("seed") or 42),
                    "render": None,
                }
        except Exception:
            pass


# ----------------------------------------------------------------------------- API handlers


def api_state(st: StudioState, _p: dict) -> dict:
    return {
        "ok": True,
        "version": __version__,
        "workdir": str(st.workdir),
        "dev_reload": st.dev_reload,
        "boot_id": st.boot_id,
        "doctor": run_doctor(),
        "packs": list_packs(),
        "current_run": st.current["run"] if st.current else None,
    }


def api_preflight(st: StudioState, p: dict) -> dict:
    md = st.workdir / f"preflight_{st.next_id()}.md"
    md.write_text(p.get("markdown", ""), encoding="utf-8")
    paper_arg = p.get("paper") or "grid"
    argv = [
        "preflight", str(md),
        "--pack", p.get("pack") or "demo_sample",
        "--paper", paper_arg,
        "--page-size", p.get("page_size") or "A4",
        "--font-size", str(float(p.get("font_size") or 12.0)),
        "--no-docx", "--json",
    ]
    rc, res, log = _run_cli(argv)
    if rc != 0 or res is None:
        raise RuntimeError(f"preflight failed: {log[-800:]}")
    return {"ok": True, "report": res}


def _count_batches(edits: list) -> int:
    return len({int(op.get("batch", i)) for i, op in enumerate(edits)})


def _edits_sig(scene_dir: Path) -> str:
    ef = Path(scene_dir) / "edits.json"
    if not ef.is_file():
        return ""
    st_f = ef.stat()
    return f"{st_f.st_mtime_ns}:{st_f.st_size}"


def _snapshot(st: StudioState) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render. Render a document first.")
    scene, edits, _ = load_inkscene(cur["scene"])
    insp = inspect_scene(scene, edits)
    cur["edits_sig"] = _edits_sig(cur["scene"])
    ac = dict(insp.get("anomaly_counts", {}))
    aligned_pages = insp.get("left_edges_aligned_pages") or []
    if aligned_pages:
        ac["left_edges_aligned"] = len(aligned_pages)
    raw_hints = insp.get("hints", [])
    friendly_hints = [
        ("多行左边缘偏齐，可点下方「增加左缘起伏」让排版更自然" if "inktwin scene drift" in str(h) else str(h))
        for h in raw_hints
    ]
    return {
        "ok": True,
        "run": cur["run"],
        "render": cur.get("render"),
        "pdf_url": st.url_for(cur["pdf"]),
        "pen_type": cur.get("pen", "gel"),
        "ink": cur.get("ink", "black"),
        "nib_mm": float(cur.get("nib", 0.5)),
        "seed": cur.get("seed"),
        "grid_lock": bool((scene.get("paper") or {}).get("grid_lock", False)),
        "previews": _pdf_vector_previews(st, cur["pdf"], f"run{cur['run']}"),
        "edits_count": len(edits),
        "batches_count": _count_batches(edits),
        "inspect": {
            "pages": insp.get("pages"),
            "glyph_count": insp.get("glyph_count"),
            "line_count": insp.get("line_count"),
            "anomaly_counts": ac,
            "hints": friendly_hints,
        },
    }


def api_render(st: StudioState, p: dict) -> dict:
    for es in st.editor_servers:
        try:
            es.shutdown()
            es.server_close()
        except Exception:
            pass
    st.editor_servers.clear()

    rid = st.next_id()
    rdir = st.workdir / f"run_{rid}"
    rdir.mkdir(parents=True, exist_ok=True)
    md = rdir / "input.md"
    md.write_text(p.get("markdown", ""), encoding="utf-8")
    out_pdf = rdir / "output.pdf"
    sdir = rdir / "scene"
    pen = p.get("pen_type") or "gel"
    ink = p.get("ink") or "black"
    nib = float(p.get("nib_mm") or 0.5)
    seed = int(p["seed"]) if p.get("seed") not in (None, "") else random.randint(1, 999999)

    argv = [
        "render", str(md), "-o", str(out_pdf),
        "--pack", p.get("pack") or "demo_sample",
        "--save-scene", str(sdir), "--json",
        "--paper", p.get("paper") or "auto",
        "--ink", ink,
        "--mood", p.get("mood") or "normal",
        "--pen-type", pen,
        "--page-size", p.get("page_size") or "A4",
        "--font-size", str(float(p.get("font_size") or 12.0)),
        "--seed", str(seed),
    ]
    if p.get("tag"):
        argv += ["--tag", str(p["tag"])]
    if p.get("elastic") is False:
        argv.append("--no-elastic")

    if p.get("overlay_pdf_b64"):
        blank = rdir / "blank.pdf"
        blank.write_bytes(_b64_bytes(p["overlay_pdf_b64"]))
        argv += ["--overlay", str(blank)]
        if p.get("layout"):
            lay = dict(p["layout"])
            lay["source_pdf"] = str(blank)
            lj = rdir / "layout.json"
            lj.write_text(json.dumps(lay, ensure_ascii=False, indent=2), encoding="utf-8")
            argv += ["--layout-json", str(lj)]
    else:
        argv += ["--no-docx"]

    rc, res, log = _run_cli(argv)
    if rc != 0 or not out_pdf.exists():
        raise RuntimeError(f"render failed (rc={rc}): {log[-1200:]}")
    if abs(nib - 0.5) > 0.02:
        export_scene(sdir, out_pdf=out_pdf, fmt="pdf", dpi=300, pen_type=pen, seed=seed, nib_mm=nib, ink_override=ink)
    st.current = {
        "run": rid,
        "dir": rdir,
        "scene": sdir,
        "pdf": out_pdf,
        "pen": pen,
        "ink": ink,
        "prev_ink": ("black" if ink == "pencil" else ink),
        "nib": nib,
        "seed": seed,
        "render": res,
    }
    st.save_current()
    return _snapshot(st)


def api_scene_ops(st: StudioState, p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    scene, edits, sdir = load_inkscene(cur["scene"])
    action = p.get("action")
    added_count = 0
    if action == "tighten":
        new_edits, added = apply_tighten(scene, edits)
        added_count = sum(1 for op in added if op.get("op") != "note")
    elif action == "drift":
        new_edits, added = apply_drift(scene, edits, force=bool(p.get("force", True)))
        added_count = sum(1 for op in added if op.get("op") != "note")
    elif action == "undo":
        new_edits, _ = undo_last_batch(edits)
    elif action == "reset":
        new_edits = []
    elif action == "repen" or action == "refresh":
        new_edits = edits
    elif action == "op":
        op = make_atomic_ops(
            p["op"],
            ids=p.get("ids"),
            line=p.get("line"),
            page=p.get("page"),
            dx=float(p.get("dx", 0.0)),
            dy=float(p.get("dy", 0.0)),
            k=float(p.get("k", 1.0)),
            about=p.get("about", "anchor"),
            deg=float(p.get("deg", 0.0)),
            vidx=p.get("vidx"),
            color=p.get("color"),
            weight=p.get("weight"),
            batch=next_batch_id(edits),
        )
        new_edits = edits + [op]
        added_count = 1
    else:
        raise ValueError(f"unknown scene action: {action}")
    save_edits(sdir, new_edits)
    state_changed = False
    if p.get("pen_type"):
        old_pen = cur.get("pen", "gel")
        new_pen = str(p["pen_type"])
        cur["pen"] = new_pen
        state_changed = True
        if not p.get("ink"):
            if new_pen == "pencil" and cur.get("ink", "black") != "pencil":
                cur["prev_ink"] = cur.get("ink", "black")
                cur["ink"] = "pencil"
            elif old_pen == "pencil" and new_pen != "pencil" and cur.get("ink") == "pencil":
                cur["ink"] = cur.get("prev_ink") or "black"
    if p.get("ink"):
        new_ink = str(p["ink"])
        cur["ink"] = new_ink
        if new_ink != "pencil":
            cur["prev_ink"] = new_ink
        state_changed = True
    if p.get("nib_mm") is not None:
        cur["nib"] = float(p["nib_mm"])
        state_changed = True
    if state_changed:
        st.save_current()
    export_scene(
        sdir,
        out_pdf=cur["pdf"],
        fmt="pdf",
        dpi=300,
        pen_type=cur.get("pen", "gel"),
        seed=cur.get("seed", 42),
        nib_mm=float(cur.get("nib", 0.5)),
        ink_override=cur.get("ink", "black"),
    )
    snap = _snapshot(st)
    snap["added"] = added_count
    return snap


def api_scene_export(st: StudioState, p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    fmt = p.get("format", "pdf")
    transparent = bool(p.get("transparent", fmt != "pdf"))
    eid = st.next_id()
    pen_t = cur.get("pen", "gel")
    nib_v = float(cur.get("nib", 0.5))
    ink_v = cur.get("ink", "black")
    if fmt == "pdf":
        target = cur["dir"] / f"export_{eid}.pdf"
        res = export_scene(
            cur["scene"], out_pdf=target, fmt="pdf", dpi=300, pen_type=pen_t,
            transparent=transparent, seed=cur.get("seed", 42), nib_mm=nib_v, ink_override=ink_v,
        )
    else:
        target_dir = cur["dir"] / f"export_{eid}_{fmt}"
        res = export_scene(
            cur["scene"], out_dir=target_dir, fmt=fmt, dpi=int(p.get("dpi", 300)),
            transparent=transparent, pen_type=(pen_t if fmt == "png" else "gel"),
            seed=cur.get("seed", 42), nib_mm=(nib_v if fmt == "png" else 0.5), ink_override=ink_v,
        )
    return {"ok": True, "format": fmt, "files": [st.url_for(Path(f)) for f in res.get("outputs", [])]}


def api_scene_editor(st: StudioState, _p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    if cur.get("editor_url"):
        return {"ok": True, "url": cur["editor_url"]}
    from ..scene.editor_server import start_editor_server

    server, port, url = start_editor_server(cur["scene"], port=0, pen_getter=lambda c=cur: c.get("pen", "gel"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    st.editor_servers.append(server)
    cur["editor_url"] = url
    return {"ok": True, "url": url}


def api_layout_detect(st: StudioState, p: dict) -> dict:
    ldir = st.workdir / f"layout_{st.next_id()}"
    ldir.mkdir(parents=True, exist_ok=True)
    blank = ldir / "blank.pdf"
    blank.write_bytes(_b64_bytes(p["pdf_b64"]))
    layout = detect_pdf_layout(blank, min_slot_h_mm=float(p.get("min_slot_mm", 15.0)))
    return {"ok": True, "layout": layout, "previews": _pdf_vector_previews(st, blank, "lay", max_pages=10)}


def api_template(st: StudioState, p: dict) -> dict:
    tier = p.get("tier") or "tier1"
    out = st.workdir / f"template_{st.next_id()}_{tier}.pdf"
    chars = p.get("chars") or None
    radicals = p.get("radicals") or None
    char_copies = p.get("char_copies") or None
    if tier == "targeted" and not chars and not radicals and p.get("markdown"):
        md = out.with_suffix(".md")
        md.write_text(p["markdown"], encoding="utf-8")
        res = generate_template_pdf(
            out, tier=tier, from_md=md, pack=load_pack(p.get("pack") or None),
            char_copies=char_copies, radicals=radicals,
        )
    else:
        res = generate_template_pdf(out, tier=tier, chars=chars, char_copies=char_copies, radicals=radicals)
    return {"ok": True, "result": res, "pdf_url": st.url_for(out), "previews": _pdf_vector_previews(st, out, f"tpl_{tier}", max_pages=8)}


def api_harvest(st: StudioState, p: dict) -> dict:
    hdir = st.workdir / f"harvest_{st.next_id()}"
    hdir.mkdir(parents=True, exist_ok=True)
    filled = hdir / "filled.pdf"
    filled.write_bytes(_b64_bytes(p["pdf_b64"]))
    merge = (p.get("merge_into") or "").strip() or None
    merge_pack = load_pack(merge) if merge else None
    merge_path = merge_pack.source_path if merge_pack else None
    raw_id = (p.get("id") or "").strip()
    if not raw_id and merge_pack and _is_local_pack(merge_pack.source_path):
        raw_id = merge_pack.id
    pid = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id or "my_hand") or "my_hand"
    out = LOCAL_PACKS_DIR / f"{pid}.hwpack"
    res = harvest_template_pdf(filled, out, pack_id=pid, merge_into=merge_path)
    return {"ok": True, "result": res}


def api_fontbuild(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    fmt = (p.get("format") or "ttf").lower()
    fdir = st.workdir / f"font_{st.next_id()}"
    out = fdir / f"InkTwin-{pk.id}.{fmt}"
    aug = p.get("augment_variants")
    augment = True if aug is None else bool(aug)
    res = build_opentype_font(pk, out, fmt=fmt, family_name=p.get("name") or None, augment_variants=augment)
    return {"ok": True, "result": res, "font_url": st.url_for(out)}


def _is_local_pack(path: Path) -> bool:
    try:
        path.resolve().relative_to(LOCAL_PACKS_DIR.resolve())
        return True
    except ValueError:
        return False


def _pack_char_group(ch: str, cat: str) -> str:
    if cat == "cjk":
        return "cjk"
    if len(ch) == 1 and (ch.isdigit() or ("a" <= ch.lower() <= "z")):
        return "alnum"
    if ch.startswith("__") or cat in ("frac_bar", "accent_bar", "accent_hat", "delim"):
        return "struct"
    return "math"


def _pack_char_sort_key(ch: str, cat: str) -> Tuple[int, str]:
    if len(ch) == 1 and ch.isdigit():
        return (0, ch)
    if len(ch) == 1 and "a" <= ch <= "z":
        return (1, ch)
    if len(ch) == 1 and "A" <= ch <= "Z":
        return (2, ch)
    if cat != "cjk" and not ch.startswith("__"):
        return (3, ch)
    if ch.startswith("__"):
        return (4, ch)
    return (5, ch)


def api_pack_inspect(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    editable = _is_local_pack(pk.source_path) and pk.source_path.is_file()
    med_t = pack_median_thickness(pk.glyphs)

    items: List[dict] = []
    single_variant_chars = 0
    flagged_variants = 0
    flagged_chars = 0
    low_variant_non_cjk: List[str] = []

    for ch in sorted(pk.glyphs.keys(), key=lambda c: _pack_char_sort_key(c, pk.glyphs[c].get("cat", ""))):
        entry = pk.glyphs[ch]
        cat = entry.get("cat", "cap")
        group = _pack_char_group(ch, cat)
        v_out, char_has_flag = inspect_glyph_variants(ch, entry, med_t)
        flagged_variants += sum(1 for v in v_out if v["flags"])
        if char_has_flag:
            flagged_chars += 1
        if len(v_out) == 1:
            single_variant_chars += 1
            if group != "cjk" and not ch.startswith("__"):
                low_variant_non_cjk.append(ch)
        items.append({"char": ch, "cat": cat, "group": group, "variants": v_out, "flagged": char_has_flag})

    missing_radicals = [r for r in _CORE_RADICALS if r not in pk.radicals]
    strokes_counts = {k: len(pk.strokes.get(k, [])) for k in ("H", "V", "P", "N", "D")}
    has_backup = editable and pack_backup_path(pk.id).is_file()
    return {
        "ok": True,
        "pack": {
            "id": pk.id,
            "scope": "local" if editable else "public",
            "editable": editable,
            "has_backup": has_backup,
            "path": str(pk.source_path),
            "total_chars": len(pk.glyphs),
            "total_variants": pk.variant_count,
            "cjk_chars": pk.real_cjk_count,
            "single_variant_chars": single_variant_chars,
            "flagged_variants": flagged_variants,
            "flagged_chars": flagged_chars,
            "median_t": round(med_t, 2),
            "radicals_count": len(pk.radicals),
            "missing_radicals": missing_radicals,
            "strokes": strokes_counts,
            "low_variant_non_cjk": low_variant_non_cjk,
        },
        "items": items,
    }


def api_pack_delete_variant(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    if not (_is_local_pack(pk.source_path) and pk.source_path.is_file()):
        raise PermissionError("公开示例字库为只读，无法直接删除；请先点击「克隆到本地」再编辑。")
    ch = p.get("char")
    if not ch or ch not in pk.glyphs:
        raise ValueError(f"Character not found in pack: {ch!r}")
    variants = pk.glyphs[ch].get("v", [])
    vidx = int(p.get("vidx", -1))
    if vidx < 0 or vidx >= len(variants):
        raise ValueError(f"Variant index out of range: {vidx} (len={len(variants)})")

    variants.pop(vidx)
    remaining = len(variants)
    if remaining == 0:
        del pk.glyphs[ch]

    manifest = dict(pk.manifest)
    manifest["total_chars"] = len(pk.glyphs)
    manifest["total_variants"] = pk.variant_count
    manifest["facets"] = compute_facets(pk.glyphs, pk.style_model)
    manifest["stats"] = {
        "total_chars": len(pk.glyphs),
        "real_cjk_chars": pk.real_cjk_count,
        "variants": pk.variant_count,
        "radicals": len(pk.radicals),
        "strokes": sum(len(v) for v in pk.strokes.values()),
    }
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

    tmp_path = pk.source_path.with_suffix(".hwpack.tmp")
    save_pack_zip(tmp_path, manifest, pk.glyphs, pk.style_model, pk.radicals, pk.strokes)
    tmp_path.replace(pk.source_path)
    return {
        "ok": True,
        "pack": pk.id,
        "deleted": {"char": ch, "vidx": vidx, "remaining": remaining},
        "total_chars": len(pk.glyphs),
        "total_variants": pk.variant_count,
    }


def api_pack_clone_local(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    raw_id = (p.get("new_id") or f"{pk.id}_local").strip()
    new_id = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id) or f"{pk.id}_local"
    out_path = (LOCAL_PACKS_DIR / f"{new_id}.hwpack").resolve()
    if out_path.exists():
        raise FileExistsError(f"本地字库 {new_id}.hwpack 已存在，请换一个名称。")
    manifest = dict(pk.manifest)
    manifest["id"] = new_id
    manifest["privacy"] = "local"
    manifest["cloned_from"] = pk.id
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_pack_zip(out_path, manifest, pk.glyphs, pk.style_model, pk.radicals, pk.strokes)
    return {"ok": True, "id": new_id, "path": str(out_path)}


def _refresh_and_save_local_pack(pk) -> None:
    manifest = dict(pk.manifest)
    manifest["total_chars"] = len(pk.glyphs)
    manifest["total_variants"] = pk.variant_count
    manifest["facets"] = compute_facets(pk.glyphs, pk.style_model)
    manifest["stats"] = {
        "total_chars": len(pk.glyphs),
        "real_cjk_chars": pk.real_cjk_count,
        "variants": pk.variant_count,
        "radicals": len(pk.radicals),
        "strokes": sum(len(v) for v in pk.strokes.values()),
    }
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp_path = pk.source_path.with_suffix(".hwpack.tmp")
    save_pack_zip(tmp_path, manifest, pk.glyphs, pk.style_model, pk.radicals, pk.strokes)
    tmp_path.replace(pk.source_path)


def api_pack_cleanup_plan(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    editable = _is_local_pack(pk.source_path) and pk.source_path.is_file()
    plan = build_cleanup_plan(pk)
    plan["ok"] = True
    plan["editable"] = editable
    plan["missing_radicals"] = [r for r in _CORE_RADICALS if r not in pk.radicals]
    return plan


def api_pack_cleanup_apply(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    if not (_is_local_pack(pk.source_path) and pk.source_path.is_file()):
        raise PermissionError("公开示例字库为只读，请先点击「克隆到本地以编辑」后再执行批量清理。")

    plan = build_cleanup_plan(pk)
    req_items = p.get("items")
    to_delete = req_items if isinstance(req_items, list) else plan["deletions"]
    if not to_delete and not p.get("force_sheet"):
        raise ValueError("没有需要清理的异常变体。")

    # Snapshot backup before applying destructive changes so user can undo cleanly
    bak_path = pack_backup_path(pk.id)
    bak_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pk.source_path, bak_path)

    del_by_char: Dict[str, List[int]] = {}
    for it in to_delete:
        ch = it.get("char")
        if ch and ch in pk.glyphs:
            del_by_char.setdefault(ch, []).append(int(it["vidx"]))

    deleted_count = 0
    emptied_chars: List[str] = []
    affected_single: List[str] = []
    for ch, idxs in del_by_char.items():
        vs = pk.glyphs[ch].get("v", [])
        for vi in sorted(set(idxs), reverse=True):
            if 0 <= vi < len(vs):
                vs.pop(vi)
                deleted_count += 1
        if not vs:
            del pk.glyphs[ch]
            emptied_chars.append(ch)
        elif len(vs) == 1:
            affected_single.append(ch)

    _refresh_and_save_local_pack(pk)

    sheet_info = None
    if bool(p.get("generate_sheet", True)):
        p3 = list(dict.fromkeys(emptied_chars + [k["char"] for k in plan.get("kept_fallbacks", []) if k["char"] in pk.glyphs or k["char"] in emptied_chars]))
        p2 = [ch for ch in dict.fromkeys(affected_single) if ch not in p3]
        chars_ordered = "".join(p3 + p2)
        char_copies = {ch: 3 for ch in p3}
        for ch in p2:
            char_copies[ch] = 2
        rads = ([r for r in _CORE_RADICALS if r not in pk.radicals] if bool(p.get("include_missing_radicals", False)) else None)
        if chars_ordered or rads:
            out = st.workdir / f"refill_{st.next_id()}.pdf"
            res = generate_template_pdf(out, tier="targeted", chars=(chars_ordered or None), char_copies=char_copies, radicals=rads)
            sheet_info = {
                "result": res,
                "pdf_url": st.url_for(out),
                "previews": _pdf_vector_previews(st, out, "refill", max_pages=8),
                "priority3_count": len(p3),
                "priority2_count": len(p2),
                "radicals_count": len(rads or []),
            }

    return {
        "ok": True,
        "pack": pk.id,
        "deleted_count": deleted_count,
        "kept_fallbacks": len(plan.get("kept_fallbacks", [])),
        "has_backup": bak_path.is_file(),
        "total_chars": len(pk.glyphs),
        "total_variants": pk.variant_count,
        "sheet": sheet_info,
    }


def api_pack_restore_backup(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    if not (_is_local_pack(pk.source_path) and pk.source_path.is_file()):
        raise PermissionError("只能还原本地字库备份。")
    bak = pack_backup_path(pk.id)
    if not bak.is_file():
        raise FileNotFoundError("未找到可还原的清理备份。")
    tmp_path = pk.source_path.with_suffix(".hwpack.tmp")
    shutil.copy2(bak, tmp_path)
    tmp_path.replace(pk.source_path)
    bak.unlink(missing_ok=True)
    restored = load_pack(pk.id)
    return {
        "ok": True,
        "pack": restored.id,
        "total_chars": len(restored.glyphs),
        "total_variants": restored.variant_count,
    }


ROUTES_POST: Dict[str, Callable[[StudioState, dict], dict]] = {
    "/api/preflight": api_preflight,
    "/api/render": api_render,
    "/api/scene/ops": api_scene_ops,
    "/api/scene/export": api_scene_export,
    "/api/scene/editor": api_scene_editor,
    "/api/layout-detect": api_layout_detect,
    "/api/template": api_template,
    "/api/harvest": api_harvest,
    "/api/fontbuild": api_fontbuild,
    "/api/pack/inspect": api_pack_inspect,
    "/api/pack/delete-variant": api_pack_delete_variant,
    "/api/pack/clone-local": api_pack_clone_local,
    "/api/pack/cleanup-plan": api_pack_cleanup_plan,
    "/api/pack/cleanup-apply": api_pack_cleanup_apply,
    "/api/pack/restore-backup": api_pack_restore_backup,
}


def _latest_frontend_mtime() -> float:
    pkg_dir = Path(__file__).resolve().parents[1]
    watched = [
        STUDIO_HTML_PATH,
        pkg_dir / "scene" / "scene_editor.html",
        pkg_dir / "renderer" / "js" / "handwrite_dom.js",
        pkg_dir / "renderer" / "css" / "handwrite.css",
    ]
    latest = 0.0
    for fp in watched:
        try:
            if fp.is_file():
                latest = max(latest, fp.stat().st_mtime)
        except OSError:
            pass
    return round(latest, 3)


def start_studio_server(
    workdir: Optional[Path] = None,
    port: int = 0,
    dev_reload: bool = False,
) -> Tuple[ThreadingHTTPServer, int, str, StudioState]:
    """Start InkTwin Studio on 127.0.0.1 and return (server, port, url, state)."""
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="inktwin_studio_"))
    st = StudioState(wd, dev_reload=dev_reload)

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status: int, data: bytes, ctype: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(hk, hv)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj: dict, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            clean = self.path.split("?", 1)[0]
            if clean in ("/", "/index.html"):
                self._send(200, STUDIO_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                return
            if clean == "/api/dev/version":
                self._json(
                    {
                        "ok": True,
                        "dev_reload": st.dev_reload,
                        "boot_id": st.boot_id,
                        "asset_mtime": _latest_frontend_mtime(),
                    }
                )
                return
            if clean == "/api/state":
                with st.lock:
                    try:
                        self._json(api_state(st, {}))
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                return
            if clean == "/api/scene/sync":
                force = "force=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
                with st.lock:
                    try:
                        cur = st.current
                        if not cur:
                            self._json({"ok": True, "changed": False})
                            return
                        sig = _edits_sig(cur["scene"])
                        if not force and sig == cur.get("edits_sig"):
                            self._json({"ok": True, "changed": False})
                            return
                        if sig != cur.get("edits_sig"):
                            export_scene(cur["scene"], out_pdf=cur["pdf"], fmt="pdf", dpi=300, pen_type=cur["pen"], seed=cur.get("seed", 42))
                        snap = _snapshot(st)
                        snap["changed"] = True
                        self._json(snap)
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                return
            if clean == "/api/template/download":
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(self.path).query)
                tier = (qs.get("tier") or ["tier1"])[0].lower()
                if tier not in ("tier1", "tier2"):
                    self._json({"ok": False, "error": f"invalid static tier: {tier}"}, 400)
                    return
                fname = "InkTwin-Tier1.pdf" if tier == "tier1" else "InkTwin-Tier2.pdf"
                static_pdf = TEMPLATES_DIR / fname
                with st.lock:
                    try:
                        if static_pdf.is_file():
                            pdf_bytes = static_pdf.read_bytes()
                        else:
                            tmp_pdf = st.workdir / fname
                            if not tmp_pdf.is_file():
                                generate_template_pdf(tmp_pdf, tier=tier)
                            pdf_bytes = tmp_pdf.read_bytes()
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                        return
                self._send(
                    200,
                    pdf_bytes,
                    "application/pdf",
                    extra_headers={"Content-Disposition": f'attachment; filename="{fname}"'},
                )
                return
            if clean == "/api/template/preview":
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(self.path).query)
                tier = (qs.get("tier") or ["tier1"])[0].lower()
                if tier not in ("tier1", "tier2"):
                    self._json({"ok": False, "error": f"invalid static tier: {tier}"}, 400)
                    return
                fname = "InkTwin-Tier1.pdf" if tier == "tier1" else "InkTwin-Tier2.pdf"
                static_pdf = TEMPLATES_DIR / fname
                with st.lock:
                    try:
                        if static_pdf.is_file():
                            src_pdf = static_pdf
                        else:
                            src_pdf = st.workdir / fname
                            if not src_pdf.is_file():
                                generate_template_pdf(src_pdf, tier=tier)
                        previews = _pdf_vector_previews(st, src_pdf, f"static_{tier}", max_pages=5)
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                        return
                self._json(
                    {
                        "ok": True,
                        "tier": tier,
                        "filename": fname,
                        "download_url": f"/api/template/download?tier={tier}",
                        "pages": len(previews),
                        "previews": previews,
                    }
                )
                return
            if clean.startswith("/files/"):
                rel = clean[len("/files/"):]
                from urllib.parse import unquote

                target = (st.workdir / unquote(rel)).resolve()
                try:
                    target.relative_to(st.workdir)
                except ValueError:
                    self._json({"ok": False, "error": "forbidden"}, 403)
                    return
                if not target.is_file():
                    self._json({"ok": False, "error": "not found"}, 404)
                    return
                self._send(200, target.read_bytes(), _MIME.get(target.suffix.lower(), "application/octet-stream"))
                return
            self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            clean = self.path.split("?", 1)[0]
            if clean == "/api/shutdown":
                self._json({"ok": True, "message": "shutting down"})
                threading.Timer(0.15, server.shutdown).start()
                return
            fn = ROUTES_POST.get(clean)
            if fn is None:
                self._json({"ok": False, "error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            with st.lock:
                try:
                    self._json(fn(st, payload))
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    actual = server.server_address[1]
    return server, actual, f"http://127.0.0.1:{actual}/", st


def _check_existing_studio(port: int) -> bool:
    if not port:
        return False
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.2) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return bool(data.get("ok") and "packs" in data)
    except Exception:
        return False
    return False


def _snapshot_py_mtimes(src_root: Path) -> Dict[str, int]:
    snap: Dict[str, int] = {}
    for p in src_root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            snap[str(p)] = p.stat().st_mtime_ns
        except OSError:
            pass
    return snap


def _shutdown_existing(port: int) -> None:
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=1.5):
            pass
    except Exception:
        pass


def _wait_port_free(port: int, timeout: float = 4.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.15)
    return False


def _serve_studio_reloader(workdir: Optional[Path], port: int, open_browser: bool) -> None:
    src_root = Path(__file__).resolve().parents[1]
    stable_wd = Path(workdir).resolve() if workdir else (Path(tempfile.gettempdir()) / "inktwin_studio_dev")
    stable_wd.mkdir(parents=True, exist_ok=True)
    target_port = port or 8765

    if _check_existing_studio(target_port):
        print(f"[DevReload] Stopping existing Studio on 127.0.0.1:{target_port}...", flush=True)
        _shutdown_existing(target_port)
        _wait_port_free(target_port, timeout=4.0)

    child_env = dict(os.environ)
    child_env["INKTWIN_RELOAD_CHILD"] = "1"
    child_env["INKTWIN_DEV_RELOAD"] = "1"

    def _spawn():
        cmd = [
            sys.executable, "-m", "inktwin", "studio",
            "--port", str(target_port),
            "--workdir", str(stable_wd),
            "--no-browser",
        ]
        return subprocess.Popen(cmd, env=child_env)

    mtimes = _snapshot_py_mtimes(src_root)
    proc = _spawn()
    url = f"http://127.0.0.1:{target_port}/"
    print(f"[DevReload] Watching {src_root} (workdir={stable_wd})", flush=True)
    print(f"[DevReload] Studio live at {url} - edit any .py / .html / .css / .js and save to auto-reload!", flush=True)

    if open_browser and os.environ.get("INKTWIN_NO_BROWSER") != "1":
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if _check_existing_studio(target_port):
                webbrowser.open(url)
                break
            time.sleep(0.2)

    try:
        while True:
            time.sleep(0.6)
            rc = proc.poll()
            if rc is not None:
                if rc == 0:
                    print("[DevReload] Studio shut down cleanly.", flush=True)
                    break
                print(f"[DevReload] Backend exited (code={rc}). Fix error and save any .py file to restart...", flush=True)
                while proc.poll() is not None:
                    time.sleep(0.6)
                    cur_mtimes = _snapshot_py_mtimes(src_root)
                    if cur_mtimes != mtimes:
                        mtimes = cur_mtimes
                        print("[DevReload] Source edited, restarting backend...", flush=True)
                        proc = _spawn()
                        break
                continue

            cur_mtimes = _snapshot_py_mtimes(src_root)
            if cur_mtimes != mtimes:
                changed = [
                    Path(k).name
                    for k in set(cur_mtimes) | set(mtimes)
                    if cur_mtimes.get(k) != mtimes.get(k)
                ]
                mtimes = cur_mtimes
                print(f"[DevReload] Detected change in {', '.join(changed[:5])}, restarting backend...", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                _wait_port_free(target_port, timeout=3.0)
                proc = _spawn()
    except KeyboardInterrupt:
        print("\n[DevReload] Stopping...", flush=True)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()


def serve_studio(workdir: Optional[Path] = None, port: int = 0, open_browser: bool = True, reload: bool = False) -> None:
    open_browser = bool(open_browser and os.environ.get("INKTWIN_NO_BROWSER") != "1")
    if sys.stdout is None or sys.stderr is None:
        log_path = Path(tempfile.gettempdir()) / "inktwin_studio.log"
        log_f = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_f
        if sys.stderr is None:
            sys.stderr = log_f
    is_reload_child = os.environ.get("INKTWIN_RELOAD_CHILD") == "1"
    if reload and not is_reload_child:
        _serve_studio_reloader(workdir=workdir, port=port, open_browser=open_browser)
        return
    if not is_reload_child and port and _check_existing_studio(port):
        url = f"http://127.0.0.1:{port}/"
        print(json.dumps({"ok": True, "url": url, "port": port, "reused": True}, ensure_ascii=False))
        if open_browser:
            webbrowser.open(url)
        return
    dev_mode = bool(reload or os.environ.get("INKTWIN_DEV_RELOAD") == "1")
    server = None
    if is_reload_child and port:
        for attempt in range(12):
            try:
                server, actual, url, st = start_studio_server(workdir, port, dev_reload=dev_mode)
                break
            except OSError:
                if attempt == 11:
                    raise
                time.sleep(0.25)
    else:
        try:
            server, actual, url, st = start_studio_server(workdir, port, dev_reload=dev_mode)
        except OSError:
            # Fallback to any available ephemeral port if requested port is occupied by another app
            server, actual, url, st = start_studio_server(workdir, 0, dev_reload=dev_mode)
    print(json.dumps({"ok": True, "url": url, "port": actual, "workdir": str(st.workdir)}, ensure_ascii=False))
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for es in st.editor_servers:
            es.shutdown()
        server.shutdown()
        server.server_close()

