"""Standard .hwpack archive loader, writer, and pack discovery."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .. import LOCAL_PACKS_DIR, PACKS_DIR, USER_DATA_DIR
from .physics_symbols import ensure_physics_symbols


@dataclass
class HwPack:
    """In-memory representation of a portable .hwpack handwriting bundle."""

    id: str
    manifest: dict
    glyphs: Dict[str, dict]
    style_model: dict
    radicals: Dict[str, list]
    strokes: Dict[str, list]
    source_path: Path

    @property
    def real_cjk_count(self) -> int:
        return sum(1 for v in self.glyphs.values() if v.get("cat") == "cjk")

    @property
    def variant_count(self) -> int:
        return sum(len(v.get("v", [])) for v in self.glyphs.values())

    @property
    def has_synth_assets(self) -> bool:
        return bool(self.style_model and (self.radicals or self.strokes))


def _local_pack_dirs() -> List[Path]:
    dirs: List[Path] = []
    seen = set()
    for d in (LOCAL_PACKS_DIR, USER_DATA_DIR / "packs"):
        rd = d.resolve()
        if rd not in seen:
            seen.add(rd)
            dirs.append(d)
    return dirs


def resolve_pack_path(name_or_path: Optional[str]) -> Path:
    """Resolve a pack alias or path; local packs take priority over public packs."""
    local_dirs = _local_pack_dirs()
    if name_or_path:
        cand = Path(name_or_path)
        if cand.exists():
            return cand.resolve()
        stem = name_or_path[:-7] if name_or_path.endswith(".hwpack") else name_or_path
        for base in [*local_dirs, PACKS_DIR]:
            z = base / f"{stem}.hwpack"
            if z.exists():
                return z.resolve()
            d = base / stem
            if d.is_dir() and (d / "glyphs.json").exists():
                return d.resolve()
        raise FileNotFoundError(f"Handwriting pack not found: {name_or_path}")

    for ldir in local_dirs:
        if ldir.is_dir():
            any_local = sorted(ldir.glob("*.hwpack"))
            if any_local:
                return any_local[0].resolve()
    demo_pub = PACKS_DIR / "demo_sample.hwpack"
    if demo_pub.exists():
        return demo_pub.resolve()
    any_pub = sorted(PACKS_DIR.glob("*.hwpack"))
    if any_pub:
        return any_pub[0].resolve()
    raise FileNotFoundError("No .hwpack files found in local or bundled packs.")


def load_pack(name_or_path: Optional[str] = None) -> HwPack:
    """Load an .hwpack from either a ZIP archive or an unpacked directory."""
    path = resolve_pack_path(name_or_path)
    if path.is_dir():
        def _rd(fn: str, default):
            p = path / fn
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

        manifest = _rd("manifest.json", {"id": path.name})
        glyphs = _rd("glyphs.json", {})
        style_model = _rd("style_model.json", {})
        radicals = _rd("radicals.json", {})
        strokes = _rd("strokes.json", {})
    else:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())

            def _rdz(fn: str, default):
                if fn not in names:
                    return default
                return json.loads(zf.read(fn).decode("utf-8"))

            manifest = _rdz("manifest.json", {"id": path.stem})
            glyphs = _rdz("glyphs.json", {})
            style_model = _rdz("style_model.json", {})
            radicals = _rdz("radicals.json", {})
            strokes = _rdz("strokes.json", {})

    ensure_physics_symbols(glyphs)
    pid = str(manifest.get("id") or path.stem)
    return HwPack(
        id=pid,
        manifest=manifest,
        glyphs=glyphs,
        style_model=style_model,
        radicals=radicals,
        strokes=strokes,
        source_path=path,
    )


def save_pack_zip(
    out_path: Path,
    manifest: dict,
    glyphs: dict,
    style_model: dict,
    radicals: dict,
    strokes: dict,
) -> Path:
    """Serialize all handwriting pack components into a compressed .hwpack ZIP."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fname, obj in (
            ("manifest.json", manifest),
            ("glyphs.json", glyphs),
            ("style_model.json", style_model),
            ("radicals.json", radicals),
            ("strokes.json", strokes),
        ):
            payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            zf.writestr(fname, payload.encode("utf-8"))
    return out_path


def list_packs() -> List[dict]:
    """List all discovered public and local handwriting packs using fast manifest-only ZIP reads."""
    import zipfile

    results: List[dict] = []
    seen_paths = set()
    scopes: List[Tuple[str, Path]] = [("local", d) for d in _local_pack_dirs()] + [("public", PACKS_DIR)]
    for scope, folder in scopes:
        if not folder.exists():
            continue
        for zp in sorted(folder.glob("*.hwpack")):
            rp = zp.resolve()
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            try:
                size_kb = round(rp.stat().st_size / 1024.0, 1)
                with zipfile.ZipFile(rp, "r") as zf:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                stats = manifest.get("stats") or {}
                if "total_chars" in manifest or "variants" in stats:
                    results.append(
                        {
                            "id": str(manifest.get("id") or zp.stem),
                            "scope": scope,
                            "path": str(rp),
                            "size_kb": size_kb,
                            "total_chars": int(manifest.get("total_chars", stats.get("total_chars", 0))),
                            "real_cjk_chars": int(stats.get("real_cjk_chars", 0)),
                            "variants": int(manifest.get("total_variants", stats.get("variants", 0))),
                            "radicals": int(stats.get("radicals", 0)),
                            "strokes": int(stats.get("strokes", 0)),
                            "facets": manifest.get("facets", {}),
                            "privacy": manifest.get("privacy", scope),
                        }
                    )
                else:
                    pk = load_pack(str(rp))
                    results.append(
                        {
                            "id": pk.id,
                            "scope": scope,
                            "path": str(rp),
                            "size_kb": size_kb,
                            "total_chars": len(pk.glyphs),
                            "real_cjk_chars": pk.real_cjk_count,
                            "variants": pk.variant_count,
                            "radicals": len(pk.radicals),
                            "strokes": sum(len(v) for v in pk.strokes.values()),
                            "facets": pk.manifest.get("facets", {}),
                            "privacy": pk.manifest.get("privacy", scope),
                        }
                    )
            except Exception as exc:
                results.append({"id": zp.stem, "scope": scope, "path": str(rp), "error": str(exc)})
    return results

