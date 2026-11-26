"""Pack, template, harvest, fontbuild, cleanup, and backup API handlers for InkTwin Studio."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .. import LOCAL_PACKS_DIR
from ..fontgen import build_opentype_font
from ..harvest import generate_template_pdf, harvest_template_pdf
from ..harvest.templates import _CORE_RADICALS
from ..pack import load_pack
from ..pack.facets import compute_facets
from ..pack.quality import build_cleanup_plan, inspect_glyph_variants, pack_backup_path, pack_median_thickness
from ..pack.schema import save_pack_zip
from .state import StudioState, _b64_bytes, _pdf_vector_previews


def _is_local_pack(path: Path) -> bool:
    try:
        path.resolve().relative_to(LOCAL_PACKS_DIR.resolve())
        return True
    except ValueError:
        return False


def _pack_char_group(ch: str, cat: str) -> str:
    if cat == "cjk":
        return "cjk"
    if len(ch) == 1 and (ch.isdigit() or ("a" <= ch.lower() <= "z")):
        return "alnum"
    if ch.startswith("__") or cat in ("frac_bar", "accent_bar", "accent_hat", "delim"):
        return "struct"
    return "math"


def _pack_char_sort_key(ch: str, cat: str) -> Tuple[int, str]:
    if len(ch) == 1 and ch.isdigit():
        return (0, ch)
    if len(ch) == 1 and "a" <= ch <= "z":
        return (1, ch)
    if len(ch) == 1 and "A" <= ch <= "Z":
        return (2, ch)
    if cat != "cjk" and not ch.startswith("__"):
        return (3, ch)
    if ch.startswith("__"):
        return (4, ch)
    return (5, ch)


def _refresh_and_save_local_pack(pk) -> None:
    manifest = dict(pk.manifest)
    manifest["total_chars"] = len(pk.glyphs)
    manifest["total_variants"] = pk.variant_count
    manifest["facets"] = compute_facets(pk.glyphs, pk.style_model)
    manifest["stats"] = {
        "total_chars": len(pk.glyphs),
        "real_cjk_chars": pk.real_cjk_count,
        "variants": pk.variant_count,
        "radicals": len(pk.radicals),
        "strokes": sum(len(v) for v in pk.strokes.values()),
    }
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp_path = pk.source_path.with_suffix(".hwpack.tmp")
    save_pack_zip(tmp_path, manifest, pk.glyphs, pk.style_model, pk.radicals, pk.strokes)
    tmp_path.replace(pk.source_path)


def api_template(st: StudioState, p: dict) -> dict:
    tier = p.get("tier") or "tier1"
    out = st.workdir / f"template_{st.next_id()}_{tier}.pdf"
    chars = p.get("chars") or None
    radicals = p.get("radicals") or None
    char_copies = p.get("char_copies") or None
    if tier == "targeted" and not chars and not radicals and p.get("markdown"):
        md = out.with_suffix(".md")
        md.write_text(p["markdown"], encoding="utf-8")
        res = generate_template_pdf(
            out,
            tier=tier,
            from_md=md,
            pack=load_pack(p.get("pack") or None),
            char_copies=char_copies,
            radicals=radicals,
        )
    else:
        res = generate_template_pdf(out, tier=tier, chars=chars, char_copies=char_copies, radicals=radicals)
    return {
        "ok": True,
        "result": res,
        "pdf_url": st.url_for(out),
        "previews": _pdf_vector_previews(st, out, f"tpl_{tier}", max_pages=8),
    }


def api_harvest(st: StudioState, p: dict) -> dict:
    hdir = st.workdir / f"harvest_{st.next_id()}"
    hdir.mkdir(parents=True, exist_ok=True)
    filled = hdir / "filled.pdf"
    filled.write_bytes(_b64_bytes(p["pdf_b64"]))
    merge = (p.get("merge_into") or "").strip() or None
    merge_pack = load_pack(merge) if merge else None
    merge_path = merge_pack.source_path if merge_pack else None
    raw_id = (p.get("id") or "").strip()
    if not raw_id and merge_pack and _is_local_pack(merge_pack.source_path):
        raw_id = merge_pack.id
    pid = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id or "my_hand") or "my_hand"
    out = LOCAL_PACKS_DIR / f"{pid}.hwpack"
    res = harvest_template_pdf(filled, out, pack_id=pid, merge_into=merge_path)
    return {"ok": True, "result": res}


def api_fontbuild(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    fmt = (p.get("format") or "ttf").lower()
    fdir = st.workdir / f"font_{st.next_id()}"
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(pk.id or "pack")).strip(".") or "pack"
    out = fdir / f"InkTwin-{safe_id}.{fmt}"
    aug = p.get("augment_variants")
    augment = True if aug is None else bool(aug)
    res = build_opentype_font(pk, out, fmt=fmt, family_name=p.get("name") or None, augment_variants=augment)
    return {"ok": True, "result": res, "font_url": st.url_for(out)}


def api_pack_inspect(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    editable = _is_local_pack(pk.source_path) and pk.source_path.is_file()
    med_t = pack_median_thickness(pk.glyphs)

    items: List[dict] = []
    single_variant_chars = 0
    flagged_variants = 0
    flagged_chars = 0
    low_variant_non_cjk: List[str] = []

    for ch in sorted(pk.glyphs.keys(), key=lambda c: _pack_char_sort_key(c, pk.glyphs[c].get("cat", ""))):
        entry = pk.glyphs[ch]
        cat = entry.get("cat", "cap")
        group = _pack_char_group(ch, cat)
        v_out, char_has_flag = inspect_glyph_variants(ch, entry, med_t)
        flagged_variants += sum(1 for v in v_out if v["flags"])
        if char_has_flag:
            flagged_chars += 1
        if len(v_out) == 1:
            single_variant_chars += 1
            if group != "cjk" and not ch.startswith("__"):
                low_variant_non_cjk.append(ch)
        items.append({"char": ch, "cat": cat, "group": group, "variants": v_out, "flagged": char_has_flag})

    missing_radicals = [r for r in _CORE_RADICALS if r not in pk.radicals]
    strokes_counts = {k: len(pk.strokes.get(k, [])) for k in ("H", "V", "P", "N", "D")}
    has_backup = editable and pack_backup_path(pk.id).is_file()
    return {
        "ok": True,
        "pack": {
            "id": pk.id,
            "scope": "local" if editable else "public",
            "editable": editable,
            "has_backup": has_backup,
            "path": str(pk.source_path),
            "total_chars": len(pk.glyphs),
            "total_variants": pk.variant_count,
            "cjk_chars": pk.real_cjk_count,
            "single_variant_chars": single_variant_chars,
            "flagged_variants": flagged_variants,
            "flagged_chars": flagged_chars,
            "median_t": round(med_t, 2),
            "radicals_count": len(pk.radicals),
            "missing_radicals": missing_radicals,
            "strokes": strokes_counts,
            "low_variant_non_cjk": low_variant_non_cjk,
        },
        "items": items,
    }


def api_pack_delete_variant(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    if not _is_local_pack(pk.source_path) or not pk.source_path.is_file():
        raise PermissionError("公开示例字库为只读，无法直接删改。请先点击「克隆到本地以编辑」，或在字体工坊另存到 packs/local/。")
    ch = p.get("char") or ""
    if not ch or ch not in pk.glyphs:
        raise ValueError(f"Character not found in pack: {ch!r}")
    variants = pk.glyphs[ch].get("v") or []
    vidx = int(p.get("vidx", -1))
    if vidx < 0 or vidx >= len(variants):
        raise ValueError(f"Variant index out of range: {vidx} (len={len(variants)})")

    bak_path = pack_backup_path(pk.id)
    bak_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pk.source_path, bak_path)

    variants.pop(vidx)
    remaining = len(variants)
    if remaining == 0:
        del pk.glyphs[ch]

    _refresh_and_save_local_pack(pk)
    return {
        "ok": True,
        "pack": pk.id,
        "deleted": {"char": ch, "vidx": vidx, "remaining": remaining},
        "total_chars": len(pk.glyphs),
        "total_variants": pk.variant_count,
    }


def api_pack_clone_local(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    raw_id = (p.get("new_id") or f"{pk.id}_local").strip()
    new_id = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id) or f"{pk.id}_local"
    out_path = (LOCAL_PACKS_DIR / f"{new_id}.hwpack").resolve()
    if out_path.exists():
        raise FileExistsError(f"本地字库 {new_id}.hwpack 已存在，请换一个名称。")
    manifest = dict(pk.manifest)
    manifest["id"] = new_id
    manifest["privacy"] = "local"
    manifest["cloned_from"] = pk.id
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_pack_zip(out_path, manifest, pk.glyphs, pk.style_model, pk.radicals, pk.strokes)
    return {"ok": True, "id": new_id, "path": str(out_path)}


def api_pack_cleanup_plan(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    editable = _is_local_pack(pk.source_path) and pk.source_path.is_file()
    plan = build_cleanup_plan(pk)
    plan["ok"] = True
    plan["editable"] = editable
    plan["missing_radicals"] = [r for r in _CORE_RADICALS if r not in pk.radicals]
    plan["low_variant_non_cjk"] = [
        ch
        for ch, entry in sorted(pk.glyphs.items(), key=lambda kv: _pack_char_sort_key(kv[0], kv[1].get("cat", "")))
        if len(entry.get("v", [])) == 1 and _pack_char_group(ch, entry.get("cat", "cap")) != "cjk" and not ch.startswith("__")
    ]
    return plan


def api_pack_cleanup_apply(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    editable = _is_local_pack(pk.source_path) and pk.source_path.is_file()
    plan = build_cleanup_plan(pk)
    req_items = p.get("items")
    to_delete = list(req_items) if isinstance(req_items, list) else list(plan["deletions"])

    deleted_count = 0
    emptied_chars: List[str] = []
    affected_single: List[str] = []
    has_backup = editable and pack_backup_path(pk.id).is_file()

    if to_delete:
        if not editable:
            raise PermissionError("公开示例字库为只读，请先点击「克隆到本地以编辑」后再删除异常变体。")
        bak_path = pack_backup_path(pk.id)
        bak_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pk.source_path, bak_path)

        del_by_char: Dict[str, List[int]] = {}
        for it in to_delete:
            ch = it.get("char")
            if ch and ch in pk.glyphs:
                del_by_char.setdefault(ch, []).append(int(it["vidx"]))

        for ch, idxs in del_by_char.items():
            vs = pk.glyphs[ch].get("v", [])
            for vi in sorted(set(idxs), reverse=True):
                if 0 <= vi < len(vs):
                    vs.pop(vi)
                    deleted_count += 1
            if not vs:
                del pk.glyphs[ch]
                emptied_chars.append(ch)
            elif len(vs) == 1:
                affected_single.append(ch)

        _refresh_and_save_local_pack(pk)
        has_backup = bak_path.is_file()

    sheet_info = None
    if bool(p.get("generate_sheet", True)):
        inc_anom = bool(p.get("include_anomaly_refill", True))
        inc_single = bool(p.get("include_single_variants", False))
        inc_rad = bool(p.get("include_missing_radicals", False))

        p3 = (
            list(dict.fromkeys(
                emptied_chars
                + [k["char"] for k in plan.get("kept_fallbacks", []) if k["char"] in pk.glyphs or k["char"] in emptied_chars]
            ))
            if inc_anom else []
        )
        p2 = (
            [ch for ch in dict.fromkeys(affected_single) if ch not in p3]
            if inc_anom else []
        )
        seen_all = set(p3 + p2)
        extra_single: List[str] = []
        if inc_single:
            for ch, entry in sorted(pk.glyphs.items(), key=lambda kv: _pack_char_sort_key(kv[0], kv[1].get("cat", ""))):
                if ch in seen_all or ch.startswith("__"):
                    continue
                if _pack_char_group(ch, entry.get("cat", "cap")) != "cjk" and len(entry.get("v", [])) == 1:
                    seen_all.add(ch)
                    extra_single.append(ch)

        chars_ordered = "".join(p3 + p2 + extra_single)
        char_copies = {ch: 3 for ch in p3}
        for ch in p2 + extra_single:
            char_copies[ch] = 2
        rads = ([r for r in _CORE_RADICALS if r not in pk.radicals] if inc_rad else None)
        if chars_ordered or rads:
            out = st.workdir / f"refill_{st.next_id()}.pdf"
            res = generate_template_pdf(out, tier="targeted", chars=(chars_ordered or None), char_copies=char_copies, radicals=rads)
            sheet_info = {
                "result": res,
                "pdf_url": st.url_for(out),
                "previews": _pdf_vector_previews(st, out, "refill", max_pages=8),
                "priority3_count": len(p3),
                "priority2_count": len(p2),
                "single_count": len(extra_single),
                "radicals_count": len(rads or []),
            }

    if not deleted_count and not sheet_info:
        raise ValueError("没有需要清理的异常变体，也没有勾选任何补写内容。")

    return {
        "ok": True,
        "pack": pk.id,
        "deleted_count": deleted_count,
        "kept_fallbacks": len(plan.get("kept_fallbacks", [])),
        "has_backup": has_backup,
        "total_chars": len(pk.glyphs),
        "total_variants": pk.variant_count,
        "sheet": sheet_info,
    }


def api_pack_restore_backup(st: StudioState, p: dict) -> dict:
    pk = load_pack(p.get("pack") or None)
    if not (_is_local_pack(pk.source_path) and pk.source_path.is_file()):
        raise PermissionError("只能还原本地字库备份。")
    bak = pack_backup_path(pk.id)
    if not bak.is_file():
        raise FileNotFoundError("未找到可还原的清理备份。")
    tmp_path = pk.source_path.with_suffix(".hwpack.tmp")
    shutil.copy2(bak, tmp_path)
    tmp_path.replace(pk.source_path)
    bak.unlink(missing_ok=True)
    restored = load_pack(pk.id)
    return {
        "ok": True,
        "pack": restored.id,
        "total_chars": len(restored.glyphs),
        "total_variants": restored.variant_count,
    }


PACK_ROUTES_POST = {
    "/api/template": api_template,
    "/api/harvest": api_harvest,
    "/api/fontbuild": api_fontbuild,
    "/api/pack/inspect": api_pack_inspect,
    "/api/pack/delete-variant": api_pack_delete_variant,
    "/api/pack/clone-local": api_pack_clone_local,
    "/api/pack/cleanup-plan": api_pack_cleanup_plan,
    "/api/pack/cleanup-apply": api_pack_cleanup_apply,
    "/api/pack/restore-backup": api_pack_restore_backup,
}

