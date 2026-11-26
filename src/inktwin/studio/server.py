"""InkTwin Studio backend: forge packs, detect worksheet boxes, render, inspect, edit, and export.

Pure Python stdlib HTTP server bound to 127.0.0.1 (100% offline, zero build step).
"""

from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .. import TEMPLATES_DIR, __version__
from ..harvest import generate_template_pdf
from ..overlay import detect_pdf_layout
from ..pack import list_packs
from ..preflight import run_doctor
from ..scene import export_scene, inspect_scene, load_inkscene
from ..scene.ops import apply_drift, apply_tighten, make_atomic_ops, next_batch_id, undo_last_batch
from ..scene.schema import save_edits
from ._http_sec import inject_token_meta, new_session_token, validate_http_request
from .routes_pack import PACK_ROUTES_POST
from .state import (
    _MIME,
    STUDIO_HTML_PATH,
    StudioState,
    _b64_bytes,
    _pdf_vector_previews,
    _pdf_vector_previews_meta,
    _run_cli,
)

_HEAVY_ROUTES = frozenset({
    "/api/render",
    "/api/harvest",
    "/api/fontbuild",
    "/api/scene/export",
    "/api/scene/export-zip",
    "/api/layout-detect",
})


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
    with st.state_lock:
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
        total_pages, previews = _pdf_vector_previews_meta(st, cur["pdf"], f"run{cur['run']}")
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
            "total_pages": total_pages,
            "previews": previews,
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
    with st.state_lock:
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
        "--nib-mm", str(nib),
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
    with st.state_lock:
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
        st.cleanup_old_runs()
    return _snapshot(st)


def api_scene_ops(st: StudioState, p: dict) -> dict:
    with st.state_lock:
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
        elif action in ("repen", "refresh"):
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
        pen_t = cur.get("pen", "gel")
        nib_v = float(cur.get("nib", 0.5))
        ink_v = cur.get("ink", "black")
        seed_v = cur.get("seed", 42)
        pdf_out = cur["pdf"]

    export_scene(
        sdir,
        out_pdf=pdf_out,
        fmt="pdf",
        dpi=300,
        pen_type=pen_t,
        seed=seed_v,
        nib_mm=nib_v,
        ink_override=ink_v,
    )
    snap = _snapshot(st)
    snap["added"] = added_count
    return snap


def api_scene_export(st: StudioState, p: dict) -> dict:
    with st.state_lock:
        cur = st.current
        if not cur:
            raise RuntimeError("No active render.")
        scene_dir = cur["scene"]
        cur_dir = cur["dir"]
        pen_t = cur.get("pen", "gel")
        nib_v = float(cur.get("nib", 0.5))
        ink_v = cur.get("ink", "black")
        seed_v = cur.get("seed", 42)
    fmt = p.get("format", "pdf")
    transparent = bool(p.get("transparent", fmt != "pdf"))
    eid = st.next_id()
    if fmt == "pdf":
        target = cur_dir / f"export_{eid}.pdf"
        res = export_scene(
            scene_dir, out_pdf=target, fmt="pdf", dpi=300, pen_type=pen_t,
            transparent=transparent, seed=seed_v, nib_mm=nib_v, ink_override=ink_v,
        )
    else:
        target_dir = cur_dir / f"export_{eid}_{fmt}"
        res = export_scene(
            scene_dir, out_dir=target_dir, fmt=fmt, dpi=int(p.get("dpi", 300)),
            transparent=transparent, pen_type=(pen_t if fmt == "png" else "gel"),
            seed=seed_v, nib_mm=(nib_v if fmt == "png" else 0.5), ink_override=ink_v,
        )
    return {"ok": True, "format": fmt, "files": [st.url_for(Path(f)) for f in res.get("outputs", [])]}


def api_scene_export_zip(st: StudioState, p: dict) -> dict:
    with st.state_lock:
        cur = st.current
        if not cur:
            raise RuntimeError("No active render.")
        scene_dir = cur["scene"]
        cur_dir = cur["dir"]
        pen_t = cur.get("pen", "gel")
        nib_v = float(cur.get("nib", 0.5))
        ink_v = cur.get("ink", "black")
        seed_v = cur.get("seed", 42)
    fmt = str(p.get("format") or "png").lower()
    if fmt not in ("svg", "png"):
        raise ValueError(f"ZIP export supports svg or png, got: {fmt}")
    transparent = bool(p.get("transparent", True))
    eid = st.next_id()
    target_dir = cur_dir / f"export_{eid}_{fmt}"
    res = export_scene(
        scene_dir, out_dir=target_dir, fmt=fmt, dpi=int(p.get("dpi", 300)),
        transparent=transparent, pen_type=(pen_t if fmt == "png" else "gel"),
        seed=seed_v, nib_mm=(nib_v if fmt == "png" else 0.5), ink_override=ink_v,
    )
    outputs = [Path(f) for f in res.get("outputs", []) if Path(f).is_file()]
    zip_path = cur_dir / f"export_{eid}_{fmt}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in outputs:
            zf.write(fp, arcname=fp.name)
    return {"ok": True, "format": fmt, "count": len(outputs), "zip_url": st.url_for(zip_path)}


def api_scene_editor(st: StudioState, _p: dict) -> dict:
    with st.state_lock:
        cur = st.current
        if not cur:
            raise RuntimeError("No active render.")
        if cur.get("editor_url"):
            return {"ok": True, "url": cur["editor_url"], "run": cur["run"]}
        from ..scene.editor_server import start_editor_server

        server, _port, url = start_editor_server(
            cur["scene"],
            port=0,
            pen_getter=lambda c=cur: c.get("pen", "gel"),
            nib_getter=lambda c=cur: float(c.get("nib", 0.5)),
            ink_getter=lambda c=cur: str(c.get("ink", "black")),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        st.editor_servers.append(server)
        cur["editor_url"] = url
        return {"ok": True, "url": url, "run": cur["run"]}


def api_layout_detect(st: StudioState, p: dict) -> dict:
    ldir = st.workdir / f"layout_{st.next_id()}"
    ldir.mkdir(parents=True, exist_ok=True)
    blank = ldir / "blank.pdf"
    blank.write_bytes(_b64_bytes(p["pdf_b64"]))
    layout = detect_pdf_layout(blank, min_slot_h_mm=float(p.get("min_slot_mm", 15.0)))
    return {"ok": True, "layout": layout, "previews": _pdf_vector_previews(st, blank, "lay", max_pages=10)}


ROUTES_POST: Dict[str, Callable[[StudioState, dict], dict]] = {
    "/api/preflight": api_preflight,
    "/api/render": api_render,
    "/api/scene/ops": api_scene_ops,
    "/api/scene/export": api_scene_export,
    "/api/scene/export-zip": api_scene_export_zip,
    "/api/scene/editor": api_scene_editor,
    "/api/layout-detect": api_layout_detect,
    **PACK_ROUTES_POST,
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
    session_token = new_session_token()

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
            srv_port = self.server.server_address[1]
            if validate_http_request(self, session_token, srv_port, is_post=False) is not None:
                return
            if clean in ("/", "/index.html"):
                self._send(200, inject_token_meta(STUDIO_HTML_PATH.read_bytes(), session_token), "text/html; charset=utf-8")
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
                with st.state_lock:
                    try:
                        self._json(api_state(st, {}))
                    except Exception as exc:
                        st.logger.exception("api_state error")
                        self._json({"ok": False, "error": str(exc)}, 500)
                return
            if clean == "/api/scene/sync":
                force = "force=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
                try:
                    with st.state_lock:
                        cur = st.current
                        if not cur:
                            self._json({"ok": True, "changed": False})
                            return
                        sig = _edits_sig(cur["scene"])
                        if not force and sig == cur.get("edits_sig"):
                            self._json({"ok": True, "changed": False})
                            return
                        need_export = sig != cur.get("edits_sig")
                        scene_dir, pdf_out = cur["scene"], cur["pdf"]
                        pen_t, seed_v = cur["pen"], cur.get("seed", 42)
                        nib_v, ink_v = float(cur.get("nib", 0.5)), cur.get("ink", "black")
                    if need_export:
                        with st.render_lock:
                            export_scene(
                                scene_dir, out_pdf=pdf_out, fmt="pdf", dpi=300,
                                pen_type=pen_t, seed=seed_v, nib_mm=nib_v, ink_override=ink_v,
                            )
                    snap = _snapshot(st)
                    snap["changed"] = True
                    self._json(snap)
                except Exception as exc:
                    st.logger.exception("api_scene_sync error")
                    self._json({"ok": False, "error": str(exc)}, 500)
                return
            if clean in ("/api/template/download", "/api/template/preview"):
                qs = parse_qs(urlparse(self.path).query)
                tier = (qs.get("tier") or ["full"])[0].lower()
                fname_map = {"full": "InkTwin-Standard.pdf", "tier1": "InkTwin-Tier1.pdf", "tier2": "InkTwin-Tier2.pdf"}
                if tier not in fname_map:
                    self._json({"ok": False, "error": f"invalid static tier: {tier}"}, 400)
                    return
                fname = fname_map[tier]
                static_pdf = TEMPLATES_DIR / fname
                try:
                    if static_pdf.is_file():
                        src_pdf = static_pdf
                    else:
                        src_pdf = st.workdir / fname
                        if not src_pdf.is_file():
                            with st.render_lock:
                                if not src_pdf.is_file():
                                    generate_template_pdf(src_pdf, tier=tier)
                    if clean == "/api/template/download":
                        self._send(
                            200,
                            src_pdf.read_bytes(),
                            "application/pdf",
                            extra_headers={"Content-Disposition": f'attachment; filename="{fname}"'},
                        )
                    else:
                        previews = _pdf_vector_previews(st, src_pdf, f"static_{tier}", max_pages=6)
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
                except Exception as exc:
                    st.logger.exception("template GET error")
                    self._json({"ok": False, "error": str(exc)}, 500)
                return
            if clean.startswith("/files/"):
                rel = clean[len("/files/"):]
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
            srv_port = self.server.server_address[1]
            if validate_http_request(self, session_token, srv_port, is_post=True) is not None:
                return
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

            try:
                if clean in _HEAVY_ROUTES:
                    with st.render_lock:
                        res = fn(st, payload)
                else:
                    with st.state_lock:
                        res = fn(st, payload)
                self._json(res)
            except Exception as exc:
                st.logger.exception("POST %s failed", clean)
                self._json({"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]}, 400)

    class _StudioHTTPServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            try:
                super().server_close()
            finally:
                st.close()

    server = _StudioHTTPServer(("127.0.0.1", port), _Handler)
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

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
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
            try:
                es.shutdown()
                es.server_close()
            except Exception:
                pass
        server.shutdown()
        server.server_close()
        st.close()
