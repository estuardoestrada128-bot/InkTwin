"""Studio runtime state, logging, preview cache, and disk LRU cleanup."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import shutil
import threading
import uuid
from http.server import ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import fitz

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
    ".zip": "application/zip",
}


def _setup_logger(workdir: Path) -> logging.Logger:
    logger = logging.getLogger(f"inktwin.studio.{abs(hash(str(workdir)))}")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        try:
            handler = RotatingFileHandler(
                str(workdir / "studio.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True
            )
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(handler)
        except OSError:
            pass
    return logger


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


class StudioState:
    def __init__(self, workdir: Path, dev_reload: bool = False):
        self.workdir = workdir.resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.dev_reload = bool(dev_reload)
        self.boot_id = uuid.uuid4().hex[:10]
        self.counter = 0
        self.current: Optional[dict] = None
        self.state_lock = threading.RLock()
        self.render_lock = threading.Lock()
        self.lock = self.state_lock  # Legacy alias preserved for direct callers/tests
        self.editor_servers: List[ThreadingHTTPServer] = []
        self._preview_cache: Dict[Tuple[str, int, int], Tuple[int, List[str]]] = {}
        self.logger = _setup_logger(self.workdir)
        self._load_current()

    def close(self) -> None:
        """Close logger file handlers so temporary workdirs can be deleted cleanly on Windows."""
        for h in list(self.logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            self.logger.removeHandler(h)

    def next_id(self) -> str:
        with self.state_lock:
            self.counter += 1
            return f"{self.counter:03d}"

    def url_for(self, p: Path) -> str:
        rel = Path(p).resolve().relative_to(self.workdir)
        return "/files/" + "/".join(quote(part) for part in rel.parts)

    def save_current(self) -> None:
        with self.state_lock:
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
            tmp_meta = self.workdir / "current.json.tmp"
            tmp_meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_meta.replace(meta_path)

    def cleanup_old_runs(self, keep_runs: int = 8, keep_preview_files: int = 120) -> None:
        """Prune stale run_* folders and preview SVGs while keeping active run and recent artifacts."""
        active_dir = Path(self.current["dir"]).resolve() if self.current and self.current.get("dir") else None
        runs = sorted(
            (d for d in self.workdir.glob("run_*") if d.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_dir in runs[keep_runs:]:
            if active_dir is not None and old_dir.resolve() == active_dir:
                continue
            shutil.rmtree(old_dir, ignore_errors=True)

        pdir = self.workdir / "previews"
        if pdir.is_dir():
            svgs = sorted(pdir.glob("*.svg"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old_svg in svgs[keep_preview_files:]:
                old_svg.unlink(missing_ok=True)

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


def _pdf_vector_previews_meta(
    st: StudioState, pdf: Path, prefix: str, max_pages: int = 12
) -> Tuple[int, List[str]]:
    """Export cached crisp SVG previews up to max_pages and return (total_pages, urls)."""
    rpdf = Path(pdf).resolve()
    mtime_ns = rpdf.stat().st_mtime_ns
    key = (str(rpdf), mtime_ns, int(max_pages))
    cached = st._preview_cache.get(key)
    if cached is not None:
        total_p, cached_urls = cached
        pdir = st.workdir / "previews"
        if all((st.workdir / u.lstrip("/files/")).exists() for u in cached_urls):
            return total_p, list(cached_urls)

    pdir = st.workdir / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    tag = st.next_id()
    urls: List[str] = []
    with fitz.open(str(rpdf)) as doc:
        total_p = len(doc)
        for i in range(min(total_p, max_pages)):
            svg_txt = doc[i].get_svg_image(text_as_path=True)
            fp = pdir / f"{prefix}_{tag}_p{i + 1}.svg"
            fp.write_text(svg_txt, encoding="utf-8")
            urls.append(st.url_for(fp))
    if len(st._preview_cache) > 32:
        st._preview_cache.clear()
    st._preview_cache[key] = (total_p, list(urls))
    return total_p, urls


def _pdf_vector_previews(st: StudioState, pdf: Path, prefix: str, max_pages: int = 12) -> List[str]:
    return _pdf_vector_previews_meta(st, pdf, prefix, max_pages=max_pages)[1]
