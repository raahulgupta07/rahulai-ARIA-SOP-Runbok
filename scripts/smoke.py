#!/usr/bin/env python3
"""Production smoke test for City Agent Aria.

Exercises every critical path end-to-end against a RUNNING instance, prints
PASS/FAIL per check, and exits non-zero if anything critical fails (CI gate).
Pure stdlib (urllib) — works where curl is unavailable/rewritten. Read-mostly;
the few things it creates (a fact, an embed key) it deletes again.

Usage:
    SMOKE_URL=http://localhost:8082 \
    SMOKE_ADMIN_EMAIL=admin@city.com SMOKE_ADMIN_PASSWORD=secret123 \
    python3 scripts/smoke.py

    # against prod (use a throwaway admin, NOT a real one if it mutates):
    SMOKE_URL=https://aria.yourco.com ... python3 scripts/smoke.py --no-write
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = os.getenv("SMOKE_URL", "http://localhost:8082").rstrip("/")
ADMIN_EMAIL = os.getenv("SMOKE_ADMIN_EMAIL", "admin@city.com")
ADMIN_PW = os.getenv("SMOKE_ADMIN_PASSWORD", "secret123")

results = []   # (name, ok, detail, critical)
TOKEN = None


def req(method, path, token=None, body=None, raw=False, timeout=120):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}  # case-insensitive
            if raw:
                return resp.status, payload, hdrs
            try:
                return resp.status, json.loads(payload or b"{}"), hdrs
            except Exception:
                return resp.status, payload.decode("utf-8", "replace"), hdrs
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(body_txt), hdrs
        except Exception:
            return e.code, body_txt, hdrs
    except Exception as e:
        return 0, {"error": str(e)}, {}


def check(name, ok, detail="", critical=True):
    results.append((name, bool(ok), detail, critical))
    mark = "PASS" if ok else ("FAIL" if critical else "WARN")
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def stream_ask(q, token, conv_id=None, timeout=180):
    """Consume the full /api/ask/stream NDJSON. Returns the 'done' event dict."""
    url = f"{BASE}/api/ask/stream"
    body = {"q": q, "mode": "auto"}
    if conv_id:
        body["conversation_id"] = conv_id
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", f"Bearer {token}")
    done, meta, tokens = None, None, 0
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        for line in resp:
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            obj = json.loads(line)
            t = obj.get("type")
            if t == "meta":
                meta = obj
            elif t == "token":
                tokens += 1
            elif t == "done":
                done = obj
    return done, meta, tokens


# ---------------------------------------------------------------- phases ---
def phase_boot():
    print("\n# 1. Boot / public")
    s, j, _ = req("GET", "/api/health")
    check("health 200 ok", s == 200 and j.get("status") == "ok", f"status={s}")
    s, j, _ = req("GET", "/api/version")
    check("version served", s == 200 and j.get("version"), f"v{j.get('version')}")
    s, j, _ = req("GET", "/api/stats/public")
    check("public stats (no auth)", s == 200 and "docs" in j,
          f"{j.get('docs')} docs / {j.get('pages')} pages")
    s, j, _ = req("GET", "/api/auth/config")
    check("auth config public", s == 200 and "enable_local" in j)


def phase_security():
    print("\n# 2. Security headers / guards")
    s, _, h = req("GET", "/api/health")
    check("nosniff header", h.get("x-content-type-options") == "nosniff")
    # HSTS only in production — warn (not fail) if absent
    check("HSTS (prod only)", bool(h.get("strict-transport-security")),
          "absent — expected only when APP_ENV=production", critical=False)
    s, j, _ = req("POST", "/api/auth/login",
                  body={"email": ADMIN_EMAIL, "password": "definitely-wrong-pw"})
    check("bad password rejected", s == 401, f"status={s}")
    s, j, _ = req("GET", "/api/documents")  # no token
    check("unauthed API blocked", s == 401, f"status={s}")


def phase_auth():
    global TOKEN
    print("\n# 3. Auth")
    s, j, _ = req("POST", "/api/auth/login",
                  body={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    ok = check("admin login 200", s == 200 and j.get("token"), f"status={s}")
    if ok:
        TOKEN = j["token"]
    s, j, _ = req("GET", "/api/auth/me", token=TOKEN)
    check("/auth/me resolves admin", s == 200 and j.get("role") == "admin",
          f"role={j.get('role')}")


def phase_corpus():
    print("\n# 4. Corpus / retrieval")
    s, j, _ = req("GET", "/api/documents?limit=500", token=TOKEN)
    docs = (j or {}).get("docs", [])
    # 'ready' = fully enriched; 'ready_lite' = phase-1 done, answerable now (newer code)
    ready = [d for d in docs if d.get("status") in ("ready", "ready_lite")]
    check("documents list", s == 200 and "docs" in j, f"{j.get('total')} total")
    if not ready:
        check("at least 1 answerable doc", False,
              "no ready/ready_lite docs — ingest a SOP before prod", critical=False)
        return None
    d = ready[0]
    check("at least 1 ready doc", True, f"#{d['id']} {d.get('name','')[:40]}")
    s, j, _ = req("GET", f"/api/documents/{d['id']}", token=TOKEN)
    check("doc detail + stats", s == 200 and "stats" in j)
    s, j, _ = req("GET", f"/api/documents/{d['id']}/text", token=TOKEN)
    check("doc full text", s == 200 and "pages" in j)
    if d.get("cover_page_id"):
        s, b, h = req("GET", f"/api/pages/{d['cover_page_id']}", raw=True)
        check("page image served (public)", s == 200 and len(b) > 100,
              f"{len(b)} bytes, {h.get('Content-Type')}")
    return d


def phase_brain():
    print("\n# 5. Brain search / graph")
    s, j, _ = req("GET", "/api/brain/search?q=user&type=all", token=TOKEN)
    check("brain search", s == 200 and "items" in j, f"{len(j.get('items', []))} items")
    s, j, _ = req("GET", "/api/brain/graph?limit=50", token=TOKEN)
    check("brain graph nodes/links", s == 200 and "nodes" in j and "links" in j,
          f"{len(j.get('nodes', []))} nodes")
    s, j, _ = req("GET", "/api/catalog", token=TOKEN)
    check("capabilities catalog", s == 200 and "groups" in j)


def phase_chat(have_docs, no_write):
    print("\n# 6. Chat pipeline (stream)")
    q = "How do I create a new user account?"
    try:
        done, meta, ntok = stream_ask(q, TOKEN)
    except Exception as e:
        check("ask/stream completes", False, str(e)[:120])
        return None
    conv = (meta or {}).get("conversation_id")
    check("ask/stream completes", done is not None, f"{ntok} tokens streamed")
    if done:
        grounded = done.get("grounded")
        cited = done.get("cited_n", 0)
        check("answer returned + done event", bool(done.get("clean") or done.get("pages") is not None))
        check("grounding flag present", "grounded" in done,
              f"grounded={grounded}, cited={cited}, blind={done.get('blind')}")
        check("token/cost telemetry", "tokens" in done and "cost" in done,
              f"{done.get('tokens',{}).get('total')} tok, ${done.get('cost')}")
    # Q&A cache: ask again, should be faster / maybe served_from_bank (only if pairs exist)
    if not no_write and conv is None:
        try:
            done2, meta2, _ = stream_ask(q, TOKEN)
            check("repeat ask works", done2 is not None,
                  "served_from_bank=" + str(bool(done2 and done2.get("served_from_bank"))),
                  critical=False)
        except Exception as e:
            check("repeat ask works", False, str(e)[:80], critical=False)
    return conv


def phase_learning(no_write):
    print("\n# 7. Self-learning / governance")
    s, j, _ = req("GET", "/api/governance", token=TOKEN)
    check("governance policy", s == 200 and "policy" in j)
    s, j, _ = req("GET", "/api/memory", token=TOKEN)
    check("memory list + pending count", s == 200 and "memory" in j,
          f"{j.get('pending')} pending")
    s, j, _ = req("GET", "/api/qa", token=TOKEN)
    check("Q&A bank list", s == 200 and "qa" in j,
          f"counts={j.get('counts')}")
    if no_write:
        return
    # teach a throwaway fact, confirm, delete it
    s, j, _ = req("POST", "/api/memory", token=TOKEN,
                  body={"value": "SMOKE TEST fact — delete me", "key": "smoke"})
    mid = j.get("id") if s == 200 else None
    check("teach a fact (admin)", bool(mid), f"id={mid}, status={j.get('status')}")
    if mid:
        s, _, _ = req("DELETE", f"/api/memory/{mid}", token=TOKEN)
        check("delete the test fact (cleanup)", s == 200)


def phase_dashboards():
    print("\n# 8. Admin dashboards / ops")
    for name, path in [
        ("dashboard cockpit", "/api/dashboard/cockpit"),
        ("dashboard admin", "/api/dashboard/admin"),
        ("vitals", "/api/dashboard/vitals"),
        ("analytics perf", "/api/analytics/perf"),
        ("ops health", "/api/ops"),
        ("audit coverage", "/api/audit/coverage"),
        ("analytics security", "/api/analytics/security"),
        ("ingest state", "/api/ingest/state"),
    ]:
        s, j, _ = req("GET", path, token=TOKEN)
        check(name, s == 200 and isinstance(j, dict), f"status={s}", critical=False)


def phase_embed(no_write):
    print("\n# 9. Embed widget")
    if no_write:
        s, j, _ = req("GET", "/api/embed/keys", token=TOKEN)
        check("embed keys list", s == 200 and "keys" in j)
        return
    s, j, _ = req("POST", "/api/embed/keys", token=TOKEN,
                  body={"name": "smoke-test", "allowed_origins": [],
                        "rate_per_min": 30})
    kid = j.get("id") if s == 200 else None
    pk = j.get("public_key")
    check("create embed key", bool(kid and pk), f"id={kid}")
    if kid:
        s, j, _ = req("POST", "/api/embed/session",
                      body={"public_key": pk, "visitor_id": "smoke-visitor"})
        wtok = j.get("token") if s == 200 else None
        check("embed session mints widget token", bool(wtok))
        if wtok:
            try:
                done, _, _ = stream_ask("what can you help with?", wtok)
                check("widget token can chat", done is not None,
                      critical=False)
            except Exception as e:
                check("widget token can chat", False, str(e)[:80], critical=False)
        s, _, _ = req("DELETE", f"/api/embed/keys/{kid}", token=TOKEN)
        check("delete embed key (cleanup)", s == 200)


# ----------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true",
                    help="skip checks that create data (safer against prod)")
    a = ap.parse_args()

    print(f"=== Aria smoke test → {BASE} ===")
    print(f"admin: {ADMIN_EMAIL}  write-checks: {'OFF' if a.no_write else 'ON'}")

    phase_boot()
    phase_security()
    phase_auth()
    if not TOKEN:
        print("\nABORT: could not authenticate — fix login first.")
        sys.exit(2)
    doc = phase_corpus()
    phase_brain()
    phase_chat(doc is not None, a.no_write)
    phase_learning(a.no_write)
    phase_dashboards()
    phase_embed(a.no_write)

    # summary
    crit_fail = [r for r in results if not r[1] and r[3]]
    warn = [r for r in results if not r[1] and not r[3]]
    npass = sum(1 for r in results if r[1])
    print(f"\n=== {npass}/{len(results)} passed · "
          f"{len(crit_fail)} critical fail · {len(warn)} warn ===")
    if crit_fail:
        print("CRITICAL FAILURES (block production):")
        for n, _, d, _ in crit_fail:
            print(f"  - {n}  {d}")
    if warn:
        print("Warnings (review, not blocking):")
        for n, _, d, _ in warn:
            print(f"  - {n}  {d}")
    print("\nGO" if not crit_fail else "\nNO-GO")
    sys.exit(1 if crit_fail else 0)


if __name__ == "__main__":
    main()
