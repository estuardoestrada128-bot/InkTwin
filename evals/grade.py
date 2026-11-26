#!/usr/bin/env python3
"""Outcome-driven quality & privacy benchmark suite for InkTwin (evals/grade.py)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz
import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from inktwin import PACKS_DIR
from inktwin.cli import main as cli_main
from inktwin.fontgen import build_opentype_font
from inktwin.pack import load_pack
from inktwin.scene import inspect_scene, load_inkscene

_MATH_CHAIN_MD = r"""---
title: Probability & Calculus Homework W10
---
# 第10周作业

## 1.
设随机变量 $(X, Y)$ 独立，求相关系数：
$$\rho_{UV} = \frac{Cov(U,V)}{\sqrt{Var(U)Var(V)}} = \frac{\sigma_Z^2}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}} = \frac{Var(Z)+Cov(Z,Y)+Cov(X,Z)+Cov(X,Y)}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}}$$

## 2.
$$f_U(u) = \begin{cases} 4ue^{-2u}, & u \ge 0 \\ 0, & \text{else} \end{cases}$$
∴ $E(U) = \int_0^\infty 4u^2 e^{-2u}\,du = 1$，$D(U) = \frac{1}{2}$

```handplot
type: func
width_mm: 62
height_mm: 38
x_range: [-0.3, 3.0]
y_range: [-0.2, 1.0]
shade:
  expr: "4*x*exp(-2*x)"
  from: 0
  to: 2.2
curves:
  - expr: "4*x*exp(-2*x)"
    domain: [0.0, 2.8]
    label: "f(u)"
```
"""

_COMPO_MD = """# 短文随笔

数学之美在于简洁与对称。微分把曲线化直，积分积微成著；从牛顿与莱布尼茨出发，经由柯西与黎曼的严格化，分析学成为自然科学最锋利的语言。
"""

_OVERLAY_EXAM_MD = r"""---
title: Physics & Calculus Solutions
---

## Problem 1
由 $\int_0^2 x^2\,dx = \frac{8}{3}$，积分面积如下：

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
牛顿第二定律 $\vec{F} = m\vec{a}$，重力分解 $\vec{F}_g = m\vec{g}$ 且 $N \perp \text{斜面}$：

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
量子化角动量 $L = n\hbar$，旋度方程 $\nabla \times \vec{E} = -\frac{\partial \vec{B}}{\partial t}$，闭合环路 $\oint \vec{B}\cdot d\vec{\ell} \propto I$。

| t(s) | x(m) | v(m/s) |
| :--- | :--- | :--- |
| 0 | 0 | 2 |
| 1 | 3 | 4 |

## Problem 4
解集为 $x \in [-2, 3)$，且 $A \subseteq B, \emptyset \notin A$：

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


def _count_visible_font_spans(pdf_path: Path, ignore_texts: Tuple[str, ...] = ()) -> int:
    vis = 0
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            for b in page.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        txt = sp["text"].strip()
                        if txt and sp["size"] > 1 and txt not in ignore_texts:
                            vis += 1
    return vis


def _raster_ink_stats(pdf_path: Path, page_idx: int = 0, dpi: int = 150) -> dict:
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_idx].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    dark = lum < 175.0
    ratio = float(dark.mean())
    med_lum = float(np.median(lum[dark])) if dark.any() else 255.0
    return {"ink_ratio": round(ratio, 5), "median_dark_lum": round(med_lum, 1)}


def _create_blank_exam(out_pdf: Path) -> Path:
    mm = 72.0 / 25.4
    doc = fitz.open()
    p0 = doc.new_page(width=210.0 * mm, height=297.0 * mm)
    p0.insert_text(fitz.Point(44 * mm, 18 * mm), "University Physics & Calculus Homework", fontsize=14, fontname="helv")
    p0.draw_line(fitz.Point(16 * mm, 22 * mm), fitz.Point(194 * mm, 22 * mm), color=(0.2, 0.2, 0.2), width=0.8)
    p0.insert_text(fitz.Point(16 * mm, 32 * mm), "Problem 1. Evaluate the definite integral and sketch the shaded region.", fontsize=10.5, fontname="helv")
    p0.insert_text(fitz.Point(16 * mm, 136 * mm), "Problem 2. Draw the free-body diagram of a block on a smooth incline.", fontsize=10.5, fontname="helv")
    sh = p0.new_shape()
    sh.draw_polyline([fitz.Point(145 * mm, 168 * mm), fitz.Point(188 * mm, 168 * mm), fitz.Point(188 * mm, 144 * mm), fitz.Point(145 * mm, 168 * mm)])
    sh.finish(color=(0.15, 0.15, 0.15), width=1.0)
    sh.commit()

    p1 = doc.new_page(width=210.0 * mm, height=297.0 * mm)
    p1.insert_text(fitz.Point(16 * mm, 22 * mm), "Problem 3. State wave relations and record measurements in a table.", fontsize=10.5, fontname="helv")
    p1.insert_text(fitz.Point(16 * mm, 152 * mm), "Problem 4. Plot the solution interval on the real line.", fontsize=10.5, fontname="helv")
    doc.save(str(out_pdf), deflate=True)
    doc.close()
    return out_pdf


def audit_privacy() -> dict:
    """Ensure demo_sample.hwpack, .gitignore, and publishable files contain no machine/personal paths."""
    hits: List[str] = []
    gi = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8", errors="ignore")
    if "packs/local/" not in gi:
        hits.append(".gitignore missing packs/local/")

    demo = load_pack("demo_sample")
    if demo.manifest.get("privacy") != "public-anonymized":
        hits.append(f"demo_sample.hwpack unexpected privacy flag: {demo.manifest.get('privacy')!r}")

    abs_user_re = re.compile(r"(?:[A-Za-z]:[/\\]Users[/\\][^/\\]+|/Users/[^/]+)")
    extra_needles = [s.strip() for s in os.environ.get("INKTWIN_PRIVACY_NEEDLES", "").split(",") if s.strip()]
    scan_roots = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "install.py",
        REPO_ROOT / "pyproject.toml",
        PACKS_DIR / "demo_sample.hwpack",
        REPO_ROOT / "src",
        REPO_ROOT / "tests",
        REPO_ROOT / "evals",
        REPO_ROOT / "docs",
        REPO_ROOT / ".agents",
        REPO_ROOT / "plugin",
    ]
    self_path = Path(__file__).resolve()
    for root in scan_roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for fp in files:
            if fp.resolve() == self_path or "__pycache__" in fp.parts or fp.suffix in (".png", ".ttf", ".otf", ".woff2", ".gz"):
                continue
            try:
                txt = fp.read_bytes().decode("utf-8", errors="ignore")
            except Exception:
                continue
            if abs_user_re.search(txt):
                hits.append(f"{fp.relative_to(REPO_ROOT)} contains hardcoded user home path")
            for nd in extra_needles:
                if nd in txt:
                    hits.append(f"{fp.relative_to(REPO_ROOT)} contains '{nd}'")
    return {"privacy_clean": len(hits) == 0, "hits": hits}


def run_eval(pack_name: str = "demo_sample", gallery_dir: Optional[Path] = None) -> dict:
    cases: List[dict] = []
    if gallery_dir is not None:
        gallery_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="inktwin_eval_") as td:
        tmp = Path(td)

        # Case 1: math_chain_grid
        md1 = tmp / "c1.md"
        md1.write_text(_MATH_CHAIN_MD, encoding="utf-8")
        pdf1 = tmp / "c1.pdf"
        sc1 = tmp / "c1_sc"
        rc1 = cli_main(["render", str(md1), "--pack", pack_name, "--no-docx", "--paper", "grid", "--tag", "W10", "--save-scene", str(sc1), "-o", str(pdf1), "--json"])
        sp1 = _count_visible_font_spans(pdf1)
        st1 = _raster_ink_stats(pdf1)
        s1, e1, _ = load_inkscene(sc1)
        in1 = inspect_scene(s1, e1)["anomaly_counts"]
        ok1 = rc1 == 0 and sp1 == 0 and 0.003 < st1["ink_ratio"] < 0.35 and st1["median_dark_lum"] < 145 and in1.get("outside_margin", 0) == 0
        cases.append({"name": "math_chain_grid", "passed": ok1, "visible_font_spans": sp1, "anomalies": in1, **st1})
        if gallery_dir is not None:
            with fitz.open(str(pdf1)) as d:
                d[0].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_grid.png"))

        # Case 2: ruled8_blue_neat
        pdf2 = tmp / "c2.pdf"
        sc2 = tmp / "c2_sc"
        rc2 = cli_main(["render", str(md1), "--pack", pack_name, "--no-docx", "--paper", "ruled8", "--mood", "neat", "--ink", "blue", "--save-scene", str(sc2), "-o", str(pdf2), "--json"])
        sp2 = _count_visible_font_spans(pdf2, ignore_texts=("No. ________", "Date ____ / ____"))
        st2 = _raster_ink_stats(pdf2)
        s2, e2, _ = load_inkscene(sc2)
        in2 = inspect_scene(s2, e2)["anomaly_counts"]
        ok2 = rc2 == 0 and sp2 == 0 and 0.003 < st2["ink_ratio"] < 0.35 and in2.get("outside_margin", 0) == 0
        cases.append({"name": "ruled8_blue_neat", "passed": ok2, "visible_font_spans": sp2, "anomalies": in2, **st2})
        if gallery_dir is not None:
            with fitz.open(str(pdf2)) as d:
                d[0].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_ruled8.png"))

        # Case 3: compo_grid_lock
        md3 = tmp / "c3.md"
        md3.write_text(_COMPO_MD, encoding="utf-8")
        pdf3 = tmp / "c3.pdf"
        sc3 = tmp / "c3_sc"
        rc3 = cli_main(["render", str(md3), "--pack", pack_name, "--no-docx", "--paper", "compo", "--save-scene", str(sc3), "-o", str(pdf3), "--json"])
        sp3 = _count_visible_font_spans(pdf3)
        st3 = _raster_ink_stats(pdf3)
        s3, e3, _ = load_inkscene(sc3)
        in3 = inspect_scene(s3, e3)["anomaly_counts"]
        ok3 = (
            rc3 == 0
            and sp3 == 0
            and 0.002 < st3["ink_ratio"] < 0.35
            and in3.get("outside_margin", 0) == 0
            and in3.get("prompt_collision", 0) == 0
        )
        cases.append({"name": "compo_grid_lock", "passed": ok3, "visible_font_spans": sp3, "anomalies": in3, **st3})
        if gallery_dir is not None:
            with fitz.open(str(pdf3)) as d:
                d[0].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_compo.png"))

        # Case 4: pdf_exam_overlay
        blank4 = _create_blank_exam(tmp / "blank.pdf")
        md4 = tmp / "c4.md"
        md4.write_text(_OVERLAY_EXAM_MD, encoding="utf-8")
        pdf4 = tmp / "c4_overlaid.pdf"
        sc4 = tmp / "c4_sc"
        lay4 = tmp / "c4_layers"
        rc4 = cli_main(["render", str(md4), "--pack", pack_name, "--overlay", str(blank4), "--save-scene", str(sc4), "--export-layers", str(lay4), "-o", str(pdf4), "--json"])
        svg_p0 = tmp / "svg0.pdf"
        with fitz.open(stream=(lay4 / "page-1.svg").read_bytes(), filetype="svg") as sd:
            svg_p0.write_bytes(sd.convert_to_pdf())
        sp4_layer = _count_visible_font_spans(svg_p0)
        sp4_match = _count_visible_font_spans(pdf4) == _count_visible_font_spans(blank4)
        s4, e4, _ = load_inkscene(sc4)
        in4 = inspect_scene(s4, e4)["anomaly_counts"]
        ok4 = rc4 == 0 and sp4_layer == 0 and sp4_match and in4.get("prompt_collision", 0) == 0 and in4.get("outside_margin", 0) == 0
        cases.append({"name": "pdf_exam_overlay", "passed": ok4, "layer_font_spans": sp4_layer, "prompts_preserved": sp4_match, "anomalies": in4})
        if gallery_dir is not None:
            with fitz.open(str(pdf4)) as d:
                d[0].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_overlay_p0.png"))
                d[1].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_overlay_p1.png"))

        # Case 5: pen_physics_pencil
        pdf5 = tmp / "c5_pencil.pdf"
        lay5 = tmp / "c5_layers"
        rc5 = cli_main(["render", str(md1), "--pack", pack_name, "--no-docx", "--paper", "white", "--pen-type", "pencil", "--export-layers", str(lay5), "-o", str(pdf5), "--json"])
        with Image.open(lay5 / "page-1.png") as im5:
            arr5 = np.array(im5)
            alpha5 = arr5[:, :, 3]
            ink_px5 = alpha5[alpha5 > 10]
            porosity_std = float(ink_px5.std()) if ink_px5.size else 0.0
        ok5 = rc5 == 0 and porosity_std > 18.0
        cases.append({"name": "pen_physics_pencil", "passed": ok5, "graphite_alpha_std": round(porosity_std, 2)})
        if gallery_dir is not None:
            with fitz.open(str(pdf5)) as d:
                d[0].get_pixmap(dpi=160).save(str(gallery_dir / "showcase_pencil.png"))

        # Case 6: opentype_font_calt
        ttf6 = tmp / "eval.ttf"
        otf6 = tmp / "eval.otf"
        rf_t = build_opentype_font(pack_name, ttf6, fmt="ttf")
        rf_o = build_opentype_font(pack_name, otf6, fmt="otf")
        with TTFont(str(ttf6)) as tt:
            has_calt = "GSUB" in tt and "calt" in [r.FeatureTag for r in tt["GSUB"].table.FeatureList.FeatureRecord]
        ok6 = rf_t["mapped_chars"] > 250 and rf_o["mapped_chars"] > 250 and has_calt
        cases.append({"name": "opentype_font_calt", "passed": ok6, "ttf_chars": rf_t["mapped_chars"], "calt_pairs": rf_t["calt_alt1_pairs"], "has_calt": has_calt})

    priv = audit_privacy()
    passed_count = sum(1 for c in cases if c["passed"])
    all_ok = (passed_count == len(cases)) and priv["privacy_clean"]
    score = round(100.0 * passed_count / len(cases), 1) if priv["privacy_clean"] else 0.0
    return {
        "ok": all_ok,
        "pack": pack_name,
        "score": score,
        "passed": passed_count,
        "total": len(cases),
        "privacy": priv,
        "cases": cases,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="InkTwin outcome-driven quality & privacy grader")
    ap.add_argument("-p", "--pack", default="demo_sample")
    ap.add_argument("--gallery-dir", default=None, help="Optional directory to write anonymized showcase PNGs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    gdir = Path(args.gallery_dir).resolve() if args.gallery_dir else None
    rep = run_eval(pack_name=args.pack, gallery_dir=gdir)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        for c in rep["cases"]:
            tag = "PASS" if c["passed"] else "FAIL"
            print(f"{tag:5s} {c['name']}")
        print(f"Privacy clean: {rep['privacy']['privacy_clean']}")
        print(f"Score: {rep['score']} ({rep['passed']}/{rep['total']})")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

