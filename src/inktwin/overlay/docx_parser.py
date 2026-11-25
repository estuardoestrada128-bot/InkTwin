"""Worksheet DOCX & Markdown structured parser."""

from __future__ import annotations

import base64
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

DEFAULT_LECTURES_ROOT = Path(
    os.environ.get("INKTWIN_LECTURES_ROOT") or (Path.home() / "Desktop" / "Learning files" / "Lectures")
)

CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def chinese_to_int(s: str) -> Optional[int]:
    s = s.strip()
    if s.isdigit():
        return int(s)
    if not s:
        return None
    if s == "十":
        return 10
    if s.startswith("十") and len(s) == 2 and s[1] in CN_NUM_MAP:
        return 10 + CN_NUM_MAP[s[1]]
    if s.endswith("十") and len(s) == 2 and s[0] in CN_NUM_MAP:
        return CN_NUM_MAP[s[0]] * 10
    if "十" in s and len(s) == 3 and s[0] in CN_NUM_MAP and s[2] in CN_NUM_MAP:
        return CN_NUM_MAP[s[0]] * 10 + CN_NUM_MAP[s[2]]
    if len(s) == 1 and s in CN_NUM_MAP:
        return CN_NUM_MAP[s]
    return None


def extract_week_number(text: str) -> Optional[int]:
    m = re.search(r"第\s*([0-9一二三四五六七八九十两]+)\s*周", text)
    if m:
        return chinese_to_int(m.group(1))
    m = re.search(r"[wW](\d{1,2})\b", text)
    if m:
        return int(m.group(1))
    return None


def split_frontmatter(md_text: str) -> Tuple[Dict, str]:
    md_text = md_text.lstrip("\ufeff")
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n", md_text, flags=re.S)
    if not m:
        return {}, md_text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, md_text[m.end():]


def parse_markdown_answers(md_text: str) -> Dict:
    fm, body = split_frontmatter(md_text)
    title = ""
    meta_line = ""
    lines = body.splitlines()
    content_start = 0
    for idx, line in enumerate(lines[:10]):
        s = line.strip()
        if not s or s == "---":
            content_start = idx + 1
            continue
        if s.startswith("# ") and not title:
            title = s[2:].strip()
            content_start = idx + 1
            continue
        if any(k in s for k in ("姓名", "学号", "日期", "时间")) and not s.startswith("##"):
            meta_line = re.sub(r"\*\*|&emsp;", " ", s)
            meta_line = re.sub(r"\s+", "  ", meta_line).strip()
            content_start = idx + 1
            continue
        break

    rest_lines = lines[content_start:]
    q_heading_re = re.compile(
        r"^#{2,3}\s*(?:【\s*题\s*(\d+)\s*】|第\s*(\d+)\s*题|题\s*(\d+)|(?:Problem|Question|Q)\.?\s*(\d+)\b|(\d+)\s*[\.、\)])(.*)$",
        re.IGNORECASE,
    )

    sections: List[Dict] = []
    preamble_lines: List[str] = []
    cur_sec: Optional[Dict] = None

    for line in rest_lines:
        m = q_heading_re.match(line.strip())
        if m:
            q_num = int(next(g for g in m.groups()[:5] if g is not None))
            raw_heading = re.sub(r"^#{2,3}\s*", "", line.strip()).strip()
            cur_sec = {"num": q_num, "heading": raw_heading, "lines": []}
            sections.append(cur_sec)
        else:
            if cur_sec is None:
                preamble_lines.append(line)
            else:
                cur_sec["lines"].append(line)

    def clean_block(blines: List[str]) -> str:
        while blines and (not blines[0].strip() or blines[0].strip() == "---"):
            blines.pop(0)
        while blines and (not blines[-1].strip() or blines[-1].strip() == "---"):
            blines.pop()
        return "\n".join(blines).strip()

    for sec in sections:
        sec["markdown"] = clean_block(sec["lines"])
        del sec["lines"]

    return {
        "frontmatter": fm,
        "title": title or str(fm.get("title", "")),
        "meta": meta_line,
        "preamble": clean_block(preamble_lines),
        "sections": sections,
        "raw_body": "\n".join(rest_lines).strip(),
    }


def auto_find_docx(md_path: Path, frontmatter: Dict, lectures_root: Optional[Path] = None) -> Optional[Path]:
    """Auto-locate matching blank worksheet DOCX from subject/week metadata or sibling directory."""
    root = lectures_root or DEFAULT_LECTURES_ROOT
    subject = str(frontmatter.get("subject", "")).strip()
    if not subject:
        parts = md_path.parts
        for idx, part in enumerate(parts):
            if part.lower() == "homework" and idx >= 1:
                subject = parts[idx - 1]
                break

    week_num = extract_week_number(str(frontmatter.get("week", "")))
    if week_num is None:
        week_num = extract_week_number(md_path.stem)
    if week_num is None:
        week_num = extract_week_number(str(frontmatter.get("title", "")))

    search_dirs: List[Path] = []
    if subject and (root / subject / "Homework").exists():
        search_dirs.append(root / subject / "Homework")
    search_dirs.append(md_path.parent)

    candidates: List[Tuple[int, Path]] = []
    for sdir in search_dirs:
        if not sdir.exists():
            continue
        for docx in sdir.glob("*.docx"):
            name = docx.name
            if name.startswith("~$") or "抄写版" in name or "解析" in name or "答案" in name:
                continue
            w = extract_week_number(name)
            if week_num is not None and w == week_num:
                score = 100
                if "作业" in name:
                    score += 10
                if "完整版" not in name:
                    score += 2
                candidates.append((score, docx))

    if candidates:
        candidates.sort(key=lambda x: (-x[0], -x[1].stat().st_mtime))
        return candidates[0][1]
    return None


def parse_docx_worksheet(docx_path: Path) -> Dict:
    """Parse blank DOCX worksheet extracting header, question prompts, slot heights, and images."""
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    }

    with zipfile.ZipFile(docx_path, "r") as z:
        rels_xml = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        rel_map = {
            rel.attrib["Id"]: rel.attrib.get("Target", "")
            for rel in rels_xml
            if "Id" in rel.attrib
        }

        doc_xml = ET.fromstring(z.read("word/document.xml"))
        body = doc_xml.find("w:body", ns)
        if body is None:
            raise ValueError(f"无法解析 DOCX 正文: {docx_path}")

        header_title = ""
        header_meta = ""
        questions: List[Dict] = []
        current_q: Optional[Dict] = None
        q_header_re = re.compile(r"^\s*第\s*(\d+)\s*题")

        for p in body.findall("w:p", ns):
            text = "".join((t.text or "") for t in p.findall(".//w:t", ns)).strip()

            imgs: List[Dict] = []
            for drawing in p.findall(".//w:drawing", ns):
                blip = drawing.find(".//a:blip", ns)
                if blip is None:
                    continue
                rid = blip.attrib.get(f"{{{ns['r']}}}embed")
                if not rid or rid not in rel_map:
                    continue
                media_rel = rel_map[rid].lstrip("/")
                media_path = media_rel if media_rel.startswith("word/") else f"word/{media_rel}"
                if media_path not in z.namelist():
                    continue
                img_bytes = z.read(media_path)
                ext = Path(media_path).suffix.lower().lstrip(".") or "png"
                mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                b64 = base64.b64encode(img_bytes).decode("ascii")

                width_mm = 162.0
                extent = drawing.find(".//wp:extent", ns)
                if extent is not None and "cx" in extent.attrib:
                    try:
                        width_mm = min(172.0, max(40.0, int(extent.attrib["cx"]) / 36000.0))
                    except ValueError:
                        pass
                imgs.append({"src": f"data:{mime};base64,{b64}", "width_mm": round(width_mm, 1)})

            if not text and not imgs:
                if current_q is not None:
                    current_q["blank_lines"] = current_q.get("blank_lines", 0) + 1
                continue

            m_q = q_header_re.match(text)
            if m_q:
                current_q = {"num": int(m_q.group(1)), "title": text, "texts": [], "images": [], "blank_lines": 0}
                questions.append(current_q)
                if imgs:
                    current_q["images"].extend(imgs)
                continue

            if current_q is None:
                if not header_title and text:
                    header_title = text
                elif not header_meta and text and any(k in text for k in ("姓名", "学号", "时间", "日期")):
                    header_meta = text
            else:
                if text:
                    current_q["texts"].append(text)
                if imgs:
                    current_q["images"].extend(imgs)

        for q in questions:
            bl = q.get("blank_lines", 0)
            q["slot_mm"] = round(max(26.0, min(86.0, bl * 6.4)), 1) if bl >= 2 else 44.0

    return {"title": header_title, "meta": header_meta, "questions": questions}

