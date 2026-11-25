#!/usr/bin/env python3
"""Offline Phase 1 regression & integration test suite for InkTwin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from inktwin.mathsplit import estimate_latex_width_mm, preprocess_markdown_block, split_long_formula
from inktwin.pack import list_packs, load_pack
from inktwin.paper import resolve_paper, usable_width_mm
from inktwin.preflight import run_doctor

SAMPLE_HW_MD = r"""---
title: Probability & Calculus Homework W10
---
# 第10周作业

## 1.
设随机变量 $(X, Y)$ 独立，求相关系数：
$$\rho_{UV} = \frac{Cov(U,V)}{\sqrt{Var(U)Var(V)}} = \frac{\sigma_Z^2}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}} = \frac{Var(Z)+Cov(Z,Y)+Cov(X,Z)+Cov(X,Y)}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}}$$

## 2.
$$f_U(u) = \begin{cases} 4ue^{-2u}, & u \ge 0 \\ 0, & \text{else} \end{cases}$$
∴ $E(U) = \int_0^\infty 4u^2 e^{-2u}\,du = 1$，$D(U) = \frac{1}{2}$
"""


def _count_visible_font_spans(pdf_path: Path, ignore_texts: tuple[str, ...] = ()) -> int:
    vis = 0
    with fitz.open(pdf_path) as doc:
        for page in doc:
            for b in page.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        txt = sp["text"].strip()
                        if txt and sp["size"] > 1 and txt not in ignore_texts:
                            vis += 1
    return vis


def main() -> int:
    results = []

    def test(name: str, fn) -> None:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    def t_doctor_and_packs():
        doc = run_doctor()
        assert doc["ok"], f"doctor not ok: {doc}"
        packs = {p["id"]: p for p in list_packs()}
        assert "demo_sample" in packs, f"missing demo_sample: {packs.keys()}"
        demo = load_pack("demo_sample")
        assert demo.manifest.get("privacy") == "public-anonymized"
        assert len(demo.manifest.get("facets", {})) == 9, demo.manifest.get("facets")

    def t_formula_splitter():
        short = r"E(X) = \frac{1}{2}"
        assert len(split_long_formula(short, 160.0, 140.0)) == 1

        long_chain = (
            r"\rho_{UV} = \frac{Cov(U,V)}{\sqrt{Var(U)Var(V)}} = "
            r"\frac{\sigma_Z^2}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}} = "
            r"\frac{Var(Z)+Cov(Z,Y)+Cov(X,Z)+Cov(X,Y)}{\sqrt{(\sigma_X^2+\sigma_Z^2)(\sigma_Y^2+\sigma_Z^2)}}"
        )
        pieces = split_long_formula(long_chain, 95.0, 90.0)
        assert len(pieces) >= 2, f"expected >=2 pieces, got {pieces}"
        assert pieces[1].lstrip().startswith("="), f"piece 1 should start with '=': {pieces[1]}"

        # Never split inside \frac{a=1}{b=2} or \begin{cases}
        frac_eq = r"\frac{a=1+c}{b=2+d}"
        assert len(split_long_formula(frac_eq, 10.0, 10.0)) == 1
        cases_tex = r"f_U(u) = \begin{cases} 4ue^{-2u}, & u \ge 0 \\ 0, & \text{else} \end{cases}"
        assert len(split_long_formula(cases_tex, 30.0, 30.0)) == 1

        # Cleaner strips derivation trailing '。' and attaches lead-in
        cleaned, splits = preprocess_markdown_block(f"∴ ${long_chain}$。\n", avail_width_mm=105.0)
        assert "。" not in cleaned, cleaned
        assert len(splits) == 1, splits
        assert cleaned.startswith("∴ "), cleaned
        assert "hw-chain-cont" in cleaned, cleaned

    def t_paper_mm():
        r8 = resolve_paper("ruled8", "B5")
        assert r8.width_mm == 176.0 and r8.pitch_mm == 8.0 and r8.has_header
        assert 120.0 < usable_width_mm(r8) < 150.0

    def t_cli_preflight_and_renders():
        with tempfile.TemporaryDirectory(prefix="inktwin_t_") as td:
            out_dir = Path(td)
            md_path = out_dir / "sample_hw.md"
            md_path.write_text(SAMPLE_HW_MD, encoding="utf-8")

            # 1. preflight JSON
            pr = subprocess.run(
                [sys.executable, "-m", "inktwin", "preflight", str(md_path), "--pack", "demo_sample", "--no-docx", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            )
            assert pr.returncode == 0, pr.stderr
            pjson = json.loads(pr.stdout)
            assert pjson["cjk"]["real_in_pack"] >= 8, pjson

            # 2. grid standalone render -> visible_font_spans == 0
            p_grid = out_dir / "w10_grid.pdf"
            r1 = subprocess.run(
                [sys.executable, "-m", "inktwin", "render", str(md_path), "-o", str(p_grid), "--pack", "demo_sample", "--no-docx", "--paper", "grid", "--tag", "W10", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            )
            assert r1.returncode == 0, r1.stderr
            j1 = json.loads(r1.stdout)
            assert j1["ok"] and p_grid.exists() and _count_visible_font_spans(p_grid) == 0

            # 3. ruled8 standalone render -> visible_font_spans == 0
            p_r8 = out_dir / "w10_ruled8.pdf"
            r2 = subprocess.run(
                [sys.executable, "-m", "inktwin", "render", str(md_path), "-o", str(p_r8), "--pack", "demo_sample", "--no-docx", "--paper", "ruled8", "--mood", "neat", "--ink", "blue", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            )
            assert r2.returncode == 0, r2.stderr
            assert _count_visible_font_spans(p_r8, ignore_texts=("No. ________", "Date ____ / ____")) == 0

            # 4. optional local legacy skill bridge check when INKTWIN_LEGACY_BRIDGE is set
            legacy_env = os.environ.get("INKTWIN_LEGACY_BRIDGE", "").strip()
            if legacy_env and Path(legacy_env).exists():
                p_leg = out_dir / "w10_legacy.pdf"
                r4 = subprocess.run(
                    [sys.executable, legacy_env, str(md_path), "-o", str(p_leg), "--no-docx", "--paper", "grid", "--tag", "W10"],
                    cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                )
                assert r4.returncode == 0 and p_leg.exists() and _count_visible_font_spans(p_leg) == 0, r4.stderr

    test("doctor_and_packs", t_doctor_and_packs)
    test("formula_chain_splitter", t_formula_splitter)
    test("paper_mm_geometry", t_paper_mm)
    test("cli_preflight_render_and_bridge", t_cli_preflight_and_renders)

    from test_phase2_scene import run_phase2_tests
    test("phase2_inkscene_inspect_ops_editor", run_phase2_tests)

    from test_phase3_harvest_and_fontgen import run_phase3_tests
    test("phase3_harvest_and_fontgen", run_phase3_tests)

    from test_phase4_overlay_sketch_pen import run_phase4_tests
    test("phase4_overlay_sketch_pen", run_phase4_tests)

    from test_phase5_studio_and_evals import run_phase5_tests
    test("phase5_studio_and_evals", run_phase5_tests)

    fails = 0
    for name, ok, msg in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
            print(f"      {msg}")
    print(f"\n{len(results) - fails}/{len(results)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
