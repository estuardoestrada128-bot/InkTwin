#!/usr/bin/env python3
"""Phase 2 integration tests: InkScene capture, inspect, tighten, drift, reroll, pen, undo, fast SVG/PDF/PNG export, editor HTTP."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from inktwin.scene import (
    apply_drift,
    apply_tighten,
    export_scene,
    inspect_scene,
    load_inkscene,
    make_atomic_ops,
    next_batch_id,
    save_edits,
    undo_last_batch,
)
from inktwin.scene.editor_server import start_editor_server


def run_phase2_tests() -> None:
    sample_md_text = (
        "# 概率论练习\n\n"
        "## 1. 第 1 题\n"
        "1. 解：$E(X) = \\frac{1}{2}$，$Var(X) = \\frac{1}{12}$。\n"
        "因为各自独立，所以 $E(X + Y) = E(X) + E(Y) = 1$。\n"
        "同理 $P(A \\cap B) = P(A)P(B)$。\n"
        "最终结果：$S = \\int_0^1 x^2 dx = \\frac{1}{3}$。\n"
    )

    with tempfile.TemporaryDirectory(prefix="inktwin_p2_") as td:
        td_p = Path(td)
        md_p = td_p / "sample.md"
        md_p.write_text(sample_md_text, encoding="utf-8")
        pdf_p = td_p / "base.pdf"
        scene_dir = td_p / "sample.inkscene"

        # 1. Capture InkScene during render
        r = subprocess.run(
            [
                sys.executable, "-m", "inktwin", "render", str(md_p),
                "-o", str(pdf_p), "--pack", "andy", "--no-docx", "--paper", "grid",
                "--save-scene", str(scene_dir), "--json",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        assert r.returncode == 0, r.stderr
        assert (scene_dir / "scene.json").exists() and (scene_dir / "edits.json").exists()

        scene, edits, _ = load_inkscene(scene_dir)
        assert len(scene["glyphs"]) > 30, len(scene["glyphs"])
        assert len(scene["lines"]) >= 4, len(scene["lines"])
        assert len(scene["variants_pool"]) >= 10, len(scene["variants_pool"])

        # 2. Inspect baseline
        insp = inspect_scene(scene, edits)
        assert insp["ok"] and insp["glyph_count"] == len(scene["glyphs"])

        # 3. Apply drift (and verify double-drift is refused without --force)
        edits1, added_d = apply_drift(scene, edits, mode="natural", amount=1.0, seed=7)
        assert len(added_d) >= 2, added_d
        refused = False
        try:
            apply_drift(scene, edits1, mode="natural", force=False)
        except RuntimeError:
            refused = True
        assert refused, "Expected double-drift to raise RuntimeError without force=True"

        # 4. Reroll variant + red pen highlight on line p0l1, then undo test
        target_line = scene["lines"][1]["id"]
        # Pick a glyph with >= 2 variants in pool
        reroll_gid = next(
            g["id"] for g in scene["glyphs"]
            if len(scene["variants_pool"].get(g.get("ch", ""), [])) >= 2
        )
        old_d = next(g["paths"][0]["d"] for g in scene["glyphs"] if g["id"] == reroll_gid)
        b = next_batch_id(edits1)
        op_rr = make_atomic_ops("reroll", ids=[reroll_gid], batch=b)
        op_red = make_atomic_ops("pen", line=target_line, color="red", batch=b + 1)
        edits2 = edits1 + [op_rr, op_red]
        save_edits(scene_dir, edits2)

        from inktwin.scene import resolve_scene_state
        res_state = resolve_scene_state(scene, edits2)
        new_d = next(g["paths"][0]["d"] for g in res_state["glyphs"] if g["id"] == reroll_gid)
        assert new_d != old_d, "Expected reroll to change vector path!"
        assert all(g["color"] == "#c6332f" for g in res_state["glyphs"] if g["line"] == target_line)

        undone, rem_n = undo_last_batch(edits2)
        assert rem_n == 1 and len(undone) == len(edits1) + 1

        # 5. Fast SVG->PDF export (< 500ms target, 0 font spans!) & transparent PNG export
        out_edited = td_p / "edited.pdf"
        ex_pdf = export_scene(scene_dir, out_pdf=out_edited, fmt="pdf")
        assert ex_pdf["ok"] and out_edited.exists()
        assert ex_pdf["elapsed_ms"] < 1500.0, ex_pdf
        with fitz.open(out_edited) as doc:
            vis = sum(
                1 for p in doc for b_ in p.get_text("dict")["blocks"]
                for ln in b_.get("lines", []) for sp in ln.get("spans", [])
                if sp["text"].strip() and sp["size"] > 1
            )
            assert vis == 0, f"Expected 0 visible font spans in scene PDF, got {vis}"

        png_dir = td_p / "pngs"
        ex_png = export_scene(scene_dir, out_dir=png_dir, fmt="png", dpi=180, transparent=True)
        png_path = Path(ex_png["outputs"][0])
        assert png_path.exists()
        im = Image.open(png_path).convert("RGBA")
        alpha = np.array(im)[:, :, 3]
        assert (alpha == 0).sum() > 10000 and (alpha > 200).sum() > 200, "Transparent PNG alpha invalid!"

        # 6. Editor HTTP endpoints test
        server, _port, base_url = start_editor_server(scene_dir, port=0)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            st = json.loads(urllib.request.urlopen(f"{base_url}api/state", timeout=5).read().decode("utf-8"))
            assert st["ok"] and st["pages"] >= 1
            pg = json.loads(urllib.request.urlopen(f"{base_url}api/page_svg/0", timeout=5).read().decode("utf-8"))
            assert pg["ok"] and "<svg" in pg["svg"]
            req = urllib.request.Request(
                f"{base_url}api/ops",
                data=json.dumps({"op": "move", "ids": [reroll_gid], "dx": 1.2, "dy": -0.5}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            op_res = json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8"))
            assert op_res["ok"] and op_res["added"] == 1
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    run_phase2_tests()
    print("PASS  phase2_scene_and_editor")

