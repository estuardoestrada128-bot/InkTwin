"""Optional stdio MCP server wrapping core InkTwin workflows (`pip install inktwin[mcp]`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def run_mcp_server() -> int:
    """Launch FastMCP stdio server for InkTwin."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The 'mcp' optional dependency is not installed. Run `pip install inktwin[mcp]` to enable `inktwin mcp`."
        ) from exc

    from .overlay import detect_pdf_layout
    from .pack import list_packs, load_pack
    from .preflight import run_doctor, run_preflight
    from .scene import inspect_scene, load_inkscene

    mcp = FastMCP("inktwin")

    @mcp.tool()
    def inktwin_doctor() -> str:
        """Check InkTwin dependencies (Chromium, PyMuPDF, fontTools, assets, packs)."""
        return json.dumps(run_doctor(), ensure_ascii=False, indent=2)

    @mcp.tool()
    def inktwin_packs() -> str:
        """List all discovered local and public .hwpack archives."""
        return json.dumps({"ok": True, "packs": list_packs()}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def inktwin_preflight(
        markdown_text: str,
        pack: str = "demo_sample",
        paper: str = "grid",
        page_size: str = "A4",
        font_size: float = 12.0,
    ) -> str:
        """Preflight Markdown content against a handwriting pack before rendering."""
        import tempfile
        from .paper import resolve_paper

        pk = load_pack(pack or None)
        ps = resolve_paper(paper, page_size)
        with tempfile.TemporaryDirectory(prefix="inktwin_mcp_") as td:
            md_path = Path(td) / "input.md"
            md_path.write_text(markdown_text, encoding="utf-8")
            rep = run_preflight(md_path, pk, paper_spec=ps, font_size_pt=font_size, no_docx=True)
        return json.dumps({"ok": True, "report": rep}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def inktwin_render(
        input_md: str,
        output_pdf: Optional[str] = None,
        pack: str = "demo_sample",
        paper: str = "auto",
        ink: str = "black",
        pen_type: str = "gel",
        nib_mm: float = 0.5,
        mood: str = "normal",
        overlay_pdf: Optional[str] = None,
        save_scene: Optional[str] = None,
    ) -> str:
        """Render a Markdown homework/note file to handwritten PDF."""
        from .cli import main as cli_main
        import contextlib
        import io

        argv = [
            "render", input_md, "--pack", pack, "--paper", paper,
            "--ink", ink, "--pen-type", pen_type, "--nib-mm", str(nib_mm),
            "--mood", mood, "--json",
        ]
        if output_pdf:
            argv += ["-o", output_pdf]
        if overlay_pdf:
            argv += ["--overlay", overlay_pdf]
        else:
            argv += ["--no-docx"]
        if save_scene:
            argv += ["--save-scene", save_scene]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(argv)
        return buf.getvalue().strip()

    @mcp.tool()
    def inktwin_inspect(scene_dir: str, max_gap_ratio: float = 0.34) -> str:
        """Inspect an InkScene directory for layout anomalies."""
        scene, edits, _ = load_inkscene(Path(scene_dir))
        return json.dumps(inspect_scene(scene, edits, max_gap_ratio=max_gap_ratio), ensure_ascii=False, indent=2)

    @mcp.tool()
    def inktwin_template(
        output_pdf: str,
        tier: str = "tier1",
        chars: Optional[str] = None,
        from_md: Optional[str] = None,
        pack: str = "demo_sample",
    ) -> str:
        """Generate a calibration template PDF."""
        from .harvest import generate_template_pdf

        res = generate_template_pdf(
            Path(output_pdf),
            tier=tier,
            chars=chars,
            from_md=(Path(from_md) if from_md else None),
            pack=(load_pack(pack) if from_md else None),
        )
        return json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def inktwin_layout_detect(blank_pdf: str, min_slot_mm: float = 15.0) -> str:
        """Detect prompts, obstacles, and blank answer slots in a blank worksheet PDF."""
        return json.dumps({"ok": True, **detect_pdf_layout(Path(blank_pdf), min_slot_h_mm=min_slot_mm)}, ensure_ascii=False, indent=2)

    mcp.run()
    return 0
