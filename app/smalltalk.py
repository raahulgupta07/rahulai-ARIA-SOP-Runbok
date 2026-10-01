"""Small talk ("hi", "thanks", "bye", "who are you", "help") — answered instantly.

Without this, a greeting ran the full runbook search (expansion + rerank + scope
gate + recovery LLM call, ~8 s) and was then refused as off-topic, logged as a
blind spot and an "off-topic question" audit event. A short friendly reply with
a few real example questions is both faster and correct. Zero LLM, never raises.

Precision first: only a WHOLE message that is small talk matches — "hi, how do
I reset a password" still goes to normal retrieval.
"""
import re

from .db import get_conn

_TAIL = r"[\s!.,?~]*(aria|there|team|all|everyone|bot|ခင်ဗျာ|ရှင့်|ရှင်|ဗျ)?[\s!.,?~😊🙂👋🙏]*$"

_KINDS = [
    ("greet", r"^\s*(hi+|hello+|hey+|hiya|yo|howdy|greetings|good\s+(morning|afternoon|evening|day)|"
              r"mingalabar|mingalaba|မင်္ဂလာပါ|မင်္ဂလာ\s*ပါ)" + _TAIL),
    ("thanks", r"^\s*(thanks?(\s+(you|a\s+lot|so\s+much))*|thank\s+you(\s+(so\s+much|very\s+much))?|thx|ty|cheers|"
               r"ok(ay)?(\s+thanks?)?|great|cool|nice|perfect|got\s+it|awesome|"
               r"ကျေးဇူး(တင်ပါတယ်|ပါ|ပဲ)?|ကျေးဇူးပါ)" + _TAIL),
    ("bye", r"^\s*(bye+|good\s*bye|see\s+(you|ya)(\s+later)?|cya|good\s+night|take\s+care|"
            r"တာ့တာ|သွားပြီ(နော်)?)" + _TAIL),
    ("who", r"^\s*(who\s+are\s+you|what\s+are\s+you|who\s+is\s+aria|what\s+is\s+aria|"
            r"what('?s|\s+is)\s+your\s+name|are\s+you\s+(a\s+)?(bot|robot|ai|human|real)|"
            r"introduce\s+yourself|နင်\s*ဘယ်သူလဲ|မင်း\s*ဘယ်သူလဲ|ဘယ်သူလဲ)" + _TAIL),
    ("help", r"^\s*(help|help\s+me|\?+|what\s+can\s+i\s+ask(\s+you)?|how\s+do(es)?\s+(this|it|you)\s+work|"
             r"how\s+(do\s+i\s+)?use\s+(this|you|aria)|how\s+to\s+use(\s+(this|you|aria))?)" + _TAIL),
]
_RX = [(k, re.compile(p, re.IGNORECASE)) for k, p in _KINDS]
_MY = re.compile(r"[က-႟]")


def kind_of(q: str) -> str | None:
    """'greet' | 'thanks' | 'bye' | 'who' | 'help' when the WHOLE message is small talk."""
    s = (q or "").strip()
    if not s or len(s) > 60:
        return None
    for k, rx in _RX:
        if rx.match(s):
            return k
    return None


def _examples(lang: str, n: int = 3) -> list[str]:
    """A few real example questions (curated starter chips, else top Q&A). Fail-soft.
    Reads the tables directly — starter_chips.read() would log fake impressions."""
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT q_en, q_my FROM starter_chips ORDER BY rank LIMIT %s", (n,)).fetchall()
            out = [((r.get("q_my") if lang == "my" else None) or r.get("q_en") or "").strip() for r in rows]
            out = [q for q in out if q]
            if len(out) < n:
                more = conn.execute(
                    "SELECT question FROM qa_pairs WHERE status='active' "
                    "AND question !~* '(this sop|this document|this runbook|purpose of)' "
                    "ORDER BY cited_count DESC NULLS LAST, id DESC LIMIT %s", (n * 3,)).fetchall()
                for r in more:
                    q = (r.get("question") or "").strip()
                    if q and q not in out:
                        out.append(q)
                    if len(out) >= n:
                        break
            return out[:n]
    except Exception as e:
        print(f"[smalltalk] examples skipped: {e!r}", flush=True)
        return []


def reply(q: str, kind: str, user: dict | None = None) -> dict:
    """{'answer': markdown, 'followups': [example questions]} for a small-talk kind."""
    lang = "my" if _MY.search(q or "") else "en"
    first = ((user or {}).get("name") or "").strip().split(" ")[0] if user else ""
    if first and "@" in first:
        first = ""
    ex = _examples(lang)
    if lang == "my":
        hi = f"မင်္ဂလာပါ{(' ' + first) if first else ''}။"
        text = {
            "greet": f"{hi} ကျွန်မက Aria ပါ — ကုမ္ပဏီရဲ့ SOP နဲ့ runbook တွေကနေ ဖြေပေးပါတယ်။ ဘာကူညီရမလဲ။",
            "thanks": "ကူညီရတာ ဝမ်းသာပါတယ်။ နောက်ထပ် မေးစရာရှိရင် မေးပါ။",
            "bye": "တာ့တာ။ လိုအပ်ရင် ပြန်လာမေးပါ။",
            "who": "ကျွန်မက Aria ပါ — ကုမ္ပဏီရဲ့ SOP နဲ့ IT runbook တွေကို ဖတ်ပြီး အဆင့်လိုက် ဖြေပေးတဲ့ assistant ပါ။ အဖြေတိုင်းမှာ ရင်းမြစ်စာမျက်နှာ ပါပါတယ်။",
            "help": "လုပ်ငန်းစဉ်တစ်ခုအကြောင်း သင့်စကားနဲ့ မေးလိုက်ပါ — အဆင့်တွေနဲ့ ရင်းမြစ်စာမျက်နှာကို ပြပေးပါမယ်။",
        }[kind]
    else:
        hi = f"Hi{(' ' + first) if first else ''}!"
        text = {
            "greet": f"{hi} I'm Aria — I answer from your company's runbooks and SOPs. What do you need help with?",
            "thanks": "You're welcome! Ask me anything else about a procedure.",
            "bye": "Bye for now — come back any time you're stuck on a procedure.",
            "who": ("I'm Aria, the assistant for your company's runbooks and IT procedures. "
                    "Ask in your own words and I'll give you the steps, with the runbook page each one comes from."),
            "help": ("Ask about any procedure in your own words — even rough wording works. "
                     "I'll answer with the steps and the runbook page they come from. "
                     "Ask *\"what runbooks do you have\"* to see everything I cover."),
        }[kind]
    # example questions go out as follow-up chips (not repeated in the text)
    return {"answer": text, "followups": ex if kind in ("greet", "who", "help", "thanks") else []}
