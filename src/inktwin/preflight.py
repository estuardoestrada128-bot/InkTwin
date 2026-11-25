"""Environment doctor and zero-side-effect preflight analysis."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import ASSETS_DIR, KATEX_DIR, MEDIANS_GZ_PATH, __version__
from .mathsplit import preprocess_markdown_block
from .overlay import auto_find_docx, parse_docx_worksheet, parse_markdown_answers
from .pack import HwPack, list_packs
from .paper import PaperSpec, usable_width_mm

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> Optional[str]:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for cmd in ("chrome", "msedge", "google-chrome", "chromium"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def run_doctor() -> dict:
    """Inspect dependencies, headless browser, offline KaTeX, medians DB, and installed packs."""
    deps = {}
    for mod in ("fitz", "PIL", "numpy", "yaml", "markdown_it", "fontTools"):
        try:
            __import__(mod)
            deps[mod] = True
        except ImportError:
            deps[mod] = False

    browser = find_browser()
    katex_ok = (KATEX_DIR / "katex.min.css").exists() and (KATEX_DIR / "katex.min.js").exists()
    medians_ok = MEDIANS_GZ_PATH.exists()
    packs = list_packs()

    ok = all(deps.values()) and bool(browser) and katex_ok and medians_ok and len(packs) > 0
    return {
        "ok": ok,
        "version": __version__,
        "python": sys.version.split()[0],
        "deps": deps,
        "browser": browser,
        "assets": {
            "assets_dir": str(ASSETS_DIR),
            "katex_ready": katex_ok,
            "medians_ready": medians_ok,
        },
        "packs": packs,
    }


def run_preflight(
    md_path: Path,
    pack: HwPack,
    paper_spec: PaperSpec,
    font_size_pt: float = 12.0,
    docx_path: Optional[Path] = None,
    no_docx: bool = False,
    style: str = "concise",
    flow: str = "natural",
) -> dict:
    """Analyze character coverage, synthesized unseen CJK, long formula splits, and matched worksheet."""
    md_text = md_path.read_text(encoding="utf-8")
    md_data = parse_markdown_answers(md_text)

    resolved_docx: Optional[Path] = None
    if not no_docx:
        resolved_docx = docx_path if docx_path is not None else auto_find_docx(md_path, md_data["frontmatter"])
    docx_data = parse_docx_worksheet(resolved_docx) if (resolved_docx and resolved_docx.exists()) else None

    avail_mm = usable_width_mm(paper_spec)
    all_splits: List[dict] = []
    if md_data["sections"]:
        for sec in md_data["sections"]:
            _, splits = preprocess_markdown_block(sec["markdown"], avail_mm, font_size_pt, flow=flow, style=style)
            for sp in splits:
                all_splits.append({"question": sec["num"], **sp})
    else:
        _, splits = preprocess_markdown_block(md_data["raw_body"], avail_mm, font_size_pt, flow=flow, style=style)
        all_splits.extend(splits)

    cjk_seen = sorted({ch for ch in md_text if "\u4e00" <= ch <= "\u9fff"})
    real_cjk = [ch for ch in cjk_seen if ch in pack.glyphs and pack.glyphs[ch].get("cat") == "cjk"]
    unseen_cjk = [ch for ch in cjk_seen if ch not in real_cjk]

    non_cjk = sorted({
        ch for ch in md_text
        if not ("\u4e00" <= ch <= "\u9fff") and not ch.isspace() and ch not in ("$", "#", "*", "`", "\\", "_", "^")
    })
    real_sym = [ch for ch in non_cjk if ch in pack.glyphs]

    coverage = round(len(real_cjk) / len(cjk_seen), 3) if cjk_seen else 1.0
    return {
        "input": str(md_path),
        "pack": pack.id,
        "paper": paper_spec.id,
        "usable_width_mm": round(avail_mm, 1),
        "mode": "worksheet" if docx_data is not None else "standalone",
        "worksheet_docx": str(resolved_docx) if resolved_docx else None,
        "worksheet_questions": len(docx_data["questions"]) if docx_data else 0,
        "answer_sections": len(md_data["sections"]),
        "cjk": {
            "total_unique": len(cjk_seen),
            "real_in_pack": len(real_cjk),
            "synthesized_unseen": len(unseen_cjk),
            "unseen_chars": unseen_cjk,
            "real_coverage_ratio": coverage,
        },
        "symbols_matched": len(real_sym),
        "formula_splits": all_splits,
    }
