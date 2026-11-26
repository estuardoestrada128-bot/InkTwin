"""Lossless migration from Demo v1.0 legacy skill assets to .hwpack bundles."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional, Set, Tuple

from .facets import compute_facets
from .schema import save_pack_zip


def _build_manifest(pack_id: str, name: str, glyphs: dict, facets: dict, privacy: str) -> dict:
    cjk_n = sum(1 for v in glyphs.values() if v.get("cat") == "cjk")
    var_n = sum(len(v.get("v", [])) for v in glyphs.values())
    return {
        "schema_version": "1.0",
        "id": pack_id,
        "name": name,
        "created_at": date.today().isoformat(),
        "privacy": privacy,
        "calibrated_stroke_px": 0.74,
        "stats": {
            "total_chars": len(glyphs),
            "real_cjk_chars": cjk_n,
            "variants": var_n,
        },
        "facets": facets,
    }


def migrate_from_legacy_skill(
    skill_custom_dir: Path,
    private_out: Path,
    demo_out: Path,
    exclude_chars: Optional[Set[str]] = None,
) -> Tuple[Path, Path]:
    """Migrate Demo v1.0 JSONs into private and anonymized .hwpack bundles."""
    glyphs = json.loads((skill_custom_dir / "personal_glyphs.json").read_text(encoding="utf-8"))
    style_model = json.loads((skill_custom_dir / "personal_style_model.json").read_text(encoding="utf-8"))
    radicals = json.loads((skill_custom_dir / "personal_radicals.json").read_text(encoding="utf-8"))
    strokes = json.loads((skill_custom_dir / "personal_strokes.json").read_text(encoding="utf-8"))

    facets = compute_facets(glyphs, style_model)
    private_manifest = _build_manifest(
        pack_id="personal",
        name="Personal Handwriting Pack",
        glyphs=glyphs,
        facets=facets,
        privacy="private-local",
    )
    save_pack_zip(private_out, private_manifest, glyphs, style_model, radicals, strokes)

    drop = exclude_chars or set()
    demo_glyphs = {k: v for k, v in glyphs.items() if k not in drop}
    demo_facets = compute_facets(demo_glyphs, style_model)
    demo_manifest = _build_manifest(
        pack_id="demo_sample",
        name="InkTwin Sample STEM Handwriting Pack (Anonymized)",
        glyphs=demo_glyphs,
        facets=demo_facets,
        privacy="public-anonymized",
    )
    save_pack_zip(demo_out, demo_manifest, demo_glyphs, style_model, radicals, strokes)
    return private_out, demo_out

