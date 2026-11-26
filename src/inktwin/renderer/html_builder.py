"""Modular HTML builder combining KaTeX, PaperSpec CSS, Formula Chain Splitter, and DOM JS."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from markdown_it import MarkdownIt

from .. import FONTS_DIR
from ..mathsplit import preprocess_markdown_block
from ..overlay import extract_week_number
from ..paper import PaperSpec, paper_to_css, usable_width_mm
from ..sketch import render_handplot_svg

INK_PRESETS: Dict[str, Tuple[str, str, str]] = {
    "black": ("#111318", "#20242d", "#c6332f"),
    "blue": ("#19378a", "#2646a0", "#c6332f"),
    "darkblue": ("#13244b", "#1e3463", "#c6332f"),
    "pencil": ("#30343d", "#454a56", "#b63d39"),
}


def render_markdown_with_math_placeholders(md_text: str, pack=None, seed: int = 42) -> str:
    sketch_tokens: List[Tuple[str, str]] = []

    def _store_sketch(m) -> str:
        idx = len(sketch_tokens)
        key = f"@@SKETCHTOKEN{idx}END@@"
        raw_body = m.group(1)
        try:
            spec = yaml.safe_load(raw_body)
            svg_html = render_handplot_svg(spec, pack=pack, seed=seed + idx * 17)
        except Exception as exc:
            svg_html = f"<!-- handplot error: {html.escape(str(exc))} -->"
        sketch_tokens.append((key, f'<div class="hw-sketch-wrap">{svg_html}</div>'))
        return f"\n\n{key}\n\n"

    text_no_sketch = re.sub(
        r"```handplot\s*\n(.*?)```",
        _store_sketch,
        md_text,
        flags=re.S | re.IGNORECASE,
    )

    tokens: List[Tuple[str, str, bool]] = []

    def store(tex: str, display: bool) -> str:
        idx = len(tokens)
        key = f"@@MATHTOKEN{idx}END@@"
        tokens.append((key, tex.replace("\x00BSLASH\x00", "\\\\").strip(), display))
        return key

    masked = text_no_sketch.replace("\\\\", "\x00BSLASH\x00")
    protected = re.sub(r"\$\$(.*?)\$\$", lambda m: store(m.group(1), True), masked, flags=re.S)
    protected = re.sub(r"(?<!\\)\$([^\$\n]+?)(?<!\\)\$", lambda m: store(m.group(1), False), protected)
    protected = protected.replace("\x00BSLASH\x00", "\\\\")

    md_engine = MarkdownIt("commonmark", {"html": True, "breaks": True})
    md_engine.enable("table")
    rendered = md_engine.render(protected)

    for key, sketch_div in sketch_tokens:
        rendered = rendered.replace(f"<p>{key}</p>", sketch_div).replace(key, sketch_div)

    for key, tex, is_display in tokens:
        escaped_tex = html.escape(tex, quote=True)
        if is_display:
            node = f'<div class="hw-math-display" data-tex="{escaped_tex}"></div>'
            rendered = rendered.replace(f"<p>{key}</p>", node)
            rendered = re.sub(
                rf"(?:<br\s*/?>\s*)*{re.escape(key)}(?:\s*<br\s*/?>)*",
                lambda _m, rep=f"</p>{node}<p>": rep,
                rendered,
            )
        else:
            node = f'<span class="hw-math-inline" data-tex="{escaped_tex}"></span>'
            rendered = rendered.replace(key, node)

    rendered = re.sub(r"<p>\s*</p>", "", rendered)
    rendered = re.sub(r"(<li>\s*)</p>", r"\1", rendered)
    rendered = re.sub(r"<p>(\s*</li>)", r"\1", rendered)
    rendered = re.sub(r"^(\s*)</p>", r"\1", rendered)
    rendered = re.sub(r"<p>(\s*)$", r"\1", rendered)
    return rendered


def build_html(
    *,
    mode: str,
    paper_spec: PaperSpec,
    ink_key: str,
    font_size_pt: float,
    math_jitter: float,
    text_jitter: float,
    wobble: float,
    seed: int,
    flow: str,
    style: str,
    layout: str,
    use_personal_glyphs: bool,
    use_personal_cjk: bool,
    synth_cjk_bank: Optional[Dict],
    docx_data: Optional[Dict],
    md_data: Dict,
    standalone_tag: str = "",
    quirks: bool = True,
    slant_add: float = 0.0,
    elastic_amp: float = 0.026,
    capture_scene: bool = False,
    pack=None,
    layout_spec: Optional[dict] = None,
) -> Tuple[str, List[dict]]:
    ink_primary, ink_secondary, ink_red = INK_PRESETS.get(ink_key, INK_PRESETS["black"])
    avail_mm = usable_width_mm(paper_spec)
    all_splits: List[dict] = []
    blocks_html: List[str] = []

    if mode == "pdf_overlay" and layout_spec is not None:
        capture_scene = True
        ans_map: Dict[int, Dict] = {sec["num"]: sec for sec in md_data["sections"]}
        seq_secs = list(md_data["sections"])
        boxes_by_page: Dict[int, List[dict]] = {}
        for b in layout_spec.get("boxes", []):
            boxes_by_page.setdefault(int(b.get("page", 0)), []).append(b)

        used_qnums = set()
        seq_idx = 0
        for pmeta in layout_spec.get("pages", [{"page": 0, "width_mm": paper_spec.width_mm, "height_mm": paper_spec.height_mm}]):
            pno = int(pmeta["page"])
            pw = float(pmeta["width_mm"])
            ph = float(pmeta["height_mm"])
            box_divs: List[str] = []
            for box in boxes_by_page.get(pno, []):
                qnum = int(box.get("qnum", 0))
                sec = ans_map.get(qnum) if (qnum in ans_map and qnum not in used_qnums) else None
                if sec is not None:
                    used_qnums.add(qnum)
                elif qnum == 0 and seq_idx < len(seq_secs):
                    sec = seq_secs[seq_idx]
                    seq_idx += 1
                if not sec:
                    continue
                bw = float(box["w_mm"])
                pre, sp = preprocess_markdown_block(sec["markdown"], max(45.0, bw - 4.0), font_size_pt, flow=flow, style=style)
                for s in sp:
                    all_splits.append({"question": sec["num"], **s})
                ans_html = render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + qnum * 31)
                box_divs.append(
                    f'<div class="ov-box hw-answer" data-box-id="{html.escape(str(box.get("id", "")), quote=True)}" '
                    f'data-qnum="{sec["num"]}" data-slot-mm="{float(box.get("h_mm", 40.0)):.2f}" '
                    f'style="position:absolute;left:{float(box["x_mm"]):.2f}mm;top:{float(box["y_mm"]):.2f}mm;width:{bw:.2f}mm;">'
                    f'{ans_html}</div>'
                )
            blocks_html.append(
                f'<div class="ov-page" data-page="{pno}" style="position:relative;width:{pw:.2f}mm;height:{ph:.2f}mm;overflow:hidden;page-break-after:always;">'
                + "\n".join(box_divs)
                + "</div>"
            )
    elif mode == "worksheet" and docx_data is not None:
        h_title = html.escape(docx_data["title"] or md_data["title"] or "课后作业")
        h_meta = html.escape(docx_data["meta"] or md_data["meta"] or "")
        blocks_html.append(
            f"""
            <header class="ws-header">
              <h1 class="ws-title">{h_title}</h1>
              {f'<div class="ws-meta">{h_meta}</div>' if h_meta else ''}
              <hr class="ws-divider" />
            </header>
            """
        )
        ans_map: Dict[int, Dict] = {sec["num"]: sec for sec in md_data["sections"]}
        used_nums = set()

        for q in docx_data["questions"]:
            qnum = q["num"]
            qtitle = html.escape(q["title"])
            imgs_html = "\n".join(
                f'<div class="ws-qimg-wrap"><img class="ws-qimg" src="{img["src"]}" style="width:{img["width_mm"]}mm;" /></div>'
                for img in q["images"]
            )
            extra_texts = "\n".join(f'<div class="ws-qtext">{html.escape(t)}</div>' for t in q["texts"])

            ans_html = ""
            if qnum in ans_map:
                used_nums.add(qnum)
                pre, sp = preprocess_markdown_block(ans_map[qnum]["markdown"], avail_mm, font_size_pt, flow=flow, style=style)
                for s in sp:
                    all_splits.append({"question": qnum, **s})
                ans_html = render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + qnum * 19)

            blocks_html.append(
                f"""
                <section class="ws-question" data-qnum="{qnum}" data-slot-mm="{q.get('slot_mm', 44.0)}">
                  <div class="ws-prompt">
                    <div class="ws-qtitle">{qtitle}</div>
                    {extra_texts}
                    {imgs_html}
                  </div>
                  <div class="hw-answer">{ans_html}</div>
                </section>
                """
            )

        for sec in md_data["sections"]:
            if sec["num"] not in used_nums:
                pre, sp = preprocess_markdown_block(sec["markdown"], avail_mm, font_size_pt, flow=flow, style=style)
                for s in sp:
                    all_splits.append({"question": sec["num"], **s})
                ans_html = render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + sec["num"] * 19)
                blocks_html.append(
                    f"""
                    <section class="ws-question" data-qnum="{sec['num']}">
                      <div class="ws-prompt"><div class="ws-qtitle">{html.escape(sec['heading'])}</div></div>
                      <div class="hw-answer">{ans_html}</div>
                    </section>
                    """
                )
    else:
        tag_text = standalone_tag
        if not tag_text:
            wk = extract_week_number(md_data["title"] or str(md_data["frontmatter"].get("week", "")))
            tag_text = f"W{wk}" if wk is not None else (md_data["title"] or "作业")
        header_meta = md_data["meta"]

        if paper_spec.kind == "grid":
            blocks_html.append(
                f"""
                <header class="st-grid-header">
                  <span class="st-grid-tag">{html.escape(tag_text)}</span>
                  {f'<span class="st-grid-meta">{html.escape(header_meta)}</span>' if header_meta else ''}
                </header>
                """
            )
        elif paper_spec.has_header:
            blocks_html.append(
                '<header class="st-ruled-header"><span>No. ________</span><span>Date ____ / ____</span></header>'
            )
        else:
            blocks_html.append(
                f"""
                <header class="st-header">
                  <div class="st-title hw-text-line">{html.escape(md_data["title"] or tag_text)}</div>
                  {f'<div class="st-meta hw-text-line">{html.escape(header_meta)}</div>' if header_meta else ''}
                </header>
                """
            )

        if md_data["sections"]:
            if md_data["preamble"]:
                pre, sp = preprocess_markdown_block(md_data["preamble"], avail_mm, font_size_pt, flow=flow, style=style)
                all_splits.extend(sp)
                blocks_html.append(f'<div class="hw-answer">{render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + 3)}</div>')
            for sec in md_data["sections"]:
                pre, sp = preprocess_markdown_block(sec["markdown"], avail_mm, font_size_pt, flow=flow, style=style)
                for s in sp:
                    all_splits.append({"question": sec["num"], **s})
                ans_html = render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + sec["num"] * 19)
                m_src = re.search(r"(\d+)\s*题|补充题\s*\(?(\d+)\)?", sec["heading"])
                short_no = f"{sec['num']}."
                if m_src:
                    ref_n = m_src.group(1) or m_src.group(2)
                    short_no = f"补({ref_n})" if "补充" in sec["heading"] else f"{sec['num']}. ({ref_n})"
                blocks_html.append(
                    f"""
                    <section class="st-question">
                      <div class="hw-answer">
                        <div class="st-qnum">{html.escape(short_no)}</div>
                        {ans_html}
                      </div>
                    </section>
                    """
                )
        else:
            pre, sp = preprocess_markdown_block(md_data["raw_body"], avail_mm, font_size_pt, flow=flow, style=style)
            all_splits.extend(sp)
            blocks_html.append(f'<div class="hw-answer">{render_markdown_with_math_placeholders(pre, pack=pack, seed=seed + 7)}</div>')

    content_html = "\n".join(blocks_html)
    if mode == "pdf_overlay" and layout_spec is not None:
        p0 = (layout_spec.get("pages") or [{}])[0]
        pw0 = float(p0.get("width_mm", paper_spec.width_mm))
        ph0 = float(p0.get("height_mm", paper_spec.height_mm))
        paper_css = (
            f"@page {{ size: {pw0:.2f}mm {ph0:.2f}mm; margin: 0 !important; }}\n"
            f"html, body {{ margin: 0 !important; padding: 0 !important; background: transparent !important; }}\n"
        )
        root_width_css = f"{pw0:.2f}mm"
    else:
        paper_css = paper_to_css(paper_spec)
        root_width_css = f"{avail_mm:.2f}mm"
    wobble_scale = round(max(0.0, min(2.0, wobble)) * 0.55, 2)
    wobble_filter_val = "url(#hw-pen-wobble)" if wobble_scale > 0.05 else "none"

    cfg = {
        "mathJitter": round(math_jitter, 3),
        "textJitter": round(text_jitter, 3),
        "cnSx": 0.86,
        "cnSy": 1.07,
        "cnSkew": round(-2.2 + slant_add, 2),
        "cnMr": -1.25,
        "usePersonalCjk": bool(use_personal_cjk),
        "quirksEnabled": bool(quirks),
        "elasticAmp": round(float(elastic_amp), 4),
        "layoutMode": layout,
        "gridLock": bool(paper_spec.grid_lock),
        "personalUrl": "/pack_glyphs.json" if use_personal_glyphs else "",
        "synthCjk": synth_cjk_bank or {},
        "baseFontPx": round(font_size_pt * 96.0 / 72.0, 2),
        "seed": int(seed),
        "captureScene": bool(capture_scene),
        "pageWidthMm": float(paper_spec.width_mm),
        "pageHeightMm": float(paper_spec.height_mm),
        "marginsMm": [float(x) for x in paper_spec.margins_mm],
        "presetObstacles": (layout_spec.get("obstacles", []) if layout_spec else []),
    }

    cn_fb = "JasonHandwriting7-ExtraLight.woff2" if (FONTS_DIR / "JasonHandwriting7-ExtraLight.woff2").exists() else "hong-lei-zhuo-shu.otf"

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<link rel="stylesheet" href="/assets/katex/katex.min.css" />
<link rel="stylesheet" href="/css/handwrite.css" />
<style>
@media screen {{
  main#page-root {{
    width: {root_width_css};
    margin: 0 auto;
  }}
}}
@media print {{
  main#page-root {{
    width: auto;
    margin: 0;
  }}
}}
@font-face {{
  font-family: 'HW-CJK';
  src: url('/assets/fonts/hong-lei-zhuo-shu.otf') format('opentype');
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
}}
@font-face {{
  font-family: 'HW-CJK-FB';
  src: url('/assets/fonts/{cn_fb}') format('{'woff2' if cn_fb.endswith('.woff2') else 'opentype'}');
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
}}
@font-face {{
  font-family: 'HW-Latin';
  src: url('/assets/fonts/ReenieBeanie.ttf') format('truetype');
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
  unicode-range: U+0020-007E, U+00A0-00FF;
}}

:root {{
  --ink-primary: {ink_primary};
  --ink-secondary: {ink_secondary};
  --ink-red: {ink_red};
  --hw-font-size: {font_size_pt}pt;
  --hw-qnum-size: {round(font_size_pt * 1.12, 1)}pt;
  --hw-wobble-filter: {wobble_filter_val};
}}

{paper_css}
</style>
</head>
<body>

<svg width="0" height="0" style="position:absolute;left:-9999px;top:-9999px;" aria-hidden="true">
  <defs>
    <filter id="hw-pen-wobble" x="-8%" y="-8%" width="116%" height="116%">
      <feTurbulence type="fractalNoise" baseFrequency="0.052" numOctaves="2" seed="{seed % 997}" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="{wobble_scale}" xChannelSelector="R" yChannelSelector="G" />
    </filter>
  </defs>
</svg>

<main id="page-root">
{content_html}
</main>

<script src="/assets/katex/katex.min.js"></script>
<script>window.__INKTWIN_CFG__ = {json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))};</script>
<script src="/js/handwrite_dom.js"></script>
</body>
</html>
"""
    return html_doc, all_splits

