"""Phase 4 tests: blank PDF overlay, physics symbols, handplot sketches, hand-drawn tables & pen physics."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from inktwin.cli import main as cli_main
from inktwin.overlay import detect_pdf_layout
from inktwin.pack import load_pack
from inktwin.pen import PEN_TYPES, apply_pen_physics_to_rgba
from inktwin.scene import inspect_scene, load_inkscene


def create_sample_blank_exam_pdf(out_pdf: Path) -> Path:
    """Create a realistic 2-page blank math & physics worksheet PDF with obstacles and blank slots."""
    mm = 72.0 / 25.4
    doc = fitz.open()

    # Page 0: Exam header + Q1 + Q2 (with diagram obstacle)
    p0 = doc.new_page(width=210.0 * mm, height=297.0 * mm)
    p0.insert_text(fitz.Point(45 * mm, 18 * mm), "University Physics & Calculus Homework", fontsize=14, fontname="helv", color=(0, 0, 0))
    p0.draw_line(fitz.Point(16 * mm, 22 * mm), fitz.Point(194 * mm, 22 * mm), color=(0.2, 0.2, 0.2), width=0.8)

    p0.insert_text(fitz.Point(16 * mm, 32 * mm), "Problem 1. Evaluate the definite integral and draw the shaded region.", fontsize=10.5, fontname="helv")
    # Blank slot ~95 mm down to y=132mm
    p0.insert_text(fitz.Point(16 * mm, 136 * mm), "Problem 2. A block slides on a smooth incline. Draw forces & compute acceleration.", fontsize=10.5, fontname="helv")
    # Diagram obstacle beside Q2 stem (y=142..170 mm)
    sh = p0.new_shape()
    sh.draw_polyline([fitz.Point(145 * mm, 168 * mm), fitz.Point(188 * mm, 168 * mm), fitz.Point(188 * mm, 144 * mm), fitz.Point(145 * mm, 168 * mm)])
    sh.finish(color=(0.15, 0.15, 0.15), width=1.0)
    sh.commit()
    # Blank slot below diagram y=174..280 mm

    # Page 1: Q3 + Q4
    p1 = doc.new_page(width=210.0 * mm, height=297.0 * mm)
    p1.insert_text(fitz.Point(16 * mm, 22 * mm), "Problem 3. Solve the wave operator relation and summarize data in a table.", fontsize=10.5, fontname="helv")
    # Blank slot y=28..148 mm
    p1.insert_text(fitz.Point(16 * mm, 152 * mm), "Problem 4. Find the domain interval on the real line.", fontsize=10.5, fontname="helv")
    # Blank slot y=158..280 mm

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_pdf), deflate=True)
    doc.close()
    return out_pdf


SAMPLE_SOLUTION_MD = r"""---
title: Physics & Calculus Solutions
---

## Problem 1
由 $\int_0^2 x^2\,dx = \frac{8}{3}$，面积如图：

```handplot
type: func
width_mm: 64
height_mm: 40
x_range: [-0.5, 2.6]
y_range: [-0.5, 4.5]
shade:
  expr: "x**2"
  from: 0
  to: 2
curves:
  - expr: "x**2"
    domain: [-0.4, 2.15]
    label: "x^2"
points:
  - x: 2
    y: 4
    label: "P"
    dashed_proj: true
```

## Problem 2
由牛顿第二定律 $\vec{F} = m\vec{a}$，重力分解 $\vec{F}_g = m\vec{g}$ 且 $N \perp \text{斜面}$：

```handplot
type: fbd
width_mm: 52
height_mm: 40
body:
  shape: incline
  incline_deg: 28
  label: "m"
forces:
  - angle_deg: -90
    len: 1.0
    label: "mg"
  - angle_deg: 118
    len: 0.85
    label: "N"
    color: "#c6332f"
```

## Problem 3
量子化关系 $L = n\hbar$，梯度与旋度满足 $\nabla \times \vec{E} = -\frac{\partial \vec{B}}{\partial t}$，闭合积分 $\oint \vec{B}\cdot d\vec{\ell} \propto I$。

| t(s) | x(m) | v(m/s) |
| :--- | :--- | :--- |
| 0 | 0 | 2 |
| 1 | 3 | 4 |

## Problem 4
解集满足 $x \in [-2, 3)$，且 $A \subseteq B, \emptyset \notin A$：

```handplot
type: number_line
width_mm: 88
height_mm: 20
range: [-3, 4]
label: "x"
intervals:
  - from: -2
    to: 3
    open_left: false
    open_right: true
    shade: true
```
"""


def _count_visible_font_spans(pdf_path: Path) -> int:
    vis = 0
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            for b in page.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        if sp["text"].strip() and sp["size"] > 1:
                            vis += 1
    return vis


def run_phase4_tests() -> None:
    # 1. Verify physics symbols injected cleanly into demo_sample pack without breaking CJK counts
    pk = load_pack("demo_sample")
    for sym in ("ℏ", "ℓ", "∮", "∇", "∝", "⊥", "⟨", "⟩", "⊆", "∉"):
        assert sym in pk.glyphs, f"Missing physics symbol: {sym}"

    # 2. Verify physical millimeter pen texture differentiation & determinism
    stroke_rgba = np.zeros((320, 320, 4), dtype=np.uint8)
    stroke_rgba[110:155, 40:280] = [20, 24, 34, 255]
    gel_1 = apply_pen_physics_to_rgba(stroke_rgba, pen_type="gel", dpi=300, seed=77)
    gel_2 = apply_pen_physics_to_rgba(stroke_rgba, pen_type="gel", dpi=300, seed=77)
    assert np.array_equal(gel_1, gel_2), "Pen texture must be deterministic for fixed seed"

    pencil = apply_pen_physics_to_rgba(stroke_rgba, pen_type="pencil", dpi=300, seed=77)
    ball = apply_pen_physics_to_rgba(stroke_rgba, pen_type="ballpoint", dpi=300, seed=77)
    fount = apply_pen_physics_to_rgba(stroke_rgba, pen_type="fountain", dpi=300, seed=77)
    assert float(pencil[115:150, 45:275, 3].std()) > float(gel_1[115:150, 45:275, 3].std()) + 5.0
    assert float(ball[115:150, 45:275, 3].std()) > float(gel_1[115:150, 45:275, 3].std())
    assert int((fount[:, :, 3] > 5).sum()) > int((stroke_rgba[:, :, 3] > 5).sum()), "Fountain should exhibit micro-bleed halo"

    nib_thin = apply_pen_physics_to_rgba(stroke_rgba, pen_type="gel", dpi=300, seed=77, nib_mm=0.38)
    nib_thick = apply_pen_physics_to_rgba(stroke_rgba, pen_type="gel", dpi=300, seed=77, nib_mm=0.7)
    assert int(nib_thin[:, :, 3].sum()) < int(gel_1[:, :, 3].sum()) < int(nib_thick[:, :, 3].sum()), "Nib mm must thin/thicken stroke cleanly"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        exam_pdf = create_sample_blank_exam_pdf(tmp / "blank_exam.pdf")
        layout = detect_pdf_layout(exam_pdf)
        assert len(layout["pages"]) == 2
        assert len(layout["boxes"]) == 4, f"Expected 4 slots, got {len(layout['boxes'])}: {layout['boxes']}"
        assert [b["qnum"] for b in layout["boxes"]] == [1, 2, 3, 4]

        md_path = tmp / "sol.md"
        md_path.write_text(SAMPLE_SOLUTION_MD, encoding="utf-8")
        out_pdf = tmp / "exam_overlaid.pdf"
        sc_dir = tmp / "exam_scene"
        layers_dir = tmp / "layers"

        rc = cli_main([
            "render", str(md_path),
            "--pack", "demo_sample",
            "--overlay", str(exam_pdf),
            "--save-scene", str(sc_dir),
            "--export-layers", str(layers_dir),
            "-o", str(out_pdf),
            "--json",
        ])
        assert rc == 0 and out_pdf.exists()

        # Pure SVG layer & printed prompt checks
        assert (layers_dir / "page-1.svg").exists() and (layers_dir / "page-2.svg").exists()
        assert (layers_dir / "page-1.png").exists() and (layers_dir / "page-2.png").exists()
        with Image.open(layers_dir / "page-1.png") as im1:
            assert im1.mode == "RGBA"

        # SVG converted standalone PDF layer must have zero visible font spans!
        svg_only_pdf = tmp / "svg_layer_only.pdf"
        with fitz.open(stream=(layers_dir / "page-1.svg").read_text(encoding="utf-8").encode("utf-8"), filetype="svg") as sdoc:
            svg_only_pdf.write_bytes(sdoc.convert_to_pdf())
        assert _count_visible_font_spans(svg_only_pdf) == 0, "Answer layer leaked font spans!"

        # Overlaid PDF keeps original 4 prompt lines (4 spans) intact across 2 pages!
        assert _count_visible_font_spans(out_pdf) == _count_visible_font_spans(exam_pdf)

        scene, edits, _ = load_inkscene(sc_dir)
        chs = {g.get("ch") for g in scene["glyphs"]}
        assert "sketch" in chs, "handplot sketch missing from captured InkScene"
        assert "table_rule" in chs, "hand-drawn table rules missing from captured InkScene"
        assert "vec_arrow" in chs, "\\vec arrow accent missing from captured InkScene"

        rep = inspect_scene(scene, edits)
        assert rep["anomaly_counts"].get("prompt_collision", 0) == 0, f"Prompt collision detected: {rep['anomalies']}"
        assert rep["anomaly_counts"].get("outside_margin", 0) == 0, f"Margin violation detected: {rep['anomalies']}"

        # Also test textured pencil overlay via CLI
        pencil_pdf = tmp / "exam_pencil.pdf"
        rc2 = cli_main(["scene", "render", str(sc_dir), "-o", str(pencil_pdf), "--pen-type", "pencil"])
        assert rc2 == 0 and pencil_pdf.exists()


if __name__ == "__main__":
    run_phase4_tests()
    print("[PASS] phase4_overlay_sketch_pen")

