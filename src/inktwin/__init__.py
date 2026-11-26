"""InkTwin — Offline Personal Handwriting Twin & Math/Physics Vector Engine."""

from __future__ import annotations

import os
import sys
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Optional

__version__ = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Locate the InkTwin repository root when running from a source checkout, else return package parent."""
    cur = (start or Path(__file__).resolve()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").is_file() and (p / "src" / "inktwin").is_dir():
            return p
    return PACKAGE_DIR


def _resolve_user_data_dir() -> Path:
    env_home = os.environ.get("INKTWIN_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return (base / "InkTwin").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "InkTwin").resolve()
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (base / "inktwin").resolve()


def _resolve_assets_dir() -> Path:
    env_a = os.environ.get("INKTWIN_ASSETS_DIR")
    if env_a:
        return Path(env_a).expanduser().resolve()
    try:
        pkg_a = Path(str(_pkg_files("inktwin").joinpath("assets")))
        if pkg_a.is_dir():
            return pkg_a.resolve()
    except Exception:
        pass
    in_pkg = PACKAGE_DIR / "assets"
    if in_pkg.is_dir():
        return in_pkg
    return REPO_ROOT / "assets"


def _resolve_packs_dir() -> Path:
    in_pkg = PACKAGE_DIR / "packs"
    if in_pkg.is_dir():
        return in_pkg
    try:
        pkg_p = Path(str(_pkg_files("inktwin").joinpath("packs")))
        if pkg_p.is_dir():
            return pkg_p.resolve()
    except Exception:
        pass
    return REPO_ROOT / "packs"


def _resolve_local_packs_dir() -> Path:
    env_lp = os.environ.get("INKTWIN_LOCAL_PACKS_DIR")
    if env_lp:
        return Path(env_lp).expanduser().resolve()
    repo_local = REPO_ROOT / "packs" / "local"
    if (REPO_ROOT / "pyproject.toml").is_file() and repo_local.is_dir():
        return repo_local.resolve()
    return (USER_DATA_DIR / "packs").resolve()


REPO_ROOT = find_repo_root()
USER_DATA_DIR = _resolve_user_data_dir()
ASSETS_DIR = _resolve_assets_dir()
DATA_DIR = ASSETS_DIR / "data"
FONTS_DIR = ASSETS_DIR / "fonts"
KATEX_DIR = ASSETS_DIR / "katex"
TEMPLATES_DIR = ASSETS_DIR / "templates"
PACKS_DIR = _resolve_packs_dir()
LOCAL_PACKS_DIR = _resolve_local_packs_dir()
MEDIANS_GZ_PATH = DATA_DIR / "hanzi_medians.json.gz"

