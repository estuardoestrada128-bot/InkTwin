"""In-memory local HTTP server & headless Chrome/Edge PDF exporter."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .. import ASSETS_DIR
from ..pack import HwPack
from ..preflight import find_browser

RENDERER_DIR = Path(__file__).resolve().parent


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def export_html_to_pdf(
    html_content: str,
    out_pdf: Path,
    pack: Optional[HwPack] = None,
    keep_html_path: Optional[Path] = None,
) -> Optional[dict]:
    """Serve static assets + in-memory pack glyphs to headless Chrome/Edge and print PDF."""
    browser = find_browser()
    if not browser:
        raise RuntimeError("No Chrome or Microsoft Edge executable found for PDF export.")

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    glyphs_bytes = (
        json.dumps(pack.glyphs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if pack is not None
        else b"{}"
    )
    captured_scene: dict = {"data": None}

    with tempfile.TemporaryDirectory(prefix="inktwin_render_") as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "index.html").write_text(html_content, encoding="utf-8")
        if keep_html_path is not None:
            keep_html_path.write_text(html_content, encoding="utf-8")

        class _InkTwinHandler(_QuietHandler):
            def do_GET(self):
                clean = self.path.split("?", 1)[0].split("#", 1)[0]
                if clean == "/pack_glyphs.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(glyphs_bytes)))
                    self.end_headers()
                    self.wfile.write(glyphs_bytes)
                    return
                super().do_GET()

            def do_POST(self):
                clean = self.path.split("?", 1)[0].split("#", 1)[0]
                if clean == "/__scene_upload__":
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    body = self.rfile.read(length) if length > 0 else b"{}"
                    try:
                        captured_scene["data"] = json.loads(body.decode("utf-8"))
                    except Exception:
                        captured_scene["data"] = None
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "11")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                    return
                self.send_response(404)
                self.end_headers()

            def translate_path(self, path: str) -> str:
                clean = path.split("?", 1)[0].split("#", 1)[0]
                if clean.startswith("/assets/"):
                    rel = urllib.request.url2pathname(clean[len("/assets/"):])
                    return str(ASSETS_DIR / rel)
                if clean.startswith("/js/"):
                    rel = urllib.request.url2pathname(clean[len("/js/"):])
                    return str(RENDERER_DIR / "js" / rel)
                if clean.startswith("/css/"):
                    rel = urllib.request.url2pathname(clean[len("/css/"):])
                    return str(RENDERER_DIR / "css" / rel)
                rel = urllib.request.url2pathname(clean.lstrip("/")) or "index.html"
                return str(tmp_path / rel)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _InkTwinHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            url = f"http://127.0.0.1:{port}/index.html"
            cmd = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-extensions",
                "--no-pdf-header-footer",
                "--virtual-time-budget=10000",
                f"--print-to-pdf={str(out_pdf)}",
                url,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0 or not out_pdf.exists() or out_pdf.stat().st_size < 1000:
                raise RuntimeError(
                    f"Headless PDF export failed (code={proc.returncode}):\n{proc.stderr or proc.stdout}"
                )
        finally:
            server.shutdown()
            server.server_close()

    return captured_scene.get("data")

