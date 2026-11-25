#!/usr/bin/env python3
"""Install InkTwin skill wrapper for Antigravity, Claude Code, Codex, Cursor, or Gemini CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent


def detect_agent() -> str:
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if any(k.startswith("CODEX_") for k in env) or env.get("GEMINI_CLI") or env.get("CURSOR_TRACE_ID"):
        return "agents"
    return "agents"


def target_dirs(agent: str, scope: str, project: Path) -> list[Path]:
    base = Path.home() if scope == "user" else project
    mapping = {
        "claude": [base / ".claude" / "skills"],
        "agents": [base / ".agents" / "skills"],
        "both": [base / ".claude" / "skills", base / ".agents" / "skills"],
    }
    return [d / "inktwin" for d in mapping[agent]]


def build_skill_text() -> str:
    return f"""---
name: inktwin
description: 100% offline personal handwriting twin & math/physics vector PDF engine (.hwpack, .ttf/.otf, PDF overlay, handplot sketches, pen textures & Studio).
---

# InkTwin Skill Wrapper

Engine root: `{HERE}`

## Core Commands

```bash
# Diagnostics & available packs
python -m inktwin doctor --json
python -m inktwin packs --json

# Collect handwriting -> forge .hwpack -> compile OpenType .ttf/.otf
python -m inktwin template --tier tier1 -o tier1.pdf --json
python -m inktwin harvest filled.pdf -o packs/local/my.hwpack --json
python -m inktwin fontbuild --pack demo_sample --format ttf -o InkTwin.ttf --json

# Preflight & render (standalone, DOCX worksheet, or blank PDF overlay)
python -m inktwin preflight homework.md --pack demo_sample --json
python -m inktwin render homework.md -o out.pdf --pack demo_sample --paper ruled8 --mood normal --pen-type gel --save-scene scene_dir --json
python -m inktwin render homework.md --overlay blank_exam.pdf -o overlaid.pdf --pen-type fountain --export-layers layers_dir --json

# Inspect, self-heal, edit, and launch Web Studio
python -m inktwin inspect scene_dir --json
python -m inktwin scene tighten scene_dir
python -m inktwin scene drift scene_dir
python -m inktwin studio --port 8765
```
"""


def write_skill_wrapper(dst: Path, link_src: Path | None = None) -> str:
    if link_src is not None:
        if dst.is_symlink() or dst.exists():
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.symlink_to(link_src, target_is_directory=True)
            return "symlink"
        except OSError:
            pass
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "SKILL.md").write_text(build_skill_text(), encoding="utf-8")
    return "copied"


def main() -> int:
    ap = argparse.ArgumentParser(description="Install InkTwin AI Agent skill wrapper")
    ap.add_argument("--agent", choices=["auto", "agents", "claude", "both"], default="auto")
    ap.add_argument("--scope", choices=["project", "user"], default="project")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--link", action="store_true", help="Symlink a staged skill folder for development (falls back to copy if unprivileged)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    agent = detect_agent() if args.agent == "auto" else args.agent
    proj = Path(args.project).resolve()
    dsts = target_dirs(agent, args.scope, proj)
    mode = "dry-run"
    if not args.dry_run:
        link_stage = None
        if args.link:
            link_stage = HERE / "build" / "skill_inktwin"
            write_skill_wrapper(link_stage, link_src=None)
        modes = [write_skill_wrapper(d, link_src=link_stage) for d in dsts]
        mode = modes[0] if modes else "copied"

    print(json.dumps({
        "ok": True,
        "agent": agent,
        "scope": args.scope,
        "mode": mode,
        "dry_run": args.dry_run,
        "engine_root": str(HERE),
        "installed": [str(d) for d in dsts],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
