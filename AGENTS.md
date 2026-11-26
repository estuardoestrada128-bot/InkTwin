# InkTwin — Agent & Developer Guide

InkTwin is an offline personal-handwriting vector PDF synthesis engine, calibration pack builder (`.hwpack`), OpenType font compiler (`.ttf` / `.otf` with `GSUB calt`), blank-worksheet PDF overlay engine, and offline Studio workbench.

## Architecture

- `src/inktwin/geom/` — Shared deterministic geometric primitives (`ribbon.py`, `elastic.py`).
- `src/inktwin/pack/` — `.hwpack` ZIP schema (`schema.py`), fast manifest-only `list_packs()`, migration (`migrate.py`), quality inspection/backup (`quality.py`), procedural physics symbol bank (`physics_symbols.py`).
- `src/inktwin/synth/` — Stroke skeleton medians loader (LRU-cached) and personal CJK stroke/radical synthesis (`cjk_synth.py`).
- `src/inktwin/mathsplit/` — LaTeX tokenizer, cleaner (`cleaner.py`), and width-aware line splitter (`splitter.py`).
- `src/inktwin/renderer/` — HTML/KaTeX builder (`html_builder.py`), headless Chromium PDF capture & `InkScene` uploader (`pdf_exporter.py`), client DOM replacement (`js/handwrite_dom.js`).
- `src/inktwin/scene/` — Non-destructive `InkScene` (`scene.json` + `edits.json`) schema, inspector, vector SVG/PDF exporter, and standalone editor server (`editor_server.py`, `scene_editor.html`).
- `src/inktwin/overlay/` — DOCX question matching (`docx_parser.py`) and blank PDF box/obstacle layout detection + center-anchored mark stamping (`pdf_overlay.py`).
- `src/inktwin/pen/` — Page-anchored mm-coordinate physical pen texture filters (`gel`, `ballpoint`, `fountain`, `pencil`) with tight ink bbox cropping and multi-page parallelism (`physics.py`).
- `src/inktwin/harvest/` — Vector template generator (`templates.py`), PDF drawing harvester (`harvester.py`), and vectorized EDT stroke-width calibrator (`calibrator.py`).
- `src/inktwin/studio/` — Modular offline HTTP workbench (`state.py`, `routes_pack.py`, `_http_sec.py`, `server.py`, `studio.html`).
- `src/inktwin/cli.py` & `src/inktwin/mcp_server.py` — Unified CLI (`inktwin`) and optional MCP stdio server (`inktwin mcp`).

## Privacy & Packaging Boundaries

- Public packs live in `src/inktwin/packs/` (`demo_sample.hwpack`).
- Personal `.hwpack` files belong strictly in git-ignored `packs/local/` or `$INKTWIN_LOCAL_PACKS_DIR` / platform user data dirs.
- Never commit personal names, local filesystem paths, personal glyph caches, or `.mcp.json` secrets. `evals/grade.py` audits all tracked directories (`privacy_clean: true`).

## Verification Commands

```bash
python tests/test_unit_core.py
python tests/run_tests.py
python -m inktwin doctor --json
```

