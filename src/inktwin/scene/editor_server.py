"""Zero-build offline HTTP backend for InkTwin Scene Editor."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

from .inspector import inspect_scene
from .ops import apply_drift, apply_tighten, make_atomic_ops, next_batch_id, undo_last_batch
from .renderer import export_scene, render_page_svg
from .schema import load_inkscene, resolve_scene_state, save_edits

EDITOR_HTML_PATH = Path(__file__).resolve().parent / "scene_editor.html"


def start_editor_server(scene_path: Path, port: int = 0) -> Tuple[ThreadingHTTPServer, int, str]:
    """Start local 127.0.0.1 editor server and return (server, port, url)."""
    _scene0, _edits0, resolved_dir = load_inkscene(scene_path)
    lock = threading.Lock()
    editor_html_bytes = EDITOR_HTML_PATH.read_bytes()

    class _EditorHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, obj: dict, status: int = 200) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            clean = self.path.split("?", 1)[0]
            if clean in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(editor_html_bytes)))
                self.end_headers()
                self.wfile.write(editor_html_bytes)
                return

            with lock:
                scene, edits, sdir = load_inkscene(resolved_dir)
                if clean == "/api/state":
                    insp = inspect_scene(scene, edits)
                    paper = scene.get("paper") or {}
                    self._send_json(
                        {
                            "ok": True,
                            "pack_id": scene.get("pack_id", ""),
                            "paper_id": paper.get("id", "white"),
                            "paper_width_mm": float(paper.get("width_mm") or 210.0),
                            "paper_height_mm": float(paper.get("height_mm") or 297.0),
                            "pages": insp["pages"],
                            "edits_count": len(edits),
                            "inspect": insp,
                        }
                    )
                    return

                if clean.startswith("/api/page_svg/"):
                    try:
                        pidx = int(clean.split("/")[-1])
                    except ValueError:
                        self._send_json({"ok": False, "error": "invalid page index"}, 400)
                        return
                    resolved = resolve_scene_state(scene, edits)
                    svg_str = render_page_svg(pidx, resolved, transparent=False)
                    self._send_json({"ok": True, "page": pidx, "svg": svg_str})
                    return

                if clean == "/api/download":
                    from urllib.parse import parse_qs, unquote, urlparse

                    qs = parse_qs(urlparse(self.path).query)
                    rel = unquote((qs.get("file") or [""])[0])
                    target = (resolved_dir / rel).resolve()
                    try:
                        target.relative_to(resolved_dir.resolve())
                    except ValueError:
                        self._send_json({"ok": False, "error": "forbidden"}, 403)
                        return
                    if not target.is_file():
                        self._send_json({"ok": False, "error": "not found"}, 404)
                        return
                    ctype = {
                        ".pdf": "application/pdf",
                        ".png": "image/png",
                        ".svg": "image/svg+xml",
                    }.get(target.suffix.lower(), "application/octet-stream")
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                    self.end_headers()
                    self.wfile.write(data)
                    return

            self._send_json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            clean = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}

            with lock:
                scene, edits, sdir = load_inkscene(resolved_dir)
                try:
                    if clean == "/api/ops":
                        action = payload.get("action")
                        if action == "tighten":
                            new_edits, added = apply_tighten(scene, edits)
                        elif action == "drift":
                            new_edits, added = apply_drift(scene, edits, force=bool(payload.get("force", False)))
                        else:
                            batch = next_batch_id(edits)
                            op = make_atomic_ops(
                                payload["op"],
                                ids=payload.get("ids"),
                                line=payload.get("line"),
                                page=payload.get("page"),
                                dx=float(payload.get("dx", 0.0)),
                                dy=float(payload.get("dy", 0.0)),
                                k=float(payload.get("k", 1.0)),
                                deg=float(payload.get("deg", 0.0)),
                                vidx=payload.get("vidx"),
                                color=payload.get("color"),
                                weight=payload.get("weight"),
                                batch=batch,
                            )
                            new_edits, added = edits + [op], [op]
                        save_edits(sdir, new_edits)
                        self._send_json({"ok": True, "added": len(added), "edits_count": len(new_edits)})
                        return

                    if clean == "/api/undo":
                        new_edits, removed = undo_last_batch(edits)
                        save_edits(sdir, new_edits)
                        self._send_json({"ok": True, "removed": removed, "edits_count": len(new_edits)})
                        return

                    if clean == "/api/reset":
                        save_edits(sdir, [])
                        self._send_json({"ok": True, "edits_count": 0})
                        return

                    if clean == "/api/export":
                        from urllib.parse import quote

                        fmt = payload.get("format", "pdf")
                        transp = bool(payload.get("transparent", False))
                        res = export_scene(sdir, fmt=fmt, transparent=transp)
                        urls = []
                        for out_f in res.get("outputs", []):
                            try:
                                rel = Path(out_f).resolve().relative_to(resolved_dir.resolve()).as_posix()
                                urls.append("/api/download?file=" + quote(rel))
                            except ValueError:
                                pass
                        res["download_urls"] = urls
                        self._send_json(res)
                        return
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                    return

            self._send_json({"ok": False, "error": "not found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", port), _EditorHandler)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    return server, actual_port, url


def serve_editor(scene_path: Path, port: int = 0, open_browser: bool = True) -> None:
    """Run blocking local InkTwin Scene Editor HTTP server until Ctrl+C."""
    server, actual_port, url = start_editor_server(scene_path, port=port)
    print(json.dumps({"ok": True, "url": url, "port": actual_port, "scene": str(scene_path)}, ensure_ascii=False))
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()

