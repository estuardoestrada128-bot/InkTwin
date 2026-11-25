"""InkTwin Studio backend: forge packs, detect worksheet boxes, render, inspect, edit, and export.

Pure Python stdlib HTTP server bound to 127.0.0.1 (100% offline, zero build step).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import tempfile
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import fitz

from .. import LOCAL_PACKS_DIR, __version__
from ..fontgen import build_opentype_font
from ..harvest import generate_template_pdf, harvest_template_pdf
from ..overlay import detect_pdf_layout
from ..pack import list_packs, load_pack
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
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = int(cli_main(argv))
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


class StudioState:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.counter = 0
        self.current: Optional[dict] = None
        self.lock = threading.Lock()
        self.editor_servers: List[ThreadingHTTPServer] = []

    def next_id(self) -> str:
        self.counter += 1
        return f"{self.counter:03d}"

    def url_for(self, p: Path) -> str:
        rel = Path(p).resolve().relative_to(self.workdir)
        return "/files/" + rel.as_posix()


# ----------------------------------------------------------------------------- API handlers


def api_state(st: StudioState, _p: dict) -> dict:
    return {
        "ok": True,
        "version": __version__,
        "workdir": str(st.workdir),
        "doctor": run_doctor(),
        "packs": list_packs(),
        "current_run": st.current["run"] if st.current else None,
    }


def api_preflight(st: StudioState, p: dict) -> dict:
    md = st.workdir / f"preflight_{st.next_id()}.md"
    md.write_text(p.get("markdown", ""), encoding="utf-8")
    rc, res, log = _run_cli(["preflight", str(md), "--pack", p.get("pack") or "demo_sample", "--no-docx", "--json"])
    if rc != 0 or res is None:
        raise RuntimeError(f"preflight failed: {log[-800:]}")
    return {"ok": True, "report": res}


def _snapshot(st: StudioState) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render. Render a document first.")
    scene, edits, _ = load_inkscene(cur["scene"])
    insp = inspect_scene(scene, edits)
    return {
        "ok": True,
        "run": cur["run"],
        "render": cur.get("render"),
        "pdf_url": st.url_for(cur["pdf"]),
        "previews": _pdf_previews(cur["pdf"]),
        "edits_count": len(edits),
        "inspect": {
            "pages": insp.get("pages"),
            "glyph_count": insp.get("glyph_count"),
            "line_count": insp.get("line_count"),
            "anomaly_counts": insp.get("anomaly_counts", {}),
            "hints": insp.get("hints", []),
        },
    }


def api_render(st: StudioState, p: dict) -> dict:
    rid = st.next_id()
    rdir = st.workdir / f"run_{rid}"
    rdir.mkdir(parents=True, exist_ok=True)
    md = rdir / "input.md"
    md.write_text(p.get("markdown", ""), encoding="utf-8")
    out_pdf = rdir / "output.pdf"
    sdir = rdir / "scene"
    pen = p.get("pen_type") or "gel"

    argv = [
        "render", str(md), "-o", str(out_pdf),
        "--pack", p.get("pack") or "demo_sample",
        "--save-scene", str(sdir), "--json",
        "--paper", p.get("paper") or "auto",
        "--ink", p.get("ink") or "black",
        "--mood", p.get("mood") or "normal",
        "--pen-type", pen,
        "--page-size", p.get("page_size") or "A4",
        "--font-size", str(float(p.get("font_size") or 12.0)),
    ]
    if p.get("seed") not in (None, ""):
        argv += ["--seed", str(int(p["seed"]))]
    if p.get("tag"):
        argv += ["--tag", str(p["tag"])]

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
    st.current = {"run": rid, "dir": rdir, "scene": sdir, "pdf": out_pdf, "pen": pen, "render": res}
    return _snapshot(st)


def api_scene_ops(st: StudioState, p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    scene, edits, sdir = load_inkscene(cur["scene"])
    action = p.get("action")
    if action == "tighten":
        new_edits, _ = apply_tighten(scene, edits)
    elif action == "drift":
        new_edits, _ = apply_drift(scene, edits, force=bool(p.get("force", True)))
    elif action == "undo":
        new_edits, _ = undo_last_batch(edits)
    elif action == "reset":
        new_edits = []
    elif action == "repen":
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
            deg=float(p.get("deg", 0.0)),
            vidx=p.get("vidx"),
            color=p.get("color"),
            weight=p.get("weight"),
            batch=next_batch_id(edits),
        )
        new_edits = edits + [op]
    else:
        raise ValueError(f"unknown scene action: {action}")
    save_edits(sdir, new_edits)
    if p.get("pen_type"):
        cur["pen"] = p["pen_type"]
    export_scene(sdir, out_pdf=cur["pdf"], fmt="pdf", dpi=300, pen_type=cur["pen"])
    return _snapshot(st)


def api_scene_export(st: StudioState, p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    fmt = p.get("format", "pdf")
    transparent = bool(p.get("transparent", fmt != "pdf"))
    eid = st.next_id()
    if fmt == "pdf":
        target = cur["dir"] / f"export_{eid}.pdf"
        res = export_scene(cur["scene"], out_pdf=target, fmt="pdf", dpi=300, pen_type=cur["pen"], transparent=transparent)
    else:
        target_dir = cur["dir"] / f"export_{eid}_{fmt}"
        res = export_scene(
            cur["scene"], out_dir=target_dir, fmt=fmt, dpi=int(p.get("dpi", 300)),
            transparent=transparent, pen_type=(cur["pen"] if fmt == "png" else "gel"),
        )
    return {"ok": True, "format": fmt, "files": [st.url_for(Path(f)) for f in res.get("outputs", [])]}


def api_scene_editor(st: StudioState, _p: dict) -> dict:
    cur = st.current
    if not cur:
        raise RuntimeError("No active render.")
    from ..scene.editor_server import start_editor_server

    server, port, url = start_editor_server(cur["scene"], port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    st.editor_servers.append(server)
    return {"ok": True, "url": url}


def api_layout_detect(st: StudioState, p: dict) -> dict:
    ldir = st.workdir / f"layout_{st.next_id()}"
    ldir.mkdir(parents=True, exist_ok=True)
    blank = ldir / "blank.pdf"
    blank.write_bytes(_b64_bytes(p["pdf_b64"]))
    layout = detect_pdf_layout(blank, min_slot_h_mm=float(p.get("min_slot_mm", 15.0)))
    return {"ok": True, "layout": layout, "previews": _pdf_previews(blank, dpi=72)}


def api_template(st: StudioState, p: dict) -> dict:
    tier = p.get("tier") or "tier1"
    out = st.workdir / f"template_{st.next_id()}_{tier}.pdf"
    chars = p.get("chars") or None
    if tier == "targeted" and not chars and p.get("markdown"):
        md = out.with_suffix(".md")
        md.write_text(p["markdown"], encoding="utf-8")
        res = generate_template_pdf(out, tier=tier, from_md=md, pack=load_pack(p.get("pack") or None))
    else:
        res = generate_template_pdf(out, tier=tier, chars=chars)
    return {"ok": True, "result": res, "pdf_url": st.url_for(out), "previews": _pdf_previews(out, dpi=60, max_pages=3)}


def api_harvest(st: StudioState, p: dict) -> dict:
    hdir = st.workdir / f"harvest_{st.next_id()}"
    hdir.mkdir(parents=True, exist_ok=True)
    filled = hdir / "filled.pdf"
    filled.write_bytes(_b64_bytes(p["pdf_b64"]))
    pid = re.sub(r"[^A-Za-z0-9_-]", "_", (p.get("id") or "my_hand").strip()) or "my_hand"
    out = LOCAL_PACKS_DIR / f"{pid}.hwpack"
    merge = p.get("merge_into") or None
    merge_path = load_pack(merge).source_path if merge else None
    res = harvest_template_pdf(filled, out, pack_id=pid, merge_into=merge_path)
    return {"ok": True, "result": res}


def api_fontbuild(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    fmt = (p.get("format") or "ttf").lower()
    fdir = st.workdir / f"font_{st.next_id()}"
    out = fdir / f"InkTwin-{pk.id}.{fmt}"
    res = build_opentype_font(pk, out, fmt=fmt, family_name=p.get("name") or None)
    return {"ok": True, "result": res, "font_url": st.url_for(out)}


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
}


def start_studio_server(workdir: Optional[Path] = None, port: int = 0) -> Tuple[ThreadingHTTPServer, int, str, StudioState]:
    """Start InkTwin Studio on 127.0.0.1 and return (server, port, url, state)."""
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="inktwin_studio_"))
    st = StudioState(wd)
    html_bytes = STUDIO_HTML_PATH.read_bytes()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status: int, data: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj: dict, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            clean = self.path.split("?", 1)[0]
            if clean in ("/", "/index.html"):
                self._send(200, html_bytes, "text/html; charset=utf-8")
                return
            if clean == "/api/state":
                with st.lock:
                    try:
                        self._json(api_state(st, {}))
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
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


def serve_studio(workdir: Optional[Path] = None, port: int = 0, open_browser: bool = True) -> None:
    server, actual, url, st = start_studio_server(workdir, port)
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

