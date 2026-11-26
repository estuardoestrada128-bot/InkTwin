# InkTwin CLI Reference

| Command | Purpose | Key Options |
| :--- | :--- | :--- |
| `inktwin doctor --json` | Check Chrome/Edge, PyMuPDF, fontTools, assets, packs | `--json` |
| `inktwin packs --json` | List local and public `.hwpack` archives | `--json` |
| `inktwin preflight <md>` | Analyze CJK coverage, `missing_glyphs`, and formula splits | `--pack`, `--paper`, `--page-size`, `--font-size`, `--json` |
| `inktwin render <md>` | Render handwritten PDF (and optional `InkScene` / layers) | `-o`, `--pack`, `--paper`, `--page-size`, `--ink`, `--pen-type`, `--nib-mm`, `--mood`, `--overlay`, `--layout-json`, `--save-scene`, `--export-layers`, `--seed`, `--no-elastic`, `--json` |
| `inktwin inspect <scene>` | Audit anomalies (`outside_margin`, `prompt_collision`, `left_edges_aligned`) | `--max-gap`, `--json` |
| `inktwin scene ...` | Non-destructive scene ops (`render`, `edit`, `tighten`, `drift`, `undo`, `reset`, `editor`) | `--format`, `--pen-type`, `--transparent` |
| `inktwin template` | Generate printable/tablet calibration sheet PDF | `--tier full\|tier1\|tier2\|targeted`, `--chars`, `--from-md`, `-p`, `-o` |
| `inktwin harvest <pdf>` | Extract vector ink from filled template PDF into `.hwpack` | `-o`, `--id`, `--merge-into`, `--json` |
| `inktwin fontbuild` | Compile `.hwpack` to OpenType `.ttf`/`.otf` with `GSUB calt` | `-p`, `--format ttf\|otf`, `--name`, `--no-augment`, `-o` |
| `inktwin layout-detect <pdf>` | Detect question prompts, obstacles, and blank slots | `-o`, `--min-slot-mm`, `--json` |
| `inktwin studio` | Launch offline HTTP workbench | `--port`, `--workdir`, `--no-browser`, `--reload` |
| `inktwin mcp` | Run optional MCP stdio server (requires `inktwin[mcp]`) | — |

