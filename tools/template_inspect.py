from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def run_text(run: ET.Element) -> str:
    chunks: list[str] = []
    for node in run.iter():
        local = name(node.tag)
        if local in {"t", "delText", "instrText"} and node.text:
            chunks.append(node.text)
        elif local == "tab":
            chunks.append("\t")
        elif local in {"br", "cr"}:
            chunks.append("\n")
    return "".join(chunks)


def inspect_paragraph(paragraph: ET.Element, path: str) -> dict:
    runs: list[dict] = []
    for index, run in enumerate(paragraph.iter(f"{W}r")):
        rpr = run.find(f"{W}rPr")
        highlight = None
        if rpr is not None:
            node = rpr.find(f"{W}highlight")
            if node is not None:
                highlight = node.get(f"{W}val")
        fonts = {}
        if rpr is not None:
            font_node = rpr.find(f"{W}rFonts")
            if font_node is not None:
                for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                    value = font_node.get(f"{W}{key}")
                    if value:
                        fonts[key] = value
        text = run_text(run)
        runs.append({
            "index": index,
            "text": text,
            "codepoints": [f"U+{ord(char):04X}" for char in text],
            "highlight": highlight,
            "fonts": fonts,
        })
    return {"path": path, "text": "".join(item["text"] for item in runs), "runs": runs}


def walk(parent: ET.Element, prefix: str, paragraphs: list[dict], counters: dict) -> None:
    child_counts: dict[str, int] = {}
    for child in list(parent):
        local = name(child.tag)
        index = child_counts.get(local, 0)
        child_counts[local] = index + 1
        path = f"{prefix}/{local}[{index}]"
        if local == "p":
            record = inspect_paragraph(child, path)
            paragraphs.append(record)
            counters["paragraphs"] = counters.get("paragraphs", 0) + 1
        elif local == "tbl":
            counters["tables"] = counters.get("tables", 0) + 1
            walk(child, path, paragraphs, counters)
        elif local in {"tr", "tc", "sdt", "sdtContent", "customXml", "txbxContent"}:
            walk(child, path, paragraphs, counters)


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    paragraphs: list[dict] = []
    counters: dict[str, int] = {}
    with zipfile.ZipFile(source) as archive:
        xml = ET.fromstring(archive.read("word/document.xml"))
        body = xml.find(f"{W}body")
        if body is None:
            raise RuntimeError("word/document.xml has no body")
        walk(body, "word/document.xml", paragraphs, counters)
        parts = [
            {
                "path": info.filename,
                "size": info.file_size,
                "sha256": hashlib.sha256(archive.read(info.filename)).hexdigest(),
            }
            for info in archive.infolist()
        ]

    highlighted = [
        paragraph
        for paragraph in paragraphs
        if any(run["highlight"] not in {None, "none"} for run in paragraph["runs"])
    ]
    result = {
        "reference": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "counters": counters,
        "paragraphs": paragraphs,
        "highlighted_paragraphs": highlighted,
        "package_parts": parts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "sha256": result["sha256"],
        "paragraphs": counters.get("paragraphs", 0),
        "tables": counters.get("tables", 0),
        "highlighted_paragraphs": len(highlighted),
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
