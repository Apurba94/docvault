"""The index, duplicate finder and version finder, on SQLite full-text search."""
import hashlib
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict

from .extract import TEXT_TYPES, extract

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL, sha TEXT NOT NULL, chars INTEGER NOT NULL, warning TEXT, shingles BLOB);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(name, body, tokenize = 'unicode61 remove_diacritics 0');
"""
SKIP = {".git", "node_modules", "__pycache__", "AppData", "$RECYCLE.BIN", "System Volume Information"}
TYPES = TEXT_TYPES + (".csv", ".docx", ".xlsx", ".pdf")


def clean(text):
    return unicodedata.normalize("NFC", text)


def signature(text, k=5, keep=128):
    """A MinHash sketch of word shingles: a small fingerprint for comparing documents quickly."""
    words = re.findall(r"[^\W_]+", text.lower())
    if len(words) < k:
        shingles = {" ".join(words)} if words else set()
    else:
        shingles = {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}
    if not shingles:
        return b""
    best = [2 ** 64] * keep
    for s in shingles:
        h = int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")
        for i in range(keep):
            v = ((h ^ (i * 0x9E3779B97F4A7C15)) * 0xBF58476D1CE4E5B9 + i) & (2 ** 64 - 1)
            if v < best[i]:
                best[i] = v
    return b"".join(x.to_bytes(8, "big") for x in best)


def similarity(a, b):
    if not a or not b:
        return 0.0
    n = len(a) // 8
    return sum(a[i * 8:i * 8 + 8] == b[i * 8:i * 8 + 8] for i in range(n)) / n


class Vault:
    def __init__(self, path=":memory:"):
        self.c = sqlite3.connect(path, check_same_thread=False)     # the web app runs one handler at a time
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def add(self, path, full=None):
        """Index one file if it is new or has changed. Returns True if it was read."""
        full = full or os.path.abspath(path)
        st = os.stat(full)
        old = self.c.execute("SELECT id, size, mtime FROM docs WHERE path = ?", (full,)).fetchone()
        if old and old["size"] == st.st_size and old["mtime"] == st.st_mtime:
            return False
        h = hashlib.sha256()
        with open(full, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        text, warning = extract(full)
        text = clean(text)
        sig = signature(text)
        name = os.path.basename(full)
        if old:
            self.c.execute("DELETE FROM search WHERE rowid = ?", (old["id"],))
            self.c.execute("UPDATE docs SET name = ?, size = ?, mtime = ?, sha = ?, chars = ?, warning = ?, shingles = ? WHERE id = ?", (name, st.st_size, st.st_mtime, h.hexdigest(), len(text), warning, sig, old["id"]))
            rowid = old["id"]
        else:
            rowid = self.c.execute("INSERT INTO docs (path, name, size, mtime, sha, chars, warning, shingles) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (full, name, st.st_size, st.st_mtime, h.hexdigest(), len(text), warning, sig)).lastrowid
        self.c.execute("INSERT INTO search (rowid, name, body) VALUES (?, ?, ?)", (rowid, name, text))
        return True

    def index(self, folder):
        """Index a folder, skip what has not changed, and forget files that are gone."""
        seen = set()
        read = 0
        for base, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            for f in files:
                if os.path.splitext(f)[1].lower() in TYPES and not f.startswith("~$"):
                    full = os.path.abspath(os.path.join(base, f))
                    seen.add(full)
                    try:
                        read += self.add(full)
                    except OSError:
                        pass
        root = os.path.abspath(folder) + os.sep
        gone = [r for r in self.c.execute("SELECT id, path FROM docs") if r["path"].startswith(root) and r["path"] not in seen]
        for r in gone:
            self.c.execute("DELETE FROM search WHERE rowid = ?", (r["id"],))
            self.c.execute("DELETE FROM docs WHERE id = ?", (r["id"],))
        self.c.commit()
        return {"read": read, "skipped": len(seen) - read, "removed": len(gone)}

    @staticmethod
    def query_text(q):
        """Quote each word, so that typed punctuation cannot be read as search syntax."""
        q = clean(q)
        parts = re.findall(r'"[^"]+"|\S+', q)
        out = []
        for p in parts:
            if p.upper() in ("OR", "AND", "NOT"):
                out.append(p.upper())
            else:
                word = p.strip('"').replace('"', "")
                prefix = word.endswith("*")
                word = word.rstrip("*")
                if word:
                    out.append('"' + word + '"' + ("*" if prefix else ""))
        while out and out[-1] in ("OR", "AND", "NOT"):
            out.pop()
        while out and out[0] in ("OR", "AND"):
            out.pop(0)
        return " ".join(out)

    def search(self, q, limit=50):
        text = self.query_text(q)
        if not text:
            return []
        rows = self.c.execute("SELECT d.*, snippet(search, 1, '[', ']', ' … ', 24) snip, bm25(search, 5.0, 1.0) rank FROM search JOIN docs d ON d.id = search.rowid WHERE search MATCH ? ORDER BY rank LIMIT ?", (text, limit)).fetchall()
        return [dict(r) for r in rows]

    def duplicates(self):
        """Groups of identical files, with the space that could be recovered."""
        groups = defaultdict(list)
        for r in self.c.execute("SELECT path, sha, size FROM docs"):
            groups[r["sha"]].append(r)
        out = [(g[0]["size"] * (len(g) - 1), [x["path"] for x in g]) for g in groups.values() if len(g) > 1]
        return sorted(out, key=lambda t: -t[0])

    def versions(self, threshold=0.5):
        """Pairs of different files with mostly the same words, such as drafts of one report."""
        rows = [r for r in self.c.execute("SELECT path, sha, shingles, chars FROM docs") if r["shingles"]]
        out = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if a["sha"] != b["sha"]:
                    s = similarity(a["shingles"], b["shingles"])
                    if s >= threshold:
                        out.append((s, a["path"], b["path"]))
        return sorted(out, key=lambda t: -t[0])

    def problems(self):
        return [dict(r) for r in self.c.execute("SELECT path, warning FROM docs WHERE warning IS NOT NULL ORDER BY path")]

    def stats(self):
        r = self.c.execute("SELECT COUNT(*) n, COALESCE(SUM(size), 0) s FROM docs").fetchone()
        return {"files": r["n"], "bytes": r["s"]}
