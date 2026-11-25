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

            # 2. POST /api/preflight
            md = "## 1.\n计算 $E(X)=\\frac{1}{2}$，测试函数微积分。"
            pf = _http_post_json(base_url + "api/preflight", {"markdown": md, "pack": "demo_sample"})
            assert pf["ok"] and "cjk" in pf["report"]

            # 3. POST /api/template & download PDF
            tpl = _http_post_json(base_url + "api/template", {"tier": "targeted", "chars": "01xy"})
            assert tpl["ok"] and tpl["result"]["pages"] == 1
            tpl_bytes = _http_get(base_url.rstrip("/") + tpl["pdf_url"])
            assert tpl_bytes.startswith(b"%PDF")

            # 4. POST /api/layout-detect on generated blank exam
            blank_pdf = _create_blank_exam(tmp / "blank.pdf")
            blank_b64 = base64.b64encode(blank_pdf.read_bytes()).decode("ascii")
            ld = _http_post_json(base_url + "api/layout-detect", {"pdf_b64": blank_b64})
            assert ld["ok"] and len(ld["layout"]["boxes"]) == 4

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

