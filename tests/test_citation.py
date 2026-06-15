"""Citation index↔page_id mapping — the fix that stopped wrong-doc citations.
Pure unit, no DB."""
from app.agent import parse_pages, map_cited, render_cited

PAGES = [
    {"page_id": 100, "page_no": 6},
    {"page_id": 200, "page_no": 8},
    {"page_id": 300, "page_no": 2},
]


def test_parse_pages_extracts_source_numbers():
    clean, nums = parse_pages("answer body\nPAGES: 1, 3", [])
    assert nums == [1, 3]
    assert "PAGES" not in clean


def test_parse_pages_none_uses_fallback():
    _, nums = parse_pages("body\nPAGES: none", [7])
    assert nums == [7]


def test_map_cited_indices_to_real_page_ids():
    assert map_cited([1, 3], PAGES) == [100, 300]


def test_map_cited_dedups_and_drops_out_of_range():
    assert map_cited([1, 1, 9, 2], PAGES) == [100, 200]


def test_render_cited_rewrites_markers_to_page_numbers():
    assert render_cited("do [1] then [3]", PAGES) == "do [p.6] then [p.2]"


def test_render_cited_leaves_out_of_range_untouched():
    assert render_cited("see [9]", PAGES) == "see [9]"
