"""Student-style handwriting cleaner and natural flow preprocessor."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .splitter import estimate_latex_width_mm, split_long_formula


def _is_single_display_math(line: str) -> str | None:
    s = line.strip()
    if s.startswith("$$") and s.endswith("$$") and len(s) > 4 and s.count("$$") == 2:
        return s[2:-2].strip()
    return None


def apply_concise_student_style(md_block: str) -> str:
    """Strip textbook boilerplate phrases, keep math symbols, enforce student punctuation."""
    text = md_block
    text = re.sub(r"\\text\{\s*其[他它]\s*\.?\s*\}", r"\\text{else}", text)
    text = re.sub(r"^\s*\*\*\s*(?:解|证|证明)\s*[：:]\s*\*\*\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:解|证|证明)\s*[：:]\s*", "", text, flags=re.M)
    text = re.sub(r"[。，,\s]*证毕[。.]?\s*$", "", text, flags=re.M)

    boilerplate_lines = [
        r"^\s*由(?:数学)?期望的线性(?:可加)?性质(?:及[^\n$]{0,16})?[：:，,]?\s*$",
        r"^\s*由方差(?:的)?性质(?:及[^\n$]{0,16})?[：:，,]?\s*$",
        r"^\s*由\s*\$[^$\n]+\$\s*(?:相互)?独立(?:及方差性质)?[：:，,]?\s*$",
        r"^\s*由定理\s*[0-9\.]+（[^）]+）[：:，,]?\s*$",
        r"^\s*由定理\s*[0-9\.]+[：:，,]?\s*$",
        r"^\s*由此计算\s*\$[^$\n]+\$\s*[：:]\s*$",
        r"^\s*求导得\s*\$[^$\n]+\$\s*的概率密度(?:函数)?为[：:]\s*$",
        r"^\s*(?:先)?求边际密度[：:]\s*$",
        r"^\s*(?:故|因此)相关系数为[：:]\s*$",
        r"^\s*由协方差的双线性性质[：:]\s*$",
        r"^\s*又由不相关性[，,]得[：:]\s*$",
        r"^\s*由全期望公式[，,]?得?[：:]\s*$",
    ]
    for pat in boilerplate_lines:
        text = re.sub(pat, "", text, flags=re.M)

    text = re.sub(r"(?:^|(?<=[，。；\s]))(?:因为|由于|因)\s*", "∵ ", text)
    text = re.sub(r"(?:^|(?<=[，。；\s]))(?:所以|因此|从而|故)[，,：:]?\s*", "∴ ", text)
    text = re.sub(r"又\s*∵", "又∵", text)
    text = re.sub(r"由题意[，,：:]\s*", "", text)
    text = re.sub(r"由对称性[，,：:]\s*", "同理, ", text)

    # Student convention: strip trailing sentence full stops on math derivation lines
    cleaned_lines = []
    for ln in text.split("\n"):
        if "$" in ln or "∵" in ln or "∴" in ln or "⇒" in ln:
            ln = re.sub(r"[。．]+\s*$", "", ln)
        cleaned_lines.append(ln)
    text = "\n".join(cleaned_lines)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _estimate_prefix_mm(prefix: str, font_size_pt: float) -> float:
    em_mm = font_size_pt * 0.352778
    plain = re.sub(r"<[^>]+>", "", prefix)
    w = 0.0
    i = 0
    while i < len(plain):
        if plain[i] == "$":
            end = plain.find("$", i + 1)
            if end != -1:
                w += estimate_latex_width_mm(plain[i + 1 : end], font_size_pt)
                i = end + 1
                continue
        ch = plain[i]
        if "\u4e00" <= ch <= "\u9fff":
            w += 1.32 * em_mm
        elif ch.isspace():
            w += 0.45 * em_mm
        else:
            w += 0.60 * em_mm
        i += 1
    return w


def preprocess_markdown_block(
    md_block: str,
    avail_width_mm: float = 175.0,
    font_size_pt: float = 12.0,
    flow: str = "natural",
    style: str = "concise",
) -> Tuple[str, List[Dict]]:
    """
    Preprocess Markdown answer block into student-natural handwriting flow with
    operator-aware long formula chain splitting.
    """
    text = md_block.replace("\r\n", "\n").replace("&emsp;", "  ")
    text = re.sub(r"^\s*---+\s*$", "", text, flags=re.M)
    if style in ("concise", "andy"):
        text = apply_concise_student_style(text)

    if flow == "natural":
        lines = text.split("\n")
        sub_label_re = re.compile(r"^\s*\*\*\s*\(([a-zA-Z0-9]+)\)\s*\*\*\s*$")
        solve_label_re = re.compile(r"^\s*\*\*\s*(解|证|证明)\s*[：:]\s*\*\*\s*$")

        pass1: List[str] = []
        i = 0
        while i < len(lines):
            cur = lines[i]
            m_sol = solve_label_re.match(cur)
            if m_sol:
                label = f"{m_sol.group(1)}："
                nxt_idx = i + 1
                while nxt_idx < len(lines) and not lines[nxt_idx].strip():
                    nxt_idx += 1
                if nxt_idx < len(lines) and sub_label_re.match(lines[nxt_idx]):
                    i = nxt_idx
                    continue
                if nxt_idx < len(lines):
                    nxt = lines[nxt_idx].strip()
                    dm = _is_single_display_math(nxt)
                    if dm:
                        pass1.append(f'<span class="hw-lead">{label}</span> $\\displaystyle {dm}$')
                        i = nxt_idx + 1
                        continue
                    if nxt and not nxt.startswith(("-", "*", "1.", "2.", "#")):
                        pass1.append(f'<span class="hw-lead">{label}</span> {nxt}')
                        i = nxt_idx + 1
                        continue
                pass1.append(f'<span class="hw-lead">{label}</span>')
                i += 1
                continue

            m_sub = sub_label_re.match(cur)
            if m_sub:
                tag = m_sub.group(1)
                label_html = f'<span class="hw-sublabel">{tag}.</span>'
                nxt_idx = i + 1
                while nxt_idx < len(lines) and not lines[nxt_idx].strip():
                    nxt_idx += 1
                if nxt_idx < len(lines):
                    nxt = lines[nxt_idx].strip()
                    dm = _is_single_display_math(nxt)
                    if dm:
                        pass1.append(f"{label_html} $\\displaystyle {dm}$")
                        i = nxt_idx + 1
                        continue
                    m_sol2 = solve_label_re.match(nxt)
                    if m_sol2:
                        nxt_idx2 = nxt_idx + 1
                        while nxt_idx2 < len(lines) and not lines[nxt_idx2].strip():
                            nxt_idx2 += 1
                        if nxt_idx2 < len(lines):
                            nxt2 = lines[nxt_idx2].strip()
                            dm2 = _is_single_display_math(nxt2)
                            if dm2:
                                pass1.append(f"{label_html} $\\displaystyle {dm2}$")
                                i = nxt_idx2 + 1
                                continue
                            if nxt2 and not nxt2.startswith(("-", "*", "1.")):
                                pass1.append(f"{label_html} {nxt2}")
                                i = nxt_idx2 + 1
                                continue
                    if nxt and not nxt.startswith(("-", "*", "1.", "2.", "#")):
                        pass1.append(f"{label_html} {nxt}")
                        i = nxt_idx + 1
                        continue
                pass1.append(f'<div class="hw-subhead">{label_html}</div>')
                i += 1
                continue

            pass1.append(cur)
            i += 1

        pass2: List[str] = []
        i = 0
        while i < len(pass1):
            cur = pass1[i]
            cur_s = cur.strip()
            if not cur_s:
                pass2.append("")
                i += 1
                continue

            # Merge short lead-in words with following formula
            if (
                not cur_s.startswith(("-", "*", "1.", "2.", "3.", "4.", "<div", "$$"))
                and cur_s.endswith(("：", ":", "，", "得", "有", "为"))
                and len(cur_s) <= 36
            ):
                nxt_idx = i + 1
                while nxt_idx < len(pass1) and not pass1[nxt_idx].strip():
                    nxt_idx += 1
                if nxt_idx < len(pass1):
                    dm = _is_single_display_math(pass1[nxt_idx])
                    if dm and len(dm) <= 78 and "\\begin{aligned}" not in dm:
                        pass2.append(f"{cur_s} $\\displaystyle {dm}$")
                        i = nxt_idx + 1
                        continue

            # Place two adjacent short display formulas side-by-side
            dm1 = _is_single_display_math(cur)
            if dm1 and len(dm1) <= 52 and "\\begin{" not in dm1 and not cur.startswith("  "):
                nxt_idx = i + 1
                while nxt_idx < len(pass1) and not pass1[nxt_idx].strip():
                    nxt_idx += 1
                if nxt_idx < len(pass1):
                    dm2 = _is_single_display_math(pass1[nxt_idx])
                    if (
                        dm2
                        and len(dm2) <= 52
                        and (len(dm1) + len(dm2) <= 88)
                        and "\\begin{" not in dm2
                        and not pass1[nxt_idx].startswith("  ")
                        and not dm2.lstrip().startswith(("=", "\\approx", "\\le", "\\ge", "\\implies"))
                    ):
                        pass2.append(
                            f'$\\displaystyle {dm1}$<span class="hw-eq-gap"></span>$\\displaystyle {dm2}$'
                        )
                        i = nxt_idx + 1
                        continue

            pass2.append(cur)
            i += 1

        lines_out = pass2
    else:
        lines_out = text.split("\n")

    # Apply AST-aware Formula Chain Splitter on long inline/display formulas
    formula_splits: List[Dict] = []
    # Account for handwriting leftDrift (~18-24mm) and wider personal stroke proportions
    safe_avail = max(76.0, avail_width_mm - 26.0)
    final_lines: List[str] = []

    for ln_idx, ln in enumerate(lines_out):
        s = ln.strip()
        # Prevent Markdown `<ol>` list pseudo `::marker` on inline numbered steps like `1. 解：...`
        # so digits render via personal vector SVGs (`visible_font_spans == 0`).
        m_num_lead = re.match(r"^(\s*)(\d+)\.\s+(.*)$", ln)
        if m_num_lead:
            ln = f'{m_num_lead.group(1)}<span class="hw-lead">{m_num_lead.group(2)}.</span> {m_num_lead.group(3)}'
            s = ln.strip()

        # Case A: standalone $$...$$
        dm = _is_single_display_math(s)
        if dm:
            pieces = split_long_formula(dm, safe_avail, safe_avail * 0.88, font_size_pt)
            if len(pieces) > 1:
                formula_splits.append({"line": ln_idx, "pieces": len(pieces), "preview": dm[:48]})
                parts = [f"$$\\displaystyle {pieces[0]}$$"]
                for pk in pieces[1:]:
                    parts.append(f'<div class="hw-chain-cont">$\\displaystyle {pk}$</div>')
                final_lines.extend(parts)
                continue
            final_lines.append(ln)
            continue

        # Case B: line ending with a balanced $...$ formula (handle escaped backslashes \\$ accurately)
        ln_r = ln.rstrip()
        ln_masked = ln_r.replace("\\\\", "\x00BSLASH\x00")
        math_matches = list(re.finditer(r"(?<!\\)\$([^\$\n]+?)(?<!\\)\$", ln_masked))
        m_last = math_matches[-1] if (math_matches and math_matches[-1].end() == len(ln_masked)) else None
        if m_last and not ln_masked[: m_last.start()].strip().endswith('<span class="hw-eq-gap"></span>'):
            prefix = ln_masked[: m_last.start()].replace("\x00BSLASH\x00", "\\\\")
            inner_tex = m_last.group(1).replace("\x00BSLASH\x00", "\\\\")
            prefix_w = _estimate_prefix_mm(prefix, font_size_pt)
            total_w = prefix_w + estimate_latex_width_mm(inner_tex, font_size_pt)
            if total_w > safe_avail * 0.96:
                first_w = max(48.0, safe_avail - prefix_w - 4.0)
                clean_inner = re.sub(r"^\\displaystyle\s+", "", inner_tex.strip())
                pieces = split_long_formula(clean_inner, first_w, safe_avail * 0.88, font_size_pt)
                if len(pieces) > 1:
                    formula_splits.append({"line": ln_idx, "pieces": len(pieces), "preview": clean_inner[:48]})
                    has_disp = "\\displaystyle" in inner_tex
                    disp_pfx = "\\displaystyle " if has_disp else ""
                    final_lines.append(f"{prefix}${disp_pfx}{pieces[0]}$")
                    for pk in pieces[1:]:
                        final_lines.append(f'<div class="hw-chain-cont">$\\displaystyle {pk}$</div>')
                    continue

        final_lines.append(ln)

    text = "\n".join(final_lines)
    text = re.sub(r"==(.+?)==", r'<span class="hw-red">\1</span>', text)
    return text, formula_splits

