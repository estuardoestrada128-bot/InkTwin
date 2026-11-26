"""Fast (<5s) unit test suite covering core geometry, mathsplit, physics, EDT, security, and CLI contracts."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import fitz
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from inktwin.cli import main as cli_main
from inktwin.geom import chain_to_starnote_ribbon, elastic_deform_loops, pts_to_ribbon_d, ribbon_polygon_pts
from inktwin.harvest.calibrator import _edt_1d_rows, _exact_edt
from inktwin.mathsplit.cleaner import preprocess_markdown_block
from inktwin.mathsplit.splitter import estimate_latex_width_mm
from inktwin.overlay.pdf_overlay import apply_marks_to_scene, detect_pdf_layout
from inktwin.pack import list_packs, load_pack
from inktwin.pen.physics import PEN_TYPES, apply_pen_physics_to_rgba
from inktwin.preflight import run_preflight
from inktwin.renderer.html_builder import render_markdown_with_math_placeholders
from inktwin.studio import start_studio_server
from inktwin.studio._http_sec import MAX_REQUEST_BYTES
from inktwin.synth.cjk_synth import load_medians_db


def test_mathsplit_escape_and_scale() -> None:
    html_line = render_markdown_with_math_placeholders(r"Price \\$ and $x + \$y = z$.")
    assert "$y" in html_line
    assert 'class="math-i"' in html_line or 'data-tex=' in html_line

    cleaned, _splits = preprocess_markdown_block(r"由公式可得 $a + \$b = c$，价格 \$10。", style="full")
    assert "$10" in cleaned and "a + " in cleaned

    w12 = estimate_latex_width_mm(r"\frac{a+b}{c+d} + x^2", font_size_pt=12.0)
    w18 = estimate_latex_width_mm(r"\frac{a+b}{c+d} + x^2", font_size_pt=18.0)
    assert abs((w18 / w12) - 1.5) < 0.05, f"Expected linear pt scale, got w12={w12}, w18={w18}"


def test_geom_primitives() -> None:
    pts = [(0.0, 0.0), (15.0, 4.0), (30.0, 1.0)]
    poly = ribbon_polygon_pts(pts, half_w=1.2)
    assert len(poly) == 6
    d = pts_to_ribbon_d(pts, half_w=1.2)
    assert d.startswith("M") and d.endswith("Z")
    d2 = chain_to_starnote_ribbon(pts, base_r=1.68)
    assert d2.startswith("M") and d2.endswith("Z")

    loops = [[(10.0, 10.0), (90.0, 10.0), (50.0, 90.0)]]
    orig_copy = [list(lp) for lp in loops]
    v1 = elastic_deform_loops(loops, 100.0, 100.0, 1, "A")
    v1_again = elastic_deform_loops(loops, 100.0, 100.0, 1, "A")
    v2 = elastic_deform_loops(loops, 100.0, 100.0, 2, "A")
    assert loops == orig_copy
    assert v1 == v1_again and v1 != v2


def test_pen_physics_cropped_canvas() -> None:
    rgba = np.zeros((180, 220, 4), dtype=np.uint8)
    rgba[60:110, 70:140, 0] = 20
    rgba[60:110, 70:140, 1] = 35
    rgba[60:110, 70:140, 2] = 95
    rgba[60:110, 70:140, 3] = 240
    for pt in PEN_TYPES:
        out = apply_pen_physics_to_rgba(rgba, pen_type=pt, dpi=300, seed=123, nib_mm=0.38 if pt == "gel" else 0.7)
        assert out.shape == rgba.shape and out.dtype == np.uint8
        assert int(out[:20, :20, 3].sum()) == 0
        assert int(out[75:95, 85:115, 3].max()) > 30


def test_fast_edt_equivalence() -> None:
    rng = np.random.RandomState(42)
    mask = np.zeros((96, 110), dtype=bool)
    mask[12:82, 16:94] = rng.rand(70, 78) > 0.25
    mask[20:40, 30:70] = True
    f = np.where(mask, 1e9, 0.0)
    ref = np.sqrt(_edt_1d_rows(_edt_1d_rows(f.T).T))
    fast = _exact_edt(mask)
    diff = float(np.max(np.abs(fast - ref)))
    assert diff < 1e-5, f"EDT mismatch max_abs_diff={diff}"


def test_cached_medians_and_fast_list_packs() -> None:
    db1 = load_medians_db()
    db2 = load_medians_db()
    assert db1 is db2 and "的" in db1
    packs = list_packs()
    assert any(p["id"] == "demo_sample" and p["variants"] > 0 for p in packs)


def test_preflight_missing_glyphs_and_two_col_overlay() -> None:
    pk = load_pack("demo_sample")
    with tempfile.TemporaryDirectory(prefix="inktwin_u_ov_") as td:
        md_p = Path(td) / "pf.md"
        md_p.write_text("测试稀缺符号：ℵ ∰ 生字：龖", encoding="utf-8")
        pf = run_preflight(md_p, pk, no_docx=True)
        mg = pf.get("missing_glyphs")
        assert isinstance(mg, list) and "ℵ" in mg and "∰" in mg
        assert "龖" in (pf.get("cjk") or {}).get("unseen_chars", [])

        p = Path(td) / "twocol.pdf"
        doc = fitz.open()
        pg = doc.new_page(width=595, height=842)
        for y in (90, 260, 460):
            pg.insert_text((45, y), f"1. Left prompt y={y}", fontsize=11)
            pg.insert_text((325, y), f"2. Right prompt y={y}", fontsize=11)
        doc.save(str(p))
        doc.close()
        lay = detect_pdf_layout(p, min_slot_h_mm=15.0)
        assert len(lay["boxes"]) >= 2

        scene = {
            "glyphs": [
                {"id": "g0", "page": 0, "x": 100.0, "y": 50.0, "w": 20.0, "h": 10.0, "paths": [{"d": "M0 0 L10 10 Z"}]}
            ]
        }
        apply_marks_to_scene(
            scene,
            marks=[{"id": "m0", "page": 0, "x_mm": 100.0, "y_mm": 50.0, "w_mm": 20.0, "h_mm": 10.0, "scale": 2.0}],
        )
        g0 = scene["glyphs"][0]
        assert abs((g0["x"] + g0["w"] * 0.5) - 110.0) < 1e-4
        assert abs((g0["y"] + g0["h"] * 0.5) - 55.0) < 1e-4


def test_http_security_and_cli_errors() -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_typo = cli_main(["rendre", "missing.md", "--json"])
    assert rc_typo == 2
    err_obj = json.loads(buf.getvalue())
    assert err_obj["ok"] is False and "Did you mean 'render'" in err_obj["error"]

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc_missing = cli_main(["inspect", "C:/definitely_does_not_exist_scene_dir", "--json"])
    assert rc_missing == 1
    assert json.loads(buf2.getvalue())["ok"] is False

    with tempfile.TemporaryDirectory(prefix="inktwin_u_sec_") as td:
        server, port, base_url, _st = start_studio_server(workdir=Path(td), port=0)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            html_doc = urllib.request.urlopen(base_url, timeout=5).read().decode("utf-8")
            assert 'name="inktwin-token"' in html_doc

            def expect_status(url: str, method: str, headers: dict, body: bytes, want: int) -> None:
                req = urllib.request.Request(url, data=(body if method == "POST" else None), headers=headers, method=method)
                try:
                    urllib.request.urlopen(req, timeout=5)
                    raise AssertionError(f"Expected HTTP {want} for {method} {url} with {headers}")
                except urllib.error.HTTPError as he:
                    assert he.code == want, f"Expected HTTP {want}, got {he.code} for headers={headers}"

            expect_status(base_url + "api/state", "GET", {"Host": "evil.com"}, b"", 403)
            expect_status(base_url + "api/state", "GET", {"Origin": "https://evil.example"}, b"", 403)
            expect_status(base_url + "api/preflight", "POST", {"Content-Type": "text/plain"}, b"{}", 415)
            expect_status(
                base_url + "api/preflight",
                "POST",
                {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
                b"{}",
                403,
            )
            expect_status(
                base_url + "api/preflight",
                "POST",
                {"Content-Type": "application/json", "Content-Length": str(MAX_REQUEST_BYTES + 10)},
                b"",
                413,
            )
        finally:
            server.shutdown()
            server.server_close()
            _st.close()


def main() -> int:
    tests = [
        test_mathsplit_escape_and_scale,
        test_geom_primitives,
        test_pen_physics_cropped_canvas,
        test_fast_edt_equivalence,
        test_cached_medians_and_fast_list_packs,
        test_preflight_missing_glyphs_and_two_col_overlay,
        test_http_security_and_cli_errors,
    ]
    for fn in tests:
        fn()
        print(f"[PASS] {fn.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
