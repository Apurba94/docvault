"""Read plain text out of common document files, using only the standard library."""
import csv
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT_TYPES = (".txt", ".md", ".rst", ".log", ".tex", ".py", ".json", ".html", ".htm")
MAX_SIZE = 100 * 1024 * 1024


def read_docx(data):
    """Paragraphs from a Word file, including tables. A .docx file is a zip of XML."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == W + "tab":
                parts.append("\t")
        if parts:
            out.append("".join(parts))
    return "\n".join(out)


def read_xlsx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                strings.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        out = []
        for n in sorted(x for x in names if x.startswith("xl/worksheets/") and x.endswith(".xml")):
            for c in ET.fromstring(z.read(n)).iter():
                if c.tag.endswith("}c"):
                    v = next((x.text for x in c if x.tag.endswith("}v")), None)
                    if v is None:
                        continue
                    if c.get("t") == "s":
                        try:
                            out.append(strings[int(v)])
                        except (ValueError, IndexError):
                            pass
                    else:
                        out.append(v)
    return " ".join(out)


def _pdf_string(raw):
    """Decode a PDF literal string: backslash escapes and octal codes."""
    out = bytearray()
    i = 0
    n = len(raw)
    simple = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12, b"(": 40, b")": 41, b"\\": 92}
    while i < n:
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < n:
            nxt = raw[i + 1:i + 2]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
            elif nxt.isdigit():
                m = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
                out.append(int(m.group(), 8) & 255)
                i += 1 + len(m.group())
            else:
                i += 1
        else:
            out.append(raw[i])
            i += 1
    return out.decode("latin-1")


def read_pdf(data):
    """Text from simple PDFs: the strings shown inside content streams.

    This handles many office-made PDFs with standard encodings. Scanned pages, and fonts with
    custom encodings, often used for Bangla, give little or nothing. A warning comes back from
    `extract` when that happens, so that nobody assumes a document was searched when it was not.
    """
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\n?endstream", data, re.S):
        body = m.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        if b"BT" not in body:
            continue
        for block in re.finditer(rb"BT(.*?)ET", body, re.S):
            text = block.group(1)
            line = []
            for tok in re.finditer(rb"\((?:\\.|[^\\()])*\)|\[.*?\]\s*TJ|<[0-9A-Fa-f]+>|T\*|Td|TD|'", text, re.S):
                t = tok.group()
                if t.startswith(b"("):
                    line.append(_pdf_string(t[1:-1]))
                elif t.endswith(b"TJ"):
                    line.append("".join(_pdf_string(x[1:-1]) for x in re.findall(rb"\((?:\\.|[^\\()])*\)", t)))
                elif t in (b"T*", b"Td", b"TD", b"'"):
                    line.append("\n")
            out.append("".join(line))
    return "\n".join(x for x in out if x.strip())


def extract(path):
    """Return (text, warning or None) for a file. Anything unreadable gives a warning, never a crash."""
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if size > MAX_SIZE:
        return "", "too large to read"
    with open(path, "rb") as f:
        data = f.read()
    try:
        if ext in TEXT_TYPES:
            for enc in ("utf-8-sig", "utf-16", "latin-1"):
                try:
                    if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                        continue
                    return data.decode(enc), None
                except UnicodeDecodeError:
                    pass
        if ext == ".csv":
            text = data.decode("utf-8-sig", "replace")
            return "\n".join(" ".join(r) for r in csv.reader(io.StringIO(text))), None
        if ext == ".docx":
            return read_docx(data), None
        if ext == ".xlsx":
            return read_xlsx(data), None
        if ext == ".pdf":
            text = read_pdf(data)
            return text, None if len(text.strip()) > 20 else "little or no text found: it may be a scan"
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        return "", f"could not read this file: {exc}"
    return "", "this file type is not read"
