"""AST-aware LaTeX width estimator and top-level operator Formula Chain Splitter."""

from __future__ import annotations

import math
import re
from typing import List, Tuple

# Operators safe to split on at depth 0: Priority 1 = relational/chain, Priority 2 = additive
REL_OPS = (
    "\\Leftrightarrow",
    "\\Longleftrightarrow",
    "\\Rightarrow",
    "\\Longrightarrow",
    "\\implies",
    "\\iff",
    "\\xrightarrow",
    "\\rightarrow",
    "\\approx",
    "\\equiv",
    "\\sim",
    "\\leq",
    "\\geq",
    "\\le",
    "\\ge",
    "\\to",
    "=",
    "<",
    ">",
)
ADD_OPS = ("+", "-")

# Environments that must never be split internally by chain splitter
NO_SPLIT_ENVS = ("\\begin{cases}", "\\begin{matrix}", "\\begin{pmatrix}", "\\begin{bmatrix}", "\\begin{vmatrix}", "\\begin{aligned}")


def _consume_braced(s: str, idx: int) -> Tuple[str, int]:
    """Consume {...} starting at idx; return (inner_text, next_idx)."""
    if idx >= len(s) or s[idx] != "{":
        return "", idx
    depth = 0
    start = idx + 1
    i = idx
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
        i += 1
    return s[start:], len(s)


def estimate_latex_width_mm(tex: str, font_size_pt: float = 12.0) -> float:
    """Estimate rendered horizontal width of a LaTeX expression in physical mm."""
    pt_scale = max(0.5, float(font_size_pt) / 12.0)
    em_mm = font_size_pt * 0.352778 * 1.06
    # Strip display directives for measurement
    s = tex.replace("\\displaystyle", " ").strip()

    def _est(expr: str, scale: float = 1.0) -> float:
        total = 0.0
        ms = scale * pt_scale
        i = 0
        n = len(expr)
        while i < n:
            ch = expr[i]
            if ch.isspace():
                i += 1
                continue
            if ch == "\\":
                # Command
                m = re.match(r"\\([A-Za-z]+|.)", expr[i:])
                cmd = m.group(1) if m else ""
                i += len(cmd) + 1
                if cmd in ("frac", "dfrac", "tfrac"):
                    num, i = _consume_braced(expr, i)
                    den, i = _consume_braced(expr, i)
                    total += max(_est(num, scale), _est(den, scale)) + 1.8 * ms
                elif cmd == "sqrt":
                    if i < n and expr[i] == "[":
                        end_b = expr.find("]", i)
                        if end_b != -1:
                            i = end_b + 1
                    body, i = _consume_braced(expr, i)
                    total += _est(body, scale) + 3.8 * ms
                elif cmd in ("text", "mathrm", "mathbf", "mathit"):
                    body, i = _consume_braced(expr, i)
                    for c in body:
                        total += (1.32 if "\u4e00" <= c <= "\u9fff" else 0.56) * em_mm * scale
                elif cmd in ("int", "iint", "iiint", "oint", "sum", "prod", "lim"):
                    total += 5.4 * ms
                elif cmd in ("Rightarrow", "Longrightarrow", "implies", "Leftrightarrow", "Longleftrightarrow", "iff"):
                    total += 5.2 * ms
                elif cmd in ("xrightarrow", "longrightarrow"):
                    if i < n and expr[i] == "[":
                        end_b = expr.find("]", i)
                        if end_b != -1:
                            i = end_b + 1
                    lbl, i = _consume_braced(expr, i)
                    total += max(6.5 * ms, _est(lbl, scale * 0.72) + 2.0 * ms)
                elif cmd in ("left", "right"):
                    if i < n:
                        i += 1
                    total += 1.9 * ms
                elif cmd in ("overline", "bar", "hat", "tilde", "vec", "dot", "ddot"):
                    body, i = _consume_braced(expr, i)
                    total += max(2.4 * ms, _est(body, scale))
                elif cmd in ("quad", "qquad"):
                    total += (4.5 if cmd == "quad" else 9.0) * ms
                elif cmd in (",", ";", ":"):
                    total += 0.9 * ms
                elif cmd in ("cdot", "times", "div", "le", "leq", "ge", "geq", "approx", "equiv", "sim", "neq", "to", "rightarrow", "leftarrow"):
                    total += 4.2 * ms
                elif cmd in ("begin", "end"):
                    _, i = _consume_braced(expr, i)
                else:
                    # Greek letters, function names (Cov, Var, exp, sin, etc.)
                    w_est = 2.7 if len(cmd) > 2 and cmd[0].islower() else 2.5
                    if cmd in ("sin", "cos", "tan", "log", "exp", "lim", "sup", "inf"):
                        w_est = len(cmd) * 2.1
                    total += w_est * ms
            elif ch in ("^", "_"):
                i += 1
                if i < n and expr[i] == "{":
                    sub, i = _consume_braced(expr, i)
                    sub_w = _est(sub, scale * 0.65)
                elif i < n:
                    sub_w = 1.9 * ms
                    i += 1
                else:
                    sub_w = 0.0
                # If immediately followed by the other script (^ then _ or _ then ^), overlap widths instead of summing
                if i < n and expr[i] in ("^", "_") and expr[i] != ch:
                    i += 1
                    if i < n and expr[i] == "{":
                        sub2, i = _consume_braced(expr, i)
                        sub_w = max(sub_w, _est(sub2, scale * 0.65))
                    elif i < n:
                        sub_w = max(sub_w, 1.9 * ms)
                        i += 1
                total += sub_w
            elif ch in ("=", "+", "-", "<", ">"):
                total += 4.0 * ms
                i += 1
            elif ch in ("(", ")", "[", "]", ",", ".", ":", ";", "|", "/"):
                total += 1.7 * ms
                i += 1
            elif "\u4e00" <= ch <= "\u9fff":
                total += 1.32 * em_mm * scale
                i += 1
            else:
                total += 2.6 * ms
                i += 1
        return total

    return round(_est(s, 1.0), 2)


def _find_top_level_split_points(tex: str, ops: Tuple[str, ...]) -> List[Tuple[int, str]]:
    """Return sorted list of (index, op_str) where op appears at brace/bracket/delim depth == 0."""
    points: List[Tuple[int, str]] = []
    brace_d = 0
    paren_d = 0
    lr_d = 0
    i = 0
    n = len(tex)
    # Longest op matches first
    sorted_ops = sorted(ops, key=len, reverse=True)

    while i < n:
        if tex.startswith("\\left", i):
            lr_d += 1
            i += 5
            continue
        if tex.startswith("\\right", i):
            lr_d = max(0, lr_d - 1)
            i += 6
            continue
        ch = tex[i]
        if ch == "{":
            brace_d += 1
            i += 1
            continue
        if ch == "}":
            brace_d = max(0, brace_d - 1)
            i += 1
            continue
        if ch in ("(", "["):
            paren_d += 1
            i += 1
            continue
        if ch in (")", "]"):
            paren_d = max(0, paren_d - 1)
            i += 1
            continue

        if brace_d == 0 and paren_d == 0 and lr_d == 0:
            matched = None
            for op in sorted_ops:
                if tex.startswith(op, i):
                    # Avoid matching \le inside \left or \leq
                    if op.startswith("\\") and i + len(op) < n and tex[i + len(op)].isalpha():
                        continue
                    # For + / -, avoid unary leading sign at start or right after another operator
                    if op in ("+", "-"):
                        prev = tex[:i].rstrip()
                        if not prev or prev.endswith(("=", "(", "[", "{", ",", "<", ">")):
                            continue
                    matched = op
                    break
            if matched:
                op_full = matched
                end_i = i + len(matched)
                if matched == "\\xrightarrow":
                    if end_i < n and tex[end_i] == "[":
                        eb = tex.find("]", end_i)
                        if eb != -1:
                            end_i = eb + 1
                    if end_i < n and tex[end_i] == "{":
                        _, end_i = _consume_braced(tex, end_i)
                    op_full = tex[i:end_i]
                points.append((i, op_full))
                i = end_i
                continue

        if ch == "\\":
            m = re.match(r"\\(?:[A-Za-z]+|.)", tex[i:])
            i += len(m.group(0)) if m else 1
        else:
            i += 1

    return points


def _split_by_ops(chunk: str, max_w_mm: float, font_size_pt: float, ops: Tuple[str, ...]) -> List[str]:
    pts = _find_top_level_split_points(chunk, ops)
    if not pts:
        return [chunk.strip()]

    # Segment into atomic chunks [prefix, op1+body1, op2+body2, ...]
    segs: List[str] = []
    last = 0
    for idx, _op in pts:
        segs.append(chunk[last:idx].strip())
        last = idx
    segs.append(chunk[last:].strip())
    segs = [s for s in segs if s]
    if len(segs) <= 1:
        return [chunk.strip()]

    # Pack greedily: if segs[0] has no '=' (it's LHS before first op) and len(segs) >= 2,
    # try to keep LHS + first RHS step together unless LHS alone is huge.
    pieces: List[str] = []
    cur = segs[0]
    for k in range(1, len(segs)):
        cand = f"{cur} {segs[k]}"
        # Keep LHS + first '=' piece together if k == 1 and combined isn't absurdly wide
        keep_lhs_together = (k == 1 and not any(op in cur for op in ("=", "\\Rightarrow", "\\le", "\\ge")) and estimate_latex_width_mm(cand, font_size_pt) <= max_w_mm * 1.15)
        if estimate_latex_width_mm(cand, font_size_pt) <= max_w_mm or keep_lhs_together:
            cur = cand
        else:
            pieces.append(cur)
            cur = segs[k]
    if cur:
        pieces.append(cur)
    return pieces


def split_long_formula(
    tex: str,
    first_avail_mm: float,
    next_avail_mm: float,
    font_size_pt: float = 12.0,
) -> List[str]:
    """
    Split an over-wide LaTeX chain at top-level relational/additive operators.
    Returns [tex] unchanged if it already fits or contains a matrix/cases environment.
    """
    clean = tex.strip()
    if not clean:
        return [clean]
    if any(env in clean for env in NO_SPLIT_ENVS):
        return [clean]
    if estimate_latex_width_mm(clean, font_size_pt) <= first_avail_mm:
        return [clean]

    # Step 1: split on top-level relational/implication operators
    rel_pieces = _split_by_ops(clean, first_avail_mm, font_size_pt, REL_OPS)

    # Step 2: if any piece still overflows next_avail_mm, split on top-level + / -
    final_pieces: List[str] = []
    for idx, p in enumerate(rel_pieces):
        limit = first_avail_mm if idx == 0 else next_avail_mm
        if estimate_latex_width_mm(p, font_size_pt) > limit * 1.04:
            sub = _split_by_ops(p, limit, font_size_pt, ADD_OPS)
            final_pieces.extend(sub)
        else:
            final_pieces.append(p)

    return [p for p in final_pieces if p]

