"""OKF export conformance + import round-trip."""
import io
import re
import tarfile

from app import okf


# ---- pure helpers (no DB) ----

def test_humanize_strips_sop_prefix():
    raw = "SOP_IT_CMHL_AMS_GLDCENTRAL_002_User Creation & Disable.pdf"
    assert okf._humanize(raw) == "User Creation & Disable"
    assert okf._humanize("Already Clean Title") == "Already Clean Title"


def test_slug_dedups():
    used: set = set()
    assert okf._slug("Hello World", used) == "hello-world"
    assert okf._slug("Hello World", used) == "hello-world-2"


def test_yaml_quotes_special_values():
    out = okf._yaml({"type": "SOP", "title": "a: b"})
    assert "type: SOP" in out
    assert 'title: "a: b"' in out


def _bundle() -> bytes:
    files = {
        "b/documents/alpha.md":
            "---\ntype: SOP\ntitle: Alpha Test Doc\ntags: [en]\n---\n"
            "## Page 1\nUnique token ZQX9. See [Beta](/documents/beta.md).\n",
        "b/documents/beta.md":
            "---\ntype: SOP\ntitle: Beta Test Doc\n---\n## Page 1\nBeta body.\n",
        "b/facts/f.md":
            "---\ntype: Fact\ntitle: zqx fact\n---\nThe ZQX9 value is 42.\n",
        "b/._junk.md": "macos appledouble noise",   # must be filtered, not 'skipped'
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_bundle_classifies_and_filters_macos_junk():
    p = okf.parse_bundle(_bundle())
    assert p["counts"]["documents"] == 2
    assert p["counts"]["facts"] == 1
    assert p["counts"]["skipped"] == 0          # ._junk filtered out, not counted
    assert p["counts"]["links"] == 1            # alpha → beta


# ---- DB-backed ----

def test_export_is_conformant(db):
    """Every emitted concept file has parseable frontmatter + a type (OKF §9)."""
    files = okf.build_files()
    concepts = [(p, t) for p, t in files
                if p.endswith(".md") and not p.endswith("index.md")
                and not p.endswith("log.md")]
    for path, text in concepts:
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        assert m, f"{path}: no frontmatter"
        assert re.search(r"^type:\s*\S", m.group(1), re.M), f"{path}: missing type"


def _clean(db):
    with db() as c:
        c.execute("DELETE FROM docs WHERE name IN ('Alpha Test Doc','Beta Test Doc')")
        c.execute("DELETE FROM memory WHERE key='zqx fact'")


def test_import_round_trip(db):
    raw = _bundle()
    _clean(db)
    res = okf.import_bundle(raw, created_by="pytest")
    assert res["imported_documents"] == 2
    assert res["imported_facts_pending"] == 1
    assert res["imported_links"] == 1

    with db() as c:
        doc = c.execute("SELECT id FROM docs WHERE name='Alpha Test Doc'").fetchone()
        assert doc
        pg = c.execute("SELECT image_path, text FROM pages WHERE doc_id=%s",
                       (doc["id"],)).fetchone()
        assert pg["image_path"] == ""           # markdown-only → no image
        assert "ZQX9" in pg["text"]             # searchable body
        fact = c.execute("SELECT status, source FROM memory WHERE key='zqx fact'").fetchone()
        assert fact["status"] == "pending"      # review gate
        assert fact["source"] == "okf"

    # idempotent: re-import dedupes by name
    res2 = okf.import_bundle(raw, created_by="pytest")
    assert res2["imported_documents"] == 0
    assert res2["duplicate_documents_skipped"] == 2
    _clean(db)


def _zip_with_image() -> bytes:
    import base64
    import zipfile
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("aria/documents/imgzip.md",
                   "---\ntype: SOP\ntitle: Zip Image Doc\n---\n## Page 1\nVVZ token.\n")
        z.writestr("aria/images/imgzip/1.png", png)
    return buf.getvalue()


def test_zip_import_with_image_round_trip(db):
    with db() as c:
        c.execute("DELETE FROM docs WHERE name='Zip Image Doc'")
    res = okf.import_bundle(_zip_with_image(), created_by="pytest")
    assert res["imported_documents"] == 1
    assert res["imported_images"] == 1            # zip read + image written
    with db() as c:
        doc = c.execute("SELECT id FROM docs WHERE name='Zip Image Doc'").fetchone()
        pg = c.execute("SELECT image_path FROM pages WHERE doc_id=%s", (doc["id"],)).fetchone()
        assert pg["image_path"] != ""             # image landed → answer-with-page survives
        c.execute("DELETE FROM docs WHERE name='Zip Image Doc'")


def test_fetch_url_rejects_non_https():
    import pytest
    with pytest.raises(ValueError):
        okf.fetch_url("http://insecure.example.com/x.tar.gz")
