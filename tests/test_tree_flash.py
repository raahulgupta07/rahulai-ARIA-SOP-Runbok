"""PageIndex Flash adapter: shape conversion + fall-back rules (pure, no DB)."""
from app import tree_flash
from app.brain_tools import _walk_tree

RAW = {
    "doc_name": "sop.pdf",
    "doc_title": "New Site Creation",
    "toc_source": "hybrid",
    "has_abstract_or_references_section": False,
    "structure": [
        {"title": "Preface", "node_id": "0000", "start_index": 1, "end_index": 3},
        {"title": "PURPOSE", "node_id": "0001", "start_index": 4, "end_index": 4},
        {"title": "PROCEDURE/ Steps", "node_id": "0002", "start_index": 4, "end_index": 21,
         "nodes": [
             {"title": "purpose", "node_id": "0003", "start_index": 4, "end_index": 4,
              "nodes": [{"title": "Create site", "node_id": "0004",
                         "start_index": 5, "end_index": 9}]},
             {"title": "Verify", "node_id": "0005", "start_index": 10, "end_index": 21,
              "summary": "check it"},
         ]},
    ],
}


def test_shape_matches_walk_tree():
    t = tree_flash.to_walk_shape(RAW)
    assert t["doc_name"] == "sop.pdf" and t["toc_source"] == "hybrid"
    top = t["structure"]
    assert [n["title"] for n in top] == ["Preface", "PURPOSE", "PROCEDURE/ Steps"]
    assert all({"title", "start_index", "end_index", "summary"} <= n.keys() for n in top)
    assert top[0]["summary"] == ""
    # duplicate "purpose" dropped, its child promoted; summary preserved
    kids = top[2]["nodes"]
    assert [k["title"] for k in kids] == ["Create site", "Verify"]
    assert kids[1]["summary"] == "check it"

    rows = []
    _walk_tree(t, 7, "sop.pdf", rows)
    assert [(r["title"], r["page_no"]) for r in rows] == [
        ("Preface", 1), ("PURPOSE", 4), ("PROCEDURE/ Steps", 4),
        ("Create site", 5), ("Verify", 10)]


def test_no_hierarchy_returns_none():
    pages = {"toc_source": "pages", "structure": [
        {"title": "Page 1", "start_index": 1, "end_index": 1}]}
    assert tree_flash.to_walk_shape(pages) is None
    assert tree_flash.to_walk_shape({"toc_source": "unreadable", "structure": []}) is None
    assert tree_flash.to_walk_shape({"toc_source": "detected", "structure": []}) is None
    assert tree_flash.to_walk_shape(None) is None


def test_build_never_raises(monkeypatch, tmp_path):
    def boom(_):
        raise RuntimeError("parser exploded")
    monkeypatch.setattr(tree_flash, "_run_flash", boom)
    assert tree_flash.build_tree_flash(str(tmp_path / "x.pdf")) is None

    monkeypatch.setattr(tree_flash, "_run_flash",
                        lambda _: {"toc_source": "pages", "structure": [{"title": "Page 1"}]})
    assert tree_flash.build_tree_flash("x.pdf") is None

    monkeypatch.setattr(tree_flash, "_run_flash", lambda _: RAW)
    assert tree_flash.build_tree_flash("x.pdf")["toc_source"] == "hybrid"


def test_timeout_returns_none(monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(tree_flash, "_run_flash", lambda _: gate.wait(5) or RAW)
    assert tree_flash.build_tree_flash("x.pdf", timeout_s=0.2) is None
    gate.set()


def test_not_a_pdf_returns_none(tmp_path):
    f = tmp_path / "fake.pdf"
    f.write_bytes(b"not a pdf")
    assert tree_flash.build_tree_flash(str(f)) is None
