"""Privacy-preserving keyword / topic analysis of ALL user questions.

NEVER returns raw chat text. Only aggregate counts: top terms, top phrases
(bigrams), intent mix and rising terms. A min-count threshold drops anything seen
fewer than MIN_COUNT times so a single user's unique wording can never surface
(k-anonymity). Tokens are letters-only (Unicode) so digits / emails / ids / codes
are stripped. Gated behind nothing destructive — pure reads.
"""
import re

from .db import get_conn

MIN_COUNT = 2   # drop terms/phrases appearing fewer than this many times

# stopwords: english function words + question framing words (kept small/explicit)
_STOP = set(
    "the a an of to in on for and or is are was were be been being do does did "
    "how what where when which who why can could should would will shall may might "
    "i me my we our you your it its this that these those there here as at by from "
    "with about into out up down over under again then than so if but not no yes "
    "have has had get got need want please tell show give me us add new use using "
    "any all some more most very just also only too via per s".split()
)

# intent buckets — first matching set wins (order matters)
_INTENTS = [
    ("troubleshoot", {"error", "errors", "fail", "failed", "failure", "issue", "issues",
                      "problem", "fix", "broken", "crash", "stuck", "wrong", "cannot", "cant"}),
    ("how_to", {"how", "steps", "step", "guide", "setup", "set", "configure", "config",
                "install", "create", "creation", "enable", "process", "do"}),
    ("delete_disable", {"delete", "remove", "disable", "deactivate", "cancel", "void", "frozen", "freeze"}),
    ("where_find", {"where", "find", "locate", "location", "which", "list"}),
    ("status_check", {"status", "check", "monitor", "verify", "confirm", "track"}),
]


# Burmese has no inter-word spaces, so a whole sentence used to collapse into ONE
# token (never recurring → killed by MIN_COUNT). Segment into syllables and emit
# 2-syllable shingles (≈ words) so Burmese terms recur and surface like English.
_MY_SYL = re.compile(
    r"[က-ဪဿ၀-၉၌-၏]"   # base: consonant / independent vowel / digit / symbol
    r"(?:္[က-အ])?"                          # stacked consonant (virama + consonant)
    r"[ျ-ှ]*"                                    # medials
    r"[ါ-ဵၖ-ၙၞ-ၠ]*"          # vowel signs
    r"[ံ့း်]*"                         # anusvara / dot below / visarga / asat
)


_MY_CODA = re.compile(r"^[က-အ]်$")  # bare consonant + asat → coda of prior syllable


def _my_syllables(run: str) -> list[str]:
    syl: list[str] = []
    for m in _MY_SYL.finditer(run):
        s = m.group(0)
        if not s:
            continue
        if syl and _MY_CODA.match(s):   # glue asat-coda onto previous syllable
            syl[-1] += s
        else:
            syl.append(s)
    return syl


def _tokens(text: str) -> list[str]:
    # Split into Burmese runs vs everything-else; tokenize each appropriately.
    text = (text or "").lower()
    out: list[str] = []
    for chunk in re.findall(r"[က-႟]+|[^\W\d_က-႟]+", text, re.UNICODE):
        if "က" <= chunk[0] <= "႟":
            syl = _my_syllables(chunk)
            if len(syl) == 1:
                out.append(syl[0])
            else:
                out.extend(syl[i] + syl[i + 1] for i in range(len(syl) - 1))
        elif len(chunk) >= 3 and chunk not in _STOP:
            out.append(chunk)
    return out


def _classify(toks: list[str]) -> str:
    tset = set(toks)
    for name, keys in _INTENTS:
        if tset & keys:
            return name
    return "other"


def keyword_analysis(days: int = 30, top: int = 30) -> dict:
    """Aggregate keyword/topic stats over user questions in the window. No raw text."""
    days = days if days in (7, 30, 90, 365) else 30
    win = f"{days} days"
    with get_conn() as conn:
        # each user question paired with whether its answer was SOURCED (had a
        # cited page) — lets us compute demand-vs-coverage per topic, no chat text out.
        rows = conn.execute(
            "SELECT u.text AS text, "
            " (b.pages IS NOT NULL AND b.pages::text <> '[]') AS sourced "
            "FROM messages u JOIN conversations c ON c.id = u.conversation_id "
            "LEFT JOIN LATERAL (SELECT pages FROM messages b "
            "  WHERE b.conversation_id = u.conversation_id AND b.role='bot' AND b.id > u.id "
            "  ORDER BY b.id LIMIT 1) b ON true "
            f"WHERE u.role='user' AND u.created_at > now() - interval '{win}' "
            "ORDER BY u.id"
        ).fetchall()

    total = len(rows)
    uni: dict[str, int] = {}
    big: dict[str, int] = {}
    intents: dict[str, int] = {}
    half = total // 2
    early: dict[str, int] = {}
    late: dict[str, int] = {}
    asked: dict[str, int] = {}      # demand: distinct questions mentioning a term
    sourced_t: dict[str, int] = {}  # of those, how many got a sourced answer
    lang = {"en": 0, "my": 0}

    def _is_my(s: str) -> bool:
        return any("က" <= ch <= "႟" for ch in (s or ""))

    for idx, r in enumerate(rows):
        toks = _tokens(r["text"])
        lang["my" if _is_my(r["text"]) else "en"] += 1
        if not toks:
            continue
        intents[_classify(toks)] = intents.get(_classify(toks), 0) + 1
        bucket = late if idx >= half else early
        seen_u = set()
        is_src = bool(r["sourced"])
        for t in toks:
            uni[t] = uni.get(t, 0) + 1
            if t not in seen_u:
                bucket[t] = bucket.get(t, 0) + 1
                asked[t] = asked.get(t, 0) + 1
                if is_src:
                    sourced_t[t] = sourced_t.get(t, 0) + 1
                seen_u.add(t)
        for a, b in zip(toks, toks[1:]):
            key = f"{a} {b}"
            big[key] = big.get(key, 0) + 1

    def _top(d: dict, n: int) -> list[dict]:
        items = [(k, v) for k, v in d.items() if v >= MIN_COUNT]
        items.sort(key=lambda x: (-x[1], x[0]))
        return [{"term": k, "count": v} for k, v in items[:n]]

    # rising = bigger share in the later half than earlier (min presence in late)
    rising = []
    for t, lc in late.items():
        if lc >= MIN_COUNT and lc > early.get(t, 0):
            rising.append({"term": t, "count": lc, "delta": lc - early.get(t, 0)})
    rising.sort(key=lambda x: (-x["delta"], -x["count"]))

    intent_list = sorted(
        ({"intent": k, "count": v} for k, v in intents.items()),
        key=lambda x: -x["count"],
    )

    # demand vs coverage: top terms by how often asked, with the % of those
    # questions that got a SOURCED answer. low coverage + high demand = build a doc.
    demand = []
    for t, a in asked.items():
        if a < MIN_COUNT:
            continue
        src = sourced_t.get(t, 0)
        demand.append({"term": t, "asked": a, "coverage_pct": round(100 * src / a) if a else 0})
    demand.sort(key=lambda x: (-x["asked"], x["coverage_pct"]))
    # gaps = decent demand but weak coverage (priority list to document)
    gaps = sorted(
        [d for d in demand if d["asked"] >= max(MIN_COUNT, 3) and d["coverage_pct"] < 60],
        key=lambda x: (-x["asked"], x["coverage_pct"]),
    )[:10]

    return {
        "total_questions": total,
        "keywords": _top(uni, top),
        "phrases": _top(big, 15),
        "intents": intent_list,
        "rising": rising[:8],
        "demand": demand[:14],
        "gaps": gaps,
        "lang": lang,
        "days": days,
        "min_count": MIN_COUNT,
    }
