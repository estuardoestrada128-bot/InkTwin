"""InkTwin — Offline Personal Handwriting Twin & Math/Physics Vector Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

__version__ = "0.1.0"


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Locate the InkTwin repository root containing assets/ and packs/."""
    cur = (start or Path(__file__).resolve()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "assets").is_dir() and (p / "pyproject.toml").exists():
            return p
    return Path(__file__).resolve().parent.parent.parent


REPO_ROOT = find_repo_root()
ASSETS_DIR = REPO_ROOT / "assets"
DATA_DIR = ASSETS_DIR / "data"
FONTS_DIR = ASSETS_DIR / "fonts"
KATEX_DIR = ASSETS_DIR / "katex"
LAYOUTS_DIR = ASSETS_DIR / "layouts"
TEMPLATES_DIR = ASSETS_DIR / "templates"
PACKS_DIR = REPO_ROOT / "packs"
LOCAL_PACKS_DIR = PACKS_DIR / "local"
MEDIANS_GZ_PATH = DATA_DIR / "hanzi_medians.json.gz"

