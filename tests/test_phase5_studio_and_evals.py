"""Phase 5 integration tests: InkTwin Studio HTTP workflow, install.py, and evals/grade.py."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))

from grade import _create_blank_exam, run_eval
from inktwin.studio import start_studio_server


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        assert resp.status == 200
        return resp.read()


def _http_post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def run_phase5_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="inktwin_p5_") as td:
        tmp = Path(td)
        server, port, base_url, st = start_studio_server(workdir=tmp / "studio_wd", port=0)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # 1. GET / & /api/state
            html = _http_get(base_url).decode("utf-8")
            assert "InkTwin Studio" in html
            state = json.loads(_http_get(base_url + "api/state").decode("utf-8"))
            assert state["ok"] and state["doctor"]["ok"]
            assert any(p["id"] == "demo_sample" for p in state["packs"])

            # 2. POST /api/preflight (including explicit paper & font_size)
            md = "## 1.\n计算 $E(X)=\\frac{1}{2}$，测试函数微积分。"
            pf = _http_post_json(
                base_url + "api/preflight",
                {"markdown": md, "pack": "demo_sample", "paper": "compo", "page_size": "A4", "font_size": 14},
            )
            assert pf["ok"] and pf["report"]["paper"] == "compo"

            # 3. GET /api/template/download (static Tier 1 & Tier 2) + POST /api/template (targeted, both explicit and from_md)
            t1_bytes = _http_get(base_url + "api/template/download?tier=tier1")
            t2_bytes = _http_get(base_url + "api/template/download?tier=tier2")
            assert t1_bytes.startswith(b"%PDF") and len(t1_bytes) > 5000
            assert t2_bytes.startswith(b"%PDF") and len(t2_bytes) > 3000

            tpl = _http_post_json(base_url + "api/template", {"tier": "targeted", "chars": "01xy"})
            assert tpl["ok"] and tpl["result"]["pages"] == 1
            tpl_bytes = _http_get(base_url.rstrip("/") + tpl["pdf_url"])
            assert tpl_bytes.startswith(b"%PDF")

            tpl_md = _http_post_json(
                base_url + "api/template",
                {"tier": "targeted", "chars": "", "markdown": "量子微积分熵变", "pack": "demo_sample"},
            )
            assert tpl_md["ok"] and tpl_md["result"]["pages"] >= 1

            # 4. POST /api/layout-detect on generated blank exam & overlay pen switch preserves background
            blank_pdf = _create_blank_exam(tmp / "blank.pdf")
            blank_b64 = base64.b64encode(blank_pdf.read_bytes()).decode("ascii")
            ld = _http_post_json(base_url + "api/layout-detect", {"pdf_b64": blank_b64})
            assert ld["ok"] and len(ld["layout"]["boxes"]) == 4

            ov_rn = _http_post_json(
                base_url + "api/render",
                {
                    "markdown": "## Problem 1\n$x=1$\n\n## Problem 2\n$y=2$\n",
                    "pack": "demo_sample",
                    "pen_type": "gel",
                    "overlay_pdf_b64": blank_b64,
                    "layout": ld["layout"],
                },
            )
            assert ov_rn["ok"] and len(ov_rn["previews"]) >= 1
            ov_repen = _http_post_json(base_url + "api/scene/ops", {"action": "repen", "pen_type": "fountain"})
            assert ov_repen["ok"] and "batches_count" in ov_repen
            import fitz
            ov_pdf_bytes = _http_get(base_url.rstrip("/") + ov_repen["pdf_url"])
            with fitz.open(stream=ov_pdf_bytes, filetype="pdf") as ov_doc:
                ov_text = "\n".join(pg.get_text() for pg in ov_doc)
                assert "Problem 1" in ov_text, "Overlay background PDF lost after scene/ops repen!"

            # 5. POST /api/render -> scene/ops -> scene/export -> fontbuild
            rn = _http_post_json(
                base_url + "api/render",
                {"markdown": md + "\n\n| a | b |\n| :--- | :--- |\n| 1 | 2 |\n", "pack": "demo_sample", "paper": "grid", "pen_type": "gel"},
            )
            assert rn["ok"] and len(rn["previews"]) >= 1 and rn["inspect"]["glyph_count"] > 5

            op_t = _http_post_json(base_url + "api/scene/ops", {"action": "tighten"})
            assert op_t["ok"]
            op_p = _http_post_json(base_url + "api/scene/ops", {"action": "repen", "pen_type": "pencil"})
            assert op_p["ok"]

            ex_svg = _http_post_json(base_url + "api/scene/export", {"format": "svg", "transparent": True})
            assert ex_svg["ok"] and len(ex_svg["files"]) >= 1
            svg_txt = _http_get(base_url.rstrip("/") + ex_svg["files"][0]).decode("utf-8")
            assert "<svg" in svg_txt

            fb = _http_post_json(base_url + "api/fontbuild", {"pack": "demo_sample", "format": "ttf"})
            assert fb["ok"] and fb["result"]["mapped_chars"] > 250
            ttf_bytes = _http_get(base_url.rstrip("/") + fb["font_url"])
            assert len(ttf_bytes) > 10000

            # 5b. POST /api/pack/inspect, read-only guard on public pack, clone-local & delete-variant
            insp_pub = _http_post_json(base_url + "api/pack/inspect", {"pack": "demo_sample"})
            assert insp_pub["ok"] and insp_pub["pack"]["editable"] is False
            assert len(insp_pub["items"]) > 200 and insp_pub["pack"]["median_t"] > 0

            # Attempting to delete from public demo_sample must be rejected (HTTP 400)
            import urllib.error
            try:
                _http_post_json(base_url + "api/pack/delete-variant", {"pack": "demo_sample", "char": "0", "vidx": 0})
                raise AssertionError("Expected read-only rejection on demo_sample")
            except urllib.error.HTTPError as he:
                assert he.code == 400

            clone_id = f"_tmp_p5_clone_{port}"
            clone_path = None
            try:
                cl = _http_post_json(base_url + "api/pack/clone-local", {"pack": "demo_sample", "new_id": clone_id})
                assert cl["ok"] and cl["id"] == clone_id
                clone_path = Path(cl["path"])
                assert clone_path.exists()

                insp_loc = _http_post_json(base_url + "api/pack/inspect", {"pack": clone_id})
                assert insp_loc["ok"] and insp_loc["pack"]["editable"] is True
                before_v = insp_loc["pack"]["total_variants"]
                target_item = next(it for it in insp_loc["items"] if len(it["variants"]) >= 2)
                del_res = _http_post_json(
                    base_url + "api/pack/delete-variant",
                    {"pack": clone_id, "char": target_item["char"], "vidx": 0},
                )
                assert del_res["ok"] and del_res["total_variants"] == before_v - 1
                assert del_res["deleted"]["remaining"] == len(target_item["variants"]) - 1
            finally:
                if clone_path and clone_path.exists():
                    clone_path.unlink()
        finally:
            for es in st.editor_servers:
                es.shutdown()
                es.server_close()
            server.shutdown()
            server.server_close()

        # 6. install.py dry-run & link flag check
        inst = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py"), "--agent", "both", "--scope", "project", "--project", str(tmp / "proj"), "--link", "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        ij = json.loads(inst.stdout)
        assert ij["ok"] and len(ij["installed"]) == 2

    # 7. evals/grade.py full outcome benchmark & privacy audit
    ev = run_eval(pack_name="demo_sample", gallery_dir=None)
    assert ev["ok"], f"evals/grade.py failed: {json.dumps(ev, ensure_ascii=False)}"
    assert ev["privacy"]["privacy_clean"] is True


if __name__ == "__main__":
    run_phase5_tests()
    print("[PASS] phase5_studio_and_evals")

