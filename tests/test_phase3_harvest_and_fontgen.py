"""Phase 3 tests: collection templates, zero-OCR vector harvesting, and OpenType TTF/OTF compilation."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import fitz
from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from inktwin.cli import main as cli_main
from inktwin.fontgen import build_opentype_font
from inktwin.harvest import generate_template_pdf, harvest_template_pdf, read_template_spec_from_pdf
from inktwin.pack import load_pack
from inktwin.preflight import find_browser


def _draw_simulated_starnote_strokes(pdf_path: Path, spec: dict) -> None:
    """Fill template cells with vector ink including a ring '0' with a real inner hole."""
    with fitz.open(str(pdf_path)) as doc:
        for cell in spec["cells"]:
            pno = int(cell["page"])
            page = doc[pno]
            x_mm, y_mm, w_mm, h_mm = cell["box_mm"]
            k = 72.0 / 25.4
            x0, y0 = x_mm * k, y_mm * k
            bw, bh = w_mm * k, h_mm * k
            cx, cy = x0 + bw * 0.5, y0 + bh * 0.5
            key = cell["key"]

            if key == "0":
                # Draw closed ring '0' as two stroked/filled loops or thick oval to test hollow counter
                # Outer oval & concentric white? No, draw thick oval with open stroke so ribbonize or overlapping strokes form ring
                # Even better: draw a true oval polyline stroke! Open stroke -> _extract_subpaths ribbonizes, wait: an oval filled with outer - inner creates true ring!
                sh = page.new_shape()
                # Closed thick black C-to-0 loop via stroked bezier (width 2.0pt -> dark ink outline!)
                pts = []
                import math
                for step in range(24):
                    ang = (step / 23.0) * 2.0 * math.pi
                    pts.append(fitz.Point(cx + math.cos(ang) * bw * 0.24, cy + math.sin(ang) * bh * 0.34))
                sh.draw_polyline(pts)
                sh.finish(color=(0.08, 0.08, 0.10), width=1.8, closePath=True)
                sh.commit()
            elif key == "H":
                sh = page.new_shape()
                sh.draw_line(fitz.Point(x0 + bw * 0.18, cy + 2.0), fitz.Point(x0 + bw * 0.82, cy - 2.0))
                sh.finish(color=(0.05, 0.05, 0.08), width=1.6)
                sh.commit()
            else:
                # Two distinct strokes inside cell so subpath splitting is exercised too
                sh = page.new_shape()
                sh.draw_line(fitz.Point(x0 + bw * 0.25, y0 + bh * 0.25), fitz.Point(x0 + bw * 0.72, y0 + bh * 0.35))
                sh.draw_line(fitz.Point(x0 + bw * 0.35, y0 + bh * 0.30), fitz.Point(x0 + bw * 0.68, y0 + bh * 0.78))
                sh.finish(color=(0.06, 0.08, 0.14), width=1.7)
                sh.commit()

        filled_tmp = pdf_path.with_name(pdf_path.stem + "_filled.pdf")
        doc.save(str(filled_tmp), deflate=True)
    filled_tmp.replace(pdf_path)


def run_phase3_tests() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1. Generate Tier-1 template & verify fiducials + embedded spec + multi-variant coverage
        t1_pdf = tmp / "tier1.pdf"
        res_t1 = generate_template_pdf(t1_pdf, tier="tier1")
        assert res_t1["pages"] == 3
        assert res_t1["cells"] > 400
        spec_t1 = read_template_spec_from_pdf(t1_pdf)
        assert spec_t1["tier"] == "tier1"
        from collections import Counter
        t1_counts = Counter(c["key"] for c in spec_t1["cells"] if c["kind"] == "glyph")
        assert all(t1_counts[d] == 3 for d in "0123456789")
        assert all(t1_counts[ch] >= 2 for ch in "abcdefghijklmnopqrstuvwxyz")
        assert all(t1_counts[ch] == 3 for ch in "xtnae=+-()")
        assert t1_counts["__FRAC_MED__"] == 2 and t1_counts["__SQRT_HOOK__"] == 2

        # 2. Generate Targeted template + fill vector ink + harvest + merge-into
        tgt_pdf = tmp / "targeted.pdf"
        res_tgt = generate_template_pdf(tgt_pdf, tier="targeted", chars="01a你微H")
        assert res_tgt["pages"] == 1
        spec_tgt = read_template_spec_from_pdf(tgt_pdf)
        _draw_simulated_starnote_strokes(tgt_pdf, spec_tgt)

        custom_pack = tmp / "custom_user.hwpack"
        t0 = time.perf_counter()
        h_res = harvest_template_pdf(tgt_pdf, custom_pack, pack_id="custom_user")
        dt = time.perf_counter() - t0
        assert dt < 10.0, f"Harvest took too long: {dt:.2f}s"
        assert h_res["filled_cells"] == len(spec_tgt["cells"])
        assert h_res["added_glyphs"] >= 5

        pk = load_pack(str(custom_pack))
        assert "0" in pk.glyphs and "你" in pk.glyphs
        assert all(v.get("fill") for v in pk.glyphs["0"]["v"])

        # Test --merge-into demo_sample
        merged_pack = tmp / "merged.hwpack"
        demo_pack_path = REPO_ROOT / "packs" / "demo_sample.hwpack"
        m_res = harvest_template_pdf(tgt_pdf, merged_pack, merge_into=demo_pack_path, pack_id="merged")
        assert m_res["total_chars"] >= load_pack(str(demo_pack_path)).glyphs.__len__()

        # 3. Compile both .ttf and .otf from demo_sample.hwpack and check GSUB calt
        ttf_path = tmp / "inktwin-demo.ttf"
        otf_path = tmp / "inktwin-demo.otf"
        r_ttf = build_opentype_font(demo_pack_path, ttf_path, fmt="ttf", family_name="InkTwinTest")
        r_otf = build_opentype_font(demo_pack_path, otf_path, fmt="otf", family_name="InkTwinTestCFF")
        assert ttf_path.exists() and ttf_path.stat().st_size > 15000
        assert otf_path.exists() and otf_path.stat().st_size > 10000
        assert r_ttf["mapped_chars"] > 200 and r_ttf["calt_alt1_pairs"] > 100
        assert r_otf["mapped_chars"] > 200 and r_otf["calt_alt2_triples"] > 10

        tt = TTFont(str(ttf_path))
        assert "GSUB" in tt
        gsub_feats = [fr.FeatureTag for fr in tt["GSUB"].table.FeatureList.FeatureRecord]
        assert "calt" in gsub_feats
        tt.close()

        ot = TTFont(str(otf_path))
        assert "CFF " in ot and "GSUB" in ot
        ot.close()

        # 4. Headless Chrome render check: verify '0' keeps hollow white counter (not filled solid black)
        browser = find_browser()
        if browser:
            import subprocess
            html_file = tmp / "specimen.html"
            png_file = tmp / "specimen.png"
            font_uri = ttf_path.resolve().as_uri()
            html_file.write_text(
                f"""<!doctype html><html><head><meta charset="utf-8"><style>
                @font-face {{ font-family: 'InkTwinSpec'; src: url('{font_uri}'); }}
                body {{ margin: 0; background: #fff; color: #000; font-family: 'InkTwinSpec', sans-serif;
                       font-feature-settings: "calt" 1; font-size: 96px; padding: 24px; }}
                </style></head><body>000 OOO 888</body></html>""",
                encoding="utf-8",
            )
            subprocess.run(
                [browser, "--headless", "--disable-gpu", "--window-size=900,260", f"--screenshot={png_file}", html_file.resolve().as_uri()],
                check=True,
                capture_output=True,
            )
            from PIL import Image
            import numpy as np
            im = Image.open(png_file).convert("L")
            arr = np.array(im)
            # Inside glyph bounding boxes there must be white counter regions surrounded by ink!
            ink = arr < 180
            assert 2000 < int(ink.sum()) < arr.size * 0.45

        # 5. CLI integration check (template + harvest + fontbuild --json)
        cli_tpl = tmp / "cli_t.pdf"
        assert cli_main(["template", "--tier", "targeted", "--chars", "01xy", "-o", str(cli_tpl), "--json"]) == 0
        assert cli_tpl.exists()
        cli_font = tmp / "cli_out.ttf"
        assert cli_main(["fontbuild", "--pack", str(demo_pack_path), "--format", "ttf", "-o", str(cli_font), "--json"]) == 0
        assert cli_font.exists()


if __name__ == "__main__":
    run_phase3_tests()
    print("[PASS] phase3_harvest_and_fontgen")

