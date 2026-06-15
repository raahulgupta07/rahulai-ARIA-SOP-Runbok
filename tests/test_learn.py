"""Auto-learning (Layer A/B) unit tests — extraction gating, dedupe, normalization."""
from app.memory import norm_fact, add_memory, find_similar
from app import learn


# ---- pure unit (no DB) ----
def test_norm_fact_strips_stopwords_punct_case():
    a = norm_fact("The refund window IS 45 days!")
    b = norm_fact("refund window 45 days")
    assert a == b == "refund window 45 days"


def test_norm_fact_keeps_burmese():
    assert "မှတ်ထား" in norm_fact("မှတ်ထား refund 45")


def test_questionish_detects_questions():
    assert learn._is_questionish("How do I reset the valve?")
    assert learn._is_questionish("what is the refund window")
    assert not learn._is_questionish("The refund window is 45 days.")


def test_wants_to_teach_gate():
    assert learn.wants_to_teach("Remember the helpdesk is ext 4357")
    assert learn.wants_to_teach("from now on use Daw Hla")
    assert not learn.wants_to_teach("how do I create a site?")


# ---- with DB (dedupe) ----
def test_dedupe_exact_and_near(db):
    i1 = add_memory("Refund window is 45 days", source="chat", status="pending")
    i2 = add_memory("Refund window is 45 days", source="chat", status="pending")  # exact dup
    assert i1 == i2
    near = find_similar("the REFUND window is 45 days!")   # near-dup via dedupe_key
    assert near and near["id"] == i1


def test_confidence_and_auto_stored(db):
    mid = add_memory("Night batch runs at 2am", source="chat", status="pending",
                     confidence=0.88, auto=True)
    got = find_similar("night batch runs at 2am")
    assert got and got["id"] == mid
