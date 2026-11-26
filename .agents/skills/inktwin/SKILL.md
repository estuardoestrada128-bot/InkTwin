---
name: inktwin
description: Offline personal handwriting vector twin engine — render Markdown/DOCX/PDF worksheets to handwritten PDFs with KaTeX formula splitting, handplot sketches, physical pen textures, InkScene editing, .hwpack harvest, and OpenType font compilation.
---

# InkTwin Skill

Use `inktwin` (or `python -m inktwin`) for offline personal-handwriting PDF generation, blank worksheet overlay, and handwriting pack authoring.

## Standard Workflow

1. **Preflight first**: Check pack coverage, missing glyphs, and formula line-split points before rendering:
   ```bash
   inktwin preflight input.md --pack demo_sample --json
   ```
2. **Render**:
   - Standalone lined/grid/composition PDF: `inktwin render input.md -o out.pdf --pack demo_sample --paper ruled8 --pen-type gel --json`
   - Blank exam/worksheet overlay: `inktwin render input.md --overlay blank.pdf -o overlaid.pdf --pen-type fountain --nib-mm 0.45 --save-scene scene_dir --json`
3. **Inspect & Self-Heal**:
   - `inktwin inspect scene_dir --json`
   - `inktwin scene tighten scene_dir` / `inktwin scene drift scene_dir`
4. **Interactive Studio**: `inktwin studio --port 8765`

## References

- [CLI & API Reference](references/cli_reference.md)
- [Handplot Sketch YAML Schema](references/handplot_schema.md)

