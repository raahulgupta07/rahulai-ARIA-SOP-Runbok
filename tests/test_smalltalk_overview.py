"""Small talk fast path + corpus-overview shaping (pure, no DB)."""
import pytest

from app import smalltalk, global_answer as ga


@pytest.mark.parametrize("q,kind", [
    ("hello", "greet"), ("Hi!", "greet"), ("hey aria", "greet"), ("good morning", "greet"),
    ("မင်္ဂလာပါ", "greet"), ("thanks", "thanks"), ("thank you so much", "thanks"),
    ("ok thanks", "thanks"), ("ကျေးဇူးတင်ပါတယ်", "thanks"), ("bye", "bye"),
    ("who are you?", "who"), ("what is aria", "who"), ("help", "help"), ("how to use this", "help"),
])
def test_smalltalk_detected(q, kind):
    assert smalltalk.kind_of(q) == kind


@pytest.mark.parametrize("q", [
    "hi, how do I reset a password", "hello can you show the backup steps",
    "how to make new staff account", "thanks but the car mass input failed",
    "what sops we have", "help me create a user", "",
])
def test_real_questions_not_smalltalk(q):
    assert smalltalk.kind_of(q) is None


def test_reply_shapes(monkeypatch):
    monkeypatch.setattr(smalltalk, "_examples", lambda lang, n=3: ["How do I add a user?"])
    r = smalltalk.reply("hello", "greet", {"name": "Rahul Gupta"})
    assert r["answer"].startswith("Hi Rahul!") and r["followups"] == ["How do I add a user?"]
    assert "How do I add a user?" not in r["answer"]          # examples go out as chips only
    my = smalltalk.reply("မင်္ဂလာပါ", "greet", None)
    assert "Aria" in my["answer"] and "မင်္ဂလာပါ" in my["answer"]
    assert smalltalk.reply("bye", "bye")["followups"] == []


CATS = {"Batch Jobs", "Data Management", "User Management", "Uncategorized"}


@pytest.mark.parametrize("q,cat", [
    ("list batch jobs runbooks", "Batch Jobs"), ("show me data management sops", "Data Management"),
    ("what user management sops do we have", "User Management"), ("list batch job sops", "Batch Jobs"),
    ("what sops we have", None), ("list all runbooks", None),
])
def test_match_category(q, cat):
    assert ga._match_category(q, CATS) == cat


def test_area_listing_needs_a_real_area(monkeypatch):
    monkeypatch.setattr(ga, "_categories", lambda: CATS)
    assert ga.is_global_query("list batch jobs runbooks")
    assert not ga.is_global_query("show me the backup runbook")      # content question → retrieval
    assert ga.is_global_query("what sops we have")
    assert not ga.is_global_query("how to make new staff account")


def test_compact_digest():
    docs = [{"category": "Batch Jobs", "title": f"Job {i}"} for i in range(12)] + \
           [{"category": "Data Management", "title": "Backup & Restore"}]
    out = ga._compact_answer(docs)
    assert "**13 runbooks** in **2 areas**" in out
    assert "**Batch Jobs** (12)" in out and "+7 more" in out
    assert "summary pending" not in out and 'list Batch Jobs runbooks' in out
