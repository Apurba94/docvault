"""The offline web app. It reads files where they are, and never changes or deletes them."""
import os

from .fieldkit import App, card, form, h, stats, table
from .vault import Vault


def create_app(db="docvault.db"):
    a = App("DocVault", ":memory:", "", [("Search", "/"), ("Add a folder", "/index"), ("Duplicates", "/duplicates"), ("Versions", "/versions"), ("Unread files", "/unread")])
    v = Vault(db)
    a.vault = v

    @a.route("/")
    def home(req):
        q = req.get("q").strip()
        s = v.stats()
        results = ""
        if q:
            hits = v.search(q)
            results = card(f"{len(hits)} results", table(hits, [("File", "name"), ("Where", lambda r: os.path.dirname(r["path"])), ("Text", "snip")], "Nothing found."))
        return a.page("Search your documents", stats([("Files", s["files"]), ("Size", f"{s['bytes'] / 1e6:.1f} MB")]) + card("Search", f'<form method="get" action="/"><label>Words, "exact phrases", OR, NOT, or a prefix*<input name="q" value="{h(q)}" autofocus></label><button>Search</button></form>') + results)

    @a.route("/index", ("GET", "POST"))
    def index(req):
        if req.method == "POST":
            folder = req.text("folder")
            if not os.path.isdir(folder):
                return a.page("Add a folder", card("Not found", f"<p>{h(folder)} is not a folder on this computer.</p>"))
            r = v.index(folder)
            return a.page("Add a folder", card("Done", f"<p>Read {r['read']} files, {r['skipped']} unchanged, {r['removed']} removed from the index.</p>"))
        return a.page("Add a folder", card("Files stay where they are", form(a, "/index", [("folder", "Folder path")], "Index")))

    @a.route("/duplicates")
    def duplicates(req):
        rows = v.duplicates()
        return a.page("Exact duplicates", stats([("Groups", len(rows)), ("Could recover", f"{sum(r[0] for r in rows) / 1e6:.1f} MB")]) + card("Identical files", table(rows, [("Spare size", lambda r: f"{r[0] / 1e6:.2f} MB"), ("Copies", lambda r: " | ".join(r[1]))], "No duplicates found.")))

    @a.route("/versions")
    def versions(req):
        return a.page("Likely versions", card("Different files with mostly the same text", table(v.versions()[:200], [("Alike", lambda r: f"{r[0]:.0%}"), ("First", lambda r: r[1]), ("Second", lambda r: r[2])], "None found.")))

    @a.route("/unread")
    def unread(req):
        return a.page("Files that could not be read", card("These are not fully searchable", table(v.problems(), [("File", "path"), ("Why", "warning")], "Every file was read.")))

    return a
