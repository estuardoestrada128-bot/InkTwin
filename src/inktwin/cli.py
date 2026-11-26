"""InkTwin CLI: doctor, packs, preflight, render (with legacy flat-CLI compatibility)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .fontgen import build_opentype_font
from .harvest import generate_template_pdf, harvest_template_pdf
from .overlay import (
    apply_marks_to_scene,
    auto_find_docx,
    detect_pdf_layout,
    parse_docx_worksheet,
    parse_markdown_answers,
    stamp_scene_onto_pdf,
)
from .pen import PEN_TYPES
from .scene.schema import resolve_scene_state
from .studio import serve_studio
from .pack import list_packs, load_pack
from .paper import PAPERS, resolve_paper
from .preflight import run_doctor, run_preflight
from .renderer import INK_PRESETS, build_html, export_html_to_pdf
from .scene import (
    apply_drift,
    apply_tighten,
    export_scene,
    inspect_scene,
    load_inkscene,
    make_atomic_ops,
    next_batch_id,
    save_edits,
    save_inkscene,
    undo_last_batch,
)
from .scene.editor_server import serve_editor
from .synth import MOOD_PRESETS, load_medians_db, synthesize_variants_for_pack


def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    for k, v in obj.items():
        print(f"{k}: {v}")


def _cmd_doctor(args: argparse.Namespace) -> int:
    res = run_doctor()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"InkTwin v{res['version']} | ok={res['ok']} | browser={res['browser']}")
        print(f"KaTeX={res['assets']['katex_ready']} | Medians={res['assets']['medians_ready']} | Packs={len(res['packs'])}")
        for pk in res["packs"]:
            print(f"  - [{pk.get('scope')}] {pk.get('id')}: {pk.get('total_chars')} chars ({pk.get('real_cjk_chars')} CJK) / {pk.get('variants')} variants")
    return 0 if res["ok"] else 1


def _cmd_packs(args: argparse.Namespace) -> int:
    packs = list_packs()
    if args.json:
        print(json.dumps({"packs": packs}, ensure_ascii=False, indent=2))
    else:
        for pk in packs:
            print(f"[{pk.get('scope')}] {pk.get('id')} ({pk.get('size_kb')} KB) - {pk.get('total_chars')} chars / {pk.get('variants')} variants | facets={pk.get('facets')}")
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    md_path = Path(args.input_md).resolve()
    if not md_path.exists():
        err = {"ok": False, "error": f"Input Markdown not found: {md_path}"}
        print(json.dumps(err, ensure_ascii=False) if args.json else err["error"], file=sys.stderr)
        return 1
    pack = load_pack(args.pack)
    paper_key = "grid" if args.paper == "auto" else args.paper
    paper_spec = resolve_paper(paper_key, args.page_size)
    docx_p = Path(args.docx).resolve() if args.docx else None
    report = run_preflight(
        md_path=md_path,
        pack=pack,
        paper_spec=paper_spec,
        font_size_pt=args.font_size,
        docx_path=docx_p,
        no_docx=args.no_docx,
        style=args.style,
        flow=args.flow,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cjk = report["cjk"]
        print(f"[Preflight] pack={report['pack']} | mode={report['mode']} | paper={report['paper']} ({report['usable_width_mm']}mm)")
        print(f"  CJK: {cjk['real_in_pack']}/{cjk['total_unique']} real ({cjk['real_coverage_ratio']*100:.1f}%), {cjk['synthesized_unseen']} unseen: {''.join(cjk['unseen_chars'])}")
        hfs = report.get("high_freq_single_variants") or []
        if hfs:
            print(f"  High-freq 1-variant ({len(hfs)}): {' '.join(x['char'] + '×' + str(x['count']) for x in hfs[:15])}")
        print(f"  Formula splits: {len(report['formula_splits'])}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    md_path = Path(args.input_md).resolve()
    if not md_path.exists():
        print(f"[错误] 找不到输入文件: {md_path}", file=sys.stderr)
        return 1

    md_text = md_path.read_text(encoding="utf-8")
    md_data = parse_markdown_answers(md_text)

    overlay_pdf: Optional[Path] = Path(args.overlay).resolve() if args.overlay else None
    layout_spec: Optional[dict] = None
    if args.layout_json:
        layout_spec = json.loads(Path(args.layout_json).read_text(encoding="utf-8"))
        if overlay_pdf is None and layout_spec.get("source_pdf"):
            cand_src = Path(layout_spec["source_pdf"])
            if cand_src.exists():
                overlay_pdf = cand_src
    elif overlay_pdf is not None:
        if not overlay_pdf.exists():
            print(f"[错误] 找不到指定空白原卷 PDF: {overlay_pdf}", file=sys.stderr)
            return 1
        layout_spec = detect_pdf_layout(overlay_pdf)

    docx_path: Optional[Path] = None
    if layout_spec is None and not args.no_docx and args.mode != "standalone":
        if args.docx:
            docx_path = Path(args.docx).resolve()
            if not docx_path.exists():
                print(f"[错误] 找不到指定 DOCX: {docx_path}", file=sys.stderr)
                return 1
        else:
            docx_path = auto_find_docx(md_path, md_data["frontmatter"])

    if layout_spec is not None:
        effective_mode = "pdf_overlay"
    elif docx_path is not None and args.mode != "standalone":
        effective_mode = "worksheet"
    else:
        effective_mode = "standalone"

    paper_key = args.paper
    if paper_key == "auto":
        paper_key = "white" if effective_mode in ("worksheet", "pdf_overlay") else "grid"
    paper_spec = resolve_paper(paper_key, args.page_size)
    docx_data = parse_docx_worksheet(docx_path) if (docx_path is not None and effective_mode == "worksheet") else None

    mood_cfg = MOOD_PRESETS.get(args.mood, MOOD_PRESETS["normal"])
    math_jitter = args.math_jitter if args.math_jitter is not None else mood_cfg["math_jitter"]
    text_jitter = args.text_jitter if args.text_jitter is not None else mood_cfg["text_jitter"]
    wobble = args.wobble if args.wobble is not None else mood_cfg["wobble"]
    quirks = (not args.no_quirks) and bool(mood_cfg["quirks"])
    if args.jitter is not None:
        math_jitter = round(0.55 * args.jitter, 3)
        text_jitter = round(1.15 * args.jitter, 3)

    seed = args.seed if args.seed is not None else int(hashlib.md5(md_path.name.encode("utf-8")).hexdigest()[:8], 16)

    if args.output:
        out_pdf = Path(args.output).resolve()
    elif overlay_pdf is not None:
        out_pdf = overlay_pdf.parent / f"{overlay_pdf.stem}_仿手写版.pdf"
    elif docx_path is not None:
        out_pdf = docx_path.parent / f"{docx_path.stem}_仿手写版.pdf"
    else:
        out_pdf = md_path.parent / f"{md_path.stem}_仿手写版.pdf"

    pack = load_pack(args.pack)
    use_pg = not args.no_personal
    use_learned_cjk = use_pg and (not args.no_personal_cjk)

    synth_cjk_bank = {}
    if use_learned_cjk:
        all_text_pool = md_text + "\n" + (md_data.get("meta") or "") + "\n" + (md_data.get("title") or "")
        unseen_cjk = sorted({
            ch for ch in all_text_pool
            if "\u4e00" <= ch <= "\u9fff" and (ch not in pack.glyphs or pack.glyphs[ch].get("cat") != "cjk")
        })
        if unseen_cjk:
            medians_db = load_medians_db()
            synth_cjk_bank = synthesize_variants_for_pack(
                unseen_cjk, pack, medians_db=medians_db, mood=args.mood, n_variants=4, base_seed=seed
            )

    pen_type = getattr(args, "pen_type", "gel") or "gel"
    need_scene = bool(args.save_scene or effective_mode == "pdf_overlay" or pen_type != "gel" or args.export_layers)

    html_str, formula_splits = build_html(
        mode=effective_mode,
        paper_spec=paper_spec,
        ink_key=args.ink,
        font_size_pt=args.font_size,
        math_jitter=math_jitter,
        text_jitter=text_jitter,
        wobble=wobble,
        seed=seed,
        flow=args.flow,
        style=args.style,
        layout=args.layout,
        use_personal_glyphs=use_pg,
        use_personal_cjk=use_learned_cjk,
        synth_cjk_bank=synth_cjk_bank,
        docx_data=docx_data,
        md_data=md_data,
        standalone_tag=args.tag,
        quirks=quirks,
        slant_add=float(mood_cfg.get("slant_add", 0.0)),
        elastic_amp=(0.0 if args.no_elastic else float(mood_cfg.get("elastic_amp", 0.026))),
        capture_scene=need_scene,
        pack=pack if use_pg else None,
        layout_spec=layout_spec,
    )

    keep_html_path = out_pdf.with_suffix(".html") if args.keep_html else None
    import tempfile
    post_render_via_scene = (effective_mode == "pdf_overlay" or pen_type != "gel")
    if post_render_via_scene:
        with tempfile.TemporaryDirectory(prefix="inktwin_chrome_") as ctmp:
            tmp_pdf = Path(ctmp) / "chrome_capture.pdf"
            captured_scene = export_html_to_pdf(html_str, tmp_pdf, pack=pack if use_pg else None, keep_html_path=keep_html_path)
    else:
        captured_scene = export_html_to_pdf(html_str, out_pdf, pack=pack if use_pg else None, keep_html_path=keep_html_path)

    if captured_scene is not None and layout_spec and layout_spec.get("marks"):
        apply_marks_to_scene(captured_scene, layout_spec.get("marks"))

    saved_scene_dir: Optional[str] = None
    tmp_scene_ctx = None
    s_dir: Optional[Path] = None
    if captured_scene is not None and need_scene:
        target_s_dir = Path(args.save_scene).resolve() if args.save_scene else None
        if target_s_dir is None:
            tmp_scene_ctx = tempfile.TemporaryDirectory(prefix="inktwin_sc_")
            target_s_dir = Path(tmp_scene_ctx.name)
        s_dir = save_inkscene(
            target_s_dir,
            captured_scene,
            paper_spec=paper_spec,
            pack=pack,
            synth_cjk=synth_cjk_bank,
            ink_key=args.ink,
            font_size_pt=args.font_size,
            background_pdf=(str(overlay_pdf) if (overlay_pdf and overlay_pdf.exists()) else None),
        )
        if args.save_scene:
            saved_scene_dir = str(s_dir)

    try:
        if post_render_via_scene and s_dir is not None:
            sc_obj, sc_edits, _ = load_inkscene(s_dir)
            resolved_sc = resolve_scene_state(sc_obj, sc_edits)
            if effective_mode == "pdf_overlay" and overlay_pdf is not None and overlay_pdf.exists():
                stamp_scene_onto_pdf(overlay_pdf, resolved_sc, out_pdf, pen_type=pen_type, dpi=300, seed=seed)
            else:
                export_scene(s_dir, out_pdf=out_pdf, fmt="pdf", dpi=300, pen_type=pen_type, seed=seed)

        exported_layers: List[str] = []
        if args.export_layers and s_dir is not None:
            ldir = Path(args.export_layers).resolve()
            r_svg = export_scene(s_dir, out_dir=ldir, fmt="svg", transparent=True, pen_type="gel", seed=seed)
            r_png = export_scene(s_dir, out_dir=ldir, fmt="png", dpi=300, transparent=True, pen_type=pen_type, seed=seed)
            exported_layers = r_svg.get("outputs", []) + r_png.get("outputs", [])
    finally:
        if tmp_scene_ctx is not None:
            tmp_scene_ctx.cleanup()

    result = {
        "ok": True,
        "output_pdf": str(out_pdf),
        "scene_dir": saved_scene_dir,
        "pack": pack.id,
        "mode": effective_mode,
        "paper": paper_spec.id,
        "page_size": args.page_size,
        "ink": args.ink,
        "mood": args.mood,
        "total_chars": len(pack.glyphs),
        "real_cjk_chars": pack.real_cjk_count,
        "synthesized_unseen_cjk": len(synth_cjk_bank),
        "formula_splits": len(formula_splits),
        "worksheet_docx": str(docx_path) if docx_path else None,
        "worksheet_questions": len(docx_data["questions"]) if docx_data else 0,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[成功] InkTwin v{__version__} 已生成仿手写版 PDF (全结构符号100%真迹 + 长公式智能断行 + 0.74px校准): {out_pdf}")
        print(f"       字库={pack.id} ({pack.real_cjk_count}真迹汉字/{pack.variant_count}变体 + 合成{len(synth_cjk_bank)}生字) | 状态={args.mood} | 断行公式={len(formula_splits)}")
        print(f"       模式={effective_mode}({args.layout}) | 底纸={paper_spec.id}({args.page_size}) | 墨水={args.ink} | 风格={args.style}/{args.flow}")
        if docx_path and docx_data:
            print(f"       原题卷={docx_path.name} (匹配 {len(docx_data['questions'])} 题)")
    return 0


def _add_render_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("input_md", help="Input Markdown homework/notes file")
    p.add_argument("-o", "--output", help="Output PDF path")
    p.add_argument("-p", "--pack", default=None, help="Handwriting pack name or .hwpack path (default: local andy or demo_sample)")
    p.add_argument("-d", "--docx", help="Optional blank worksheet .docx path")
    p.add_argument("--no-docx", action="store_true", help="Ignore worksheet DOCX and use standalone paper")
    p.add_argument("--mode", choices=["auto", "worksheet", "standalone"], default="auto")
    p.add_argument("--paper", choices=["auto"] + sorted(PAPERS.keys()), default="auto")
    p.add_argument("--page-size", choices=["A4", "B5", "16K", "LETTER"], default="A4")
    p.add_argument("--ink", choices=list(INK_PRESETS.keys()), default="black")
    p.add_argument("--mood", choices=list(MOOD_PRESETS.keys()), default="normal")
    p.add_argument("--style", choices=["concise", "andy", "full"], default="concise")
    p.add_argument("--flow", choices=["natural", "strict"], default="natural")
    p.add_argument("--layout", choices=["natural", "compact"], default="natural")
    p.add_argument("--font-size", type=float, default=12.0)
    p.add_argument("--jitter", type=float, default=None)
    p.add_argument("--math-jitter", type=float, default=None)
    p.add_argument("--text-jitter", type=float, default=None)
    p.add_argument("--wobble", type=float, default=None)
    p.add_argument("--no-quirks", action="store_true")
    p.add_argument("--no-elastic", action="store_true", help="Disable adaptive vector elastic micro-variation on repeated glyphs")
    p.add_argument("--no-personal", action="store_true")
    p.add_argument("--no-personal-cjk", action="store_true")
    p.add_argument("--personal-cjk", action="store_true")
    p.add_argument("--cn-font", default="learned")
    p.add_argument("--en-font", default="auto")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--tag", default="")
    p.add_argument("--overlay", default=None, help="Blank PDF worksheet path to overlay handwriting onto")
    p.add_argument("--layout-json", default=None, help="Declarative layout.json (boxes + marks) for PDF overlay")
    p.add_argument("--pen-type", choices=list(PEN_TYPES), default="gel", help="Physical pen ink texture preset")
    p.add_argument("--export-layers", default=None, help="Directory to export transparent SVG + PNG layers")
    p.add_argument("--save-scene", default=None, help="Save editable InkScene directory (scene.json + edits.json)")
    p.add_argument("--keep-html", action="store_true")
    p.add_argument("--json", action="store_true", help="Print structured JSON result")


def _cmd_scene_inspect(args: argparse.Namespace) -> int:
    scene, edits, _ = load_inkscene(Path(args.scene))
    report = inspect_scene(scene, edits, max_gap_ratio=args.max_gap)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[Inspect] pages={report['pages']} | glyphs={report['glyph_count']} | lines={report['line_count']} | anomalies={report['anomaly_counts']}")
        for h in report.get("hints", []):
            print(f"  hint: {h}")
    return 0


def _cmd_scene_render(args: argparse.Namespace) -> int:
    res = export_scene(
        Path(args.scene),
        out_pdf=Path(args.output) if args.output else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        fmt=args.format,
        dpi=args.dpi,
        transparent=args.transparent,
        pen_type=args.pen_type,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def _cmd_layout_detect(args: argparse.Namespace) -> int:
    layout = detect_pdf_layout(Path(args.blank_pdf), min_slot_h_mm=args.min_slot_mm)
    if args.output:
        out_p = Path(args.output).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json or not args.output:
        print(json.dumps(layout, ensure_ascii=False, indent=2))
    else:
        print(f"[LayoutDetect] pages={len(layout['pages'])} | boxes={len(layout['boxes'])} | obstacles={len(layout['obstacles'])} -> {args.output}")
    return 0


def _cmd_scene_tighten(args: argparse.Namespace) -> int:
    scene, edits, sdir = load_inkscene(Path(args.scene))
    lines = args.line.split(",") if args.line else None
    new_edits, added = apply_tighten(scene, edits, line_ids=lines, max_gap_ratio=args.max_gap, seed=args.seed)
    if added and not args.dry_run:
        save_edits(sdir, new_edits)
    print(json.dumps({"ok": True, "added": len(added), "dry_run": args.dry_run, "edits_count": len(new_edits)}, ensure_ascii=False, indent=2))
    return 0


def _cmd_scene_drift(args: argparse.Namespace) -> int:
    scene, edits, sdir = load_inkscene(Path(args.scene))
    new_edits, added = apply_drift(scene, edits, mode=args.mode, amount=args.amount, seed=args.seed, force=args.force)
    if added and not args.dry_run:
        save_edits(sdir, new_edits)
    print(json.dumps({"ok": True, "added": len(added), "dry_run": args.dry_run, "edits_count": len(new_edits)}, ensure_ascii=False, indent=2))
    return 0


def _cmd_scene_edit(args: argparse.Namespace) -> int:
    scene, edits, sdir = load_inkscene(Path(args.scene))
    batch = next_batch_id(edits)
    ids = args.ids.split(",") if args.ids else None
    op = make_atomic_ops(
        args.op,
        ids=ids,
        line=args.line,
        page=args.page,
        dx=args.dx,
        dy=args.dy,
        k=args.k,
        deg=args.deg,
        color=args.color,
        weight=args.weight,
        batch=batch,
    )
    new_edits = edits + [op]
    save_edits(sdir, new_edits)
    print(json.dumps({"ok": True, "batch": batch, "edits_count": len(new_edits)}, ensure_ascii=False, indent=2))
    return 0


def _cmd_scene_undo(args: argparse.Namespace) -> int:
    _scene, edits, sdir = load_inkscene(Path(args.scene))
    new_edits, removed = undo_last_batch(edits)
    save_edits(sdir, new_edits)
    print(json.dumps({"ok": True, "removed": removed, "edits_count": len(new_edits)}, ensure_ascii=False, indent=2))
    return 0


def _cmd_scene_reset(args: argparse.Namespace) -> int:
    _scene, _edits, sdir = load_inkscene(Path(args.scene))
    save_edits(sdir, [])
    print(json.dumps({"ok": True, "edits_count": 0}, ensure_ascii=False, indent=2))
    return 0


def _cmd_scene_editor(args: argparse.Namespace) -> int:
    serve_editor(Path(args.scene), port=args.port, open_browser=(not args.no_browser))
    return 0


def _cmd_studio(args: argparse.Namespace) -> int:
    serve_studio(
        workdir=Path(args.workdir) if args.workdir else None,
        port=args.port,
        open_browser=(not args.no_browser),
        reload=bool(getattr(args, "reload", False)),
    )
    return 0


def _cmd_template(args: argparse.Namespace) -> int:
    pk = load_pack(args.pack) if args.pack else None
    from_md = Path(args.from_md) if args.from_md else None
    out_pdf = Path(args.output or f"inktwin_{args.tier}_template.pdf")
    res = generate_template_pdf(out_pdf, tier=args.tier, chars=args.chars, from_md=from_md, pack=pk)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"[Template] tier={res['tier']} | pages={res['pages']} | cells={res['cells']} -> {res['pdf']}")
    return 0


def _cmd_harvest(args: argparse.Namespace) -> int:
    merge_p = Path(args.merge_into) if args.merge_into else None
    res = harvest_template_pdf(
        Path(args.filled_pdf),
        Path(args.output),
        pack_id=args.id,
        merge_into=merge_p,
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(
            f"[Harvest] filled={res['filled_cells']}/{res['total_cells']} | "
            f"+glyphs={res['added_glyphs']} +variants={res['added_variants']} "
            f"+strokes={res['added_strokes']} +radicals={res['added_radicals']} -> {res['pack']}"
        )
    return 0


def _cmd_fontbuild(args: argparse.Namespace) -> int:
    pk = load_pack(args.pack)
    ext = args.format.lower().lstrip(".")
    out_p = Path(args.output or f"InkTwin-{pk.id}.{ext}")
    res = build_opentype_font(pk, out_p, fmt=ext, family_name=args.name, augment_variants=(not args.no_augment))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(
            f"[FontBuild] {res['family']} ({res['format'].upper()}) | "
            f"chars={res['mapped_chars']} | calt_alt1={res['calt_alt1_pairs']} alt2={res['calt_alt2_triples']} "
            f"(aug={res.get('augmented_variants', 0)}) -> {res['font']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inktwin", description="InkTwin — Personal Handwriting Twin & Math/Physics PDF Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Check Python deps, browser, assets, and packs")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=_cmd_doctor)

    p_pks = sub.add_parser("packs", help="List available .hwpack handwriting packs")
    p_pks.add_argument("--json", action="store_true")
    p_pks.set_defaults(func=_cmd_packs)

    p_pre = sub.add_parser("preflight", help="Inspect CJK coverage and long formula splits without rendering")
    p_pre.add_argument("input_md", help="Input Markdown file")
    p_pre.add_argument("-p", "--pack", default=None)
    p_pre.add_argument("-d", "--docx", default=None)
    p_pre.add_argument("--no-docx", action="store_true")
    p_pre.add_argument("--paper", choices=["auto"] + sorted(PAPERS.keys()), default="grid")
    p_pre.add_argument("--page-size", choices=["A4", "B5", "16K", "LETTER"], default="A4")
    p_pre.add_argument("--font-size", type=float, default=12.0)
    p_pre.add_argument("--style", choices=["concise", "andy", "full"], default="concise")
    p_pre.add_argument("--flow", choices=["natural", "strict"], default="natural")
    p_pre.add_argument("--json", action="store_true")
    p_pre.set_defaults(func=_cmd_preflight)

    p_ren = sub.add_parser("render", help="Render Markdown+LaTeX into vector handwritten PDF")
    _add_render_flags(p_ren)
    p_ren.set_defaults(func=_cmd_render)

    p_insp = sub.add_parser("inspect", help="Alias for `inktwin scene inspect`")
    p_insp.add_argument("scene", help="Path to InkScene directory or scene.json")
    p_insp.add_argument("--max-gap", type=float, default=0.95)
    p_insp.add_argument("--json", action="store_true")
    p_insp.set_defaults(func=_cmd_scene_inspect)

    p_sc = sub.add_parser("scene", help="Non-destructive InkScene inspect, self-healing, edit, render & editor")
    sc_sub = p_sc.add_subparsers(dest="scene_cmd", required=True)

    ps_i = sc_sub.add_parser("inspect")
    ps_i.add_argument("scene")
    ps_i.add_argument("--max-gap", type=float, default=0.95)
    ps_i.add_argument("--json", action="store_true")
    ps_i.set_defaults(func=_cmd_scene_inspect)

    ps_r = sc_sub.add_parser("render")
    ps_r.add_argument("scene")
    ps_r.add_argument("-o", "--output", default=None)
    ps_r.add_argument("--out-dir", default=None)
    ps_r.add_argument("--format", choices=["pdf", "svg", "png"], default="pdf")
    ps_r.add_argument("--dpi", type=int, default=300)
    ps_r.add_argument("--transparent", action="store_true")
    ps_r.add_argument("--pen-type", choices=list(PEN_TYPES), default="gel")
    ps_r.set_defaults(func=_cmd_scene_render)

    ps_t = sc_sub.add_parser("tighten")
    ps_t.add_argument("scene")
    ps_t.add_argument("--line", default=None)
    ps_t.add_argument("--max-gap", type=float, default=0.95)
    ps_t.add_argument("--seed", type=int, default=42)
    ps_t.add_argument("--dry-run", action="store_true")
    ps_t.set_defaults(func=_cmd_scene_tighten)

    ps_d = sc_sub.add_parser("drift")
    ps_d.add_argument("scene")
    ps_d.add_argument("--mode", choices=["natural", "random"], default="natural")
    ps_d.add_argument("--amount", type=float, default=1.0)
    ps_d.add_argument("--seed", type=int, default=42)
    ps_d.add_argument("--force", action="store_true")
    ps_d.add_argument("--dry-run", action="store_true")
    ps_d.set_defaults(func=_cmd_scene_drift)

    ps_e = sc_sub.add_parser("edit")
    ps_e.add_argument("scene")
    ps_e.add_argument("--op", choices=["move", "scale", "rotate", "reroll", "pen", "hide", "show"], required=True)
    ps_e.add_argument("--ids", default=None)
    ps_e.add_argument("--line", default=None)
    ps_e.add_argument("--page", type=int, default=None)
    ps_e.add_argument("--dx", type=float, default=0.0)
    ps_e.add_argument("--dy", type=float, default=0.0)
    ps_e.add_argument("--k", type=float, default=1.0)
    ps_e.add_argument("--deg", type=float, default=0.0)
    ps_e.add_argument("--color", default=None)
    ps_e.add_argument("--weight", type=float, default=None)
    ps_e.set_defaults(func=_cmd_scene_edit)

    ps_u = sc_sub.add_parser("undo")
    ps_u.add_argument("scene")
    ps_u.set_defaults(func=_cmd_scene_undo)

    ps_rs = sc_sub.add_parser("reset")
    ps_rs.add_argument("scene")
    ps_rs.set_defaults(func=_cmd_scene_reset)

    ps_ed = sc_sub.add_parser("editor")
    ps_ed.add_argument("scene")
    ps_ed.add_argument("--port", type=int, default=0)
    ps_ed.add_argument("--no-browser", action="store_true")
    ps_ed.set_defaults(func=_cmd_scene_editor)

    p_tpl = sub.add_parser("template", help="Generate printable/iPad handwriting calibration sheet PDF")
    p_tpl.add_argument("--tier", choices=["full", "tier1", "tier2", "targeted"], default="tier1")
    p_tpl.add_argument("--chars", default=None, help="Explicit characters for targeted sheet")
    p_tpl.add_argument("--from-md", default=None, help="Auto-detect unseen chars from Markdown for targeted sheet")
    p_tpl.add_argument("-p", "--pack", default=None)
    p_tpl.add_argument("-o", "--output", default=None)
    p_tpl.add_argument("--json", action="store_true")
    p_tpl.set_defaults(func=_cmd_template)

    p_hrv = sub.add_parser("harvest", help="Zero-OCR extract vector ink from filled template PDF into .hwpack")
    p_hrv.add_argument("filled_pdf", help="Filled calibration sheet PDF exported from StarNote/GoodNotes/etc.")
    p_hrv.add_argument("-o", "--output", required=True, help="Output .hwpack path")
    p_hrv.add_argument("--id", default=None, help="Pack identifier")
    p_hrv.add_argument("--merge-into", default=None, help="Existing .hwpack to merge new variants into")
    p_hrv.add_argument("--json", action="store_true")
    p_hrv.set_defaults(func=_cmd_harvest)

    p_fb = sub.add_parser("fontbuild", help="Compile .hwpack into OpenType .ttf / .otf font with GSUB calt alternates")
    p_fb.add_argument("-p", "--pack", default=None)
    p_fb.add_argument("--format", choices=["ttf", "otf"], default="ttf")
    p_fb.add_argument("--name", default=None, help="Custom font family name")
    p_fb.add_argument("-o", "--output", default=None)
    p_fb.add_argument("--no-augment", action="store_true", help="Disable auto-synthesizing .alt1/.alt2 calt variants for 1-2 variant glyphs")
    p_fb.add_argument("--json", action="store_true")
    p_fb.set_defaults(func=_cmd_fontbuild)

    p_ld = sub.add_parser("layout-detect", help="Detect question prompts, obstacles, and blank slots in a PDF")
    p_ld.add_argument("blank_pdf", help="Blank worksheet/exam PDF path")
    p_ld.add_argument("-o", "--output", default=None, help="Output layout.json path")
    p_ld.add_argument("--min-slot-mm", type=float, default=15.0)
    p_ld.add_argument("--json", action="store_true")
    p_ld.set_defaults(func=_cmd_layout_detect)

    p_st = sub.add_parser("studio", help="Launch InkTwin Studio offline web workbench")
    p_st.add_argument("--port", type=int, default=0)
    p_st.add_argument("--workdir", default=None, help="Directory to store Studio projects & exports")
    p_st.add_argument("--no-browser", action="store_true")
    p_st.add_argument("--reload", action="store_true", help="Auto-reload backend and browser on source edits (development mode)")
    p_st.set_defaults(func=_cmd_studio)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw == ["--setup"]:
        return _cmd_doctor(argparse.Namespace(json=False))
    known_cmds = {
        "doctor", "packs", "preflight", "render", "inspect", "scene",
        "template", "harvest", "fontbuild", "layout-detect", "studio", "-h", "--help",
    }
    if raw and raw[0] not in known_cmds:
        raw = ["render"] + raw
    elif not raw:
        raw = ["--help"]

    parser = build_parser()
    args = parser.parse_args(raw)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

