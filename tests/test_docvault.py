import io
import os
import random
import shutil
import tempfile
import unittest
import zipfile
import zlib

from docvault import Vault, create_app, extract, read_docx, read_pdf, read_xlsx, signature, similarity

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(*paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return b.getvalue()


def xlsx(words):
    shared = "".join(f"<si><t>{w}</t></si>" for w in words)
    cells = "".join(f'<c r="A{i + 1}" t="s"><v>{i}</v></c>' for i in range(len(words)))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="x">{shared}</sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="x"><sheetData><row>{cells}<c r="B1"><v>42</v></c></row></sheetData></worksheet>')
    return b.getvalue()


def pdf(text, compress=True):
    body = f"BT /F1 12 Tf 72 700 Td ({text}) Tj T* [(more) -20 ( words)] TJ ET".encode()
    data = zlib.compress(body) if compress else body
    return b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream\nendobj\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.v = Vault()

    def tearDown(self):
        self.v.c.close()
        shutil.rmtree(self.dir)

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)
        return p


class TestExtract(Base):
    def test_docx(self):
        self.assertEqual(read_docx(docx("First line", "বাংলা লেখা")), "First line\nবাংলা লেখা")

    def test_xlsx(self):
        text = read_xlsx(xlsx(["water", "rice"]))
        self.assertIn("rice", text)
        self.assertIn("42", text)

    def test_pdf_compressed_and_plain(self):
        for c in (True, False):
            text = read_pdf(pdf("Annual report", c))
            self.assertIn("Annual report", text)
            self.assertIn("more words", text)

    def test_pdf_escapes(self):
        self.assertIn("(a) b", read_pdf(pdf(r"\(a\) b")))

    def test_scan_gives_a_warning(self):
        p = self.write("scan.pdf", b"%PDF-1.4\nno text at all")
        self.assertIn("scan", extract(p)[1])

    def test_broken_files_do_not_crash(self):
        self.assertIn("could not read", extract(self.write("x.docx", b"not a zip"))[1])

    def test_text_encodings(self):
        self.assertEqual(extract(self.write("a.txt", "\ufeffhello".encode()))[0], "hello")
        self.assertEqual(extract(self.write("b.txt", "hi".encode("utf-16")))[0], "hi")
        self.assertEqual(extract(self.write("c.txt", b"caf\xe9"))[0], "café")

    def test_csv(self):
        self.assertIn("a b", extract(self.write("d.csv", "a,b\n"))[0])

    def test_unknown_type(self):
        self.assertIn("not read", extract(self.write("e.bin", b"x"))[1])


class TestSearch(Base):
    def setUp(self):
        super().setUp()
        self.write("a/water.txt", "Clean water points reduce disease in the camp")
        self.write("a/shelter.docx", docx("Shelter repairs before the monsoon"))
        self.write("b/report.pdf", pdf("Quarterly water quality report for donors"))
        self.write("b/bangla.txt", "পানি সরবরাহ ও স্বাস্থ্য")
        self.write("skip.exe", b"binary")
        self.first = self.v.index(self.dir)

    def names(self, q):
        return sorted(r["name"] for r in self.v.search(q))

    def test_reads_every_type(self):
        self.assertEqual(self.first["read"], 4)
        self.assertEqual(self.names("water"), ["report.pdf", "water.txt"])
        self.assertEqual(self.names("monsoon"), ["shelter.docx"])

    def test_bangla_search(self):
        self.assertEqual(self.names("পানি"), ["bangla.txt"])

    def test_query_forms(self):
        self.assertEqual(self.names('"water points"'), ["water.txt"])
        self.assertEqual(self.names("water NOT donors"), ["water.txt"])
        self.assertEqual(self.names("monsoon OR donors"), ["report.pdf", "shelter.docx"])
        self.assertEqual(self.names("rep*"), ["report.pdf", "shelter.docx"])

    def test_file_names_are_searched(self):
        self.assertIn("bangla.txt", self.names("bangla"))

    def test_punctuation_cannot_break_a_search(self):
        for q in ('"', "a:b", "(", "foo'", "AND", "NOT", "-x", "*", "a AND"):
            self.v.search(q)

    def test_snippet_marks_the_word(self):
        self.assertIn("[water]", self.v.search("water")[0]["snip"].lower())

    def test_unchanged_files_are_skipped(self):
        self.assertEqual(self.v.index(self.dir), {"read": 0, "skipped": 4, "removed": 0})

    def test_changed_files_are_read_again(self):
        p = self.write("a/water.txt", "now about bicycles entirely")
        os.utime(p, (1, 1))
        self.v.index(self.dir)
        self.assertEqual(self.names("bicycles"), ["water.txt"])
        self.assertNotIn("water.txt", self.names("disease"))

    def test_deleted_files_leave_the_index(self):
        os.remove(os.path.join(self.dir, "a", "water.txt"))
        self.assertEqual(self.v.index(self.dir)["removed"], 1)
        self.assertEqual(self.names("disease"), [])


class TestSameness(Base):
    def test_duplicates(self):
        self.write("a.txt", "same words here")
        self.write("sub/b.txt", "same words here")
        self.write("c.txt", "different")
        self.v.index(self.dir)
        rows = self.v.duplicates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0][1]), 2)

    def test_signature_estimates_overlap(self):
        rng = random.Random(1)
        words = [f"w{rng.randint(0, 50000)}" for _ in range(3000)]
        a = " ".join(words)
        b = " ".join(words[:2600] + [f"x{i}" for i in range(400)])
        self.assertAlmostEqual(similarity(signature(a), signature(b)), 2596 / 3400, delta=0.12)
        self.assertEqual(similarity(signature(a), signature(a)), 1)
        self.assertLess(similarity(signature(a), signature("totally other words " * 50)), 0.05)

    def test_versions(self):
        base = " ".join(f"word{i}" for i in range(400))
        self.write("draft1.txt", base)
        self.write("draft2.txt", base + " one more sentence at the end of it")
        self.write("other.txt", " ".join(f"other{i}" for i in range(400)))
        self.v.index(self.dir)
        rows = self.v.versions()
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0][0], 0.9)


class TestApp(Base):
    def setUp(self):
        super().setUp()
        self.write("note.txt", "Clean water")
        self.a = create_app(":memory:")

    def test_flow(self):
        r = self.a.post("/index", folder=self.dir)
        self.assertIn("Read 1 files", r.body.decode())
        self.assertIn("note.txt", self.a.get("/?q=water").body.decode())
        for u in ("/duplicates", "/versions", "/unread"):
            self.assertEqual(self.a.get(u).status, 200)

    def test_missing_folder(self):
        self.assertIn("is not a folder", self.a.post("/index", folder="Z:/nowhere").body.decode())

    def test_search_box_is_escaped(self):
        self.assertNotIn('"><script>', self.a.get('/?q="><script>x').body.decode())

    def test_files_are_never_changed(self):
        before = os.path.getmtime(os.path.join(self.dir, "note.txt"))
        self.a.post("/index", folder=self.dir)
        self.assertEqual(os.path.getmtime(os.path.join(self.dir, "note.txt")), before)


if __name__ == "__main__":
    unittest.main()
