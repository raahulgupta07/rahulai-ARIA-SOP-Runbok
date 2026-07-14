# Keycloak / OIDC SSO — Build Spec

OpenID Connect authorization-code login (Keycloak, Azure AD, Google, Okta).
Hand-rolled with `httpx` + `pyjwt` — equivalent to Open WebUI's authlib flow, plus the
production hardening every one of these gotchas cost us a debugging round.

Depends on LOCAL_LOGIN (shares `users`, `issue()`, `find_or_create`, merge-by-email).

Libraries: `httpx` (all IdP calls), `pyjwt` (`jwt.decode`, `jwt.PyJWK`, `jwt.get_unverified_header`).

---

## 1. The flow

```
Browser  ──GET /api/auth/oidc/login──▶  App
App: discover, make state+nonce, store them, redirect ─────▶ Keycloak /auth
User authenticates at Keycloak
Keycloak ──redirect  /api/auth/oidc/callback?code=..&state=..──▶ App
App: consume state, POST /token (code→tokens), verify id_token, get email, find_or_create
App ──redirect  /login#token=<session-jwt>──▶ Browser (SPA stores token)
```

Two endpoints: `GET /api/auth/oidc/login` (start) and `GET /api/auth/oidc/callback` (finish).

---

## 2. State + nonce store (CSRF + replay protection)

State and nonce MUST survive across uvicorn workers (login can land on a different worker
than the callback) — so store them in Postgres, not an in-process dict. One-shot, 10-min TTL.

```sql
CREATE TABLE oidc_state (
  state      TEXT PRIMARY KEY,
  pid        TEXT,                                   -- which provider (multi-provider)
  nonce      TEXT,                                   -- validated against id_token nonce claim
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```python
def state_consume(state):                            # atomic one-shot take
    with conn() as c:
        c.execute("DELETE FROM oidc_state WHERE created_at < now() - interval '10 min'")
        row = c.execute("DELETE FROM oidc_state WHERE state=%s "
                        "AND created_at >= now() - interval '10 min' "
                        "RETURNING pid, nonce", (state,)).fetchone()
    return (bool(row), row and row["pid"], row and row["nonce"])
```

---

## 3. Discovery — tolerant of a pasted full URL  ★GOTCHA

The admin gives you an **Issuer URL**. Append `/.well-known/openid-configuration` yourself.
But admins paste the FULL discovery URL half the time → you'd build a DOUBLED path → 404 → 500.
Normalise first:

```python
_WK = "/.well-known/openid-configuration"
def issuer_base(issuer):
    iss = (issuer or "").strip().rstrip("/")
    if iss.endswith(_WK): iss = iss[:-len(_WK)].rstrip("/")
    return iss

def discover(issuer):
    r = httpx.get(issuer_base(issuer) + _WK, headers=UA, timeout=10)
    r.raise_for_status()
    return r.json()          # {authorization_endpoint, token_endpoint, jwks_uri, userinfo_endpoint, ...}
```

Keycloak issuer looks like `https://iam.example.com/realms/<realm>` — NO `.well-known`.

---

## 4. Browser User-Agent on EVERY IdP call  ★GOTCHA (the "403 Forbidden")

A WAF / reverse proxy in front of the IdP commonly 403s library User-Agents
(`Python-urllib`, and sometimes `python-httpx`). **Do NOT use `PyJWKClient`** — it fetches
JWKS via `urllib` and gets 403. Fetch everything with `httpx` and a normal UA:

```python
UA = {"User-Agent": "Mozilla/5.0 (App OIDC client)", "Accept": "application/json"}
```
Apply on discovery, token POST, JWKS, and userinfo.

---

## 5. Start endpoint — build auth URL

```python
@get("/auth/oidc/login")
def oidc_login(request, pid=None):
    prov = pick_provider(pid)                         # enabled provider with issuer+client_id
    if not prov: raise 403 "SSO disabled"
    disc = discover(prov["issuer"])
    state = token_urlsafe(24); nonce = token_urlsafe(24)
    state_put(state, prov["id"], nonce)
    params = {
      "client_id":     prov["client_id"],
      "response_type": "code",
      "scope":         prov.get("scopes", "openid email profile"),
      "redirect_uri":  redirect_uri(public_base(request)),   # see §6
      "state":         state,
      "nonce":         nonce,
    }
    return Redirect(disc["authorization_endpoint"] + "?" + urlencode(params))
```

---

## 6. redirect_uri — deterministic + proxy-aware  ★GOTCHA ("Invalid parameter: redirect_uri")

The `redirect_uri` must be **byte-identical** in three places: the auth request, the token
exchange, and the value registered in the IdP client. Behind a reverse proxy the request
often arrives as `http://` → you send `http://...` → IdP registered `https://...` → rejected.

Resolve the public base as: `PUBLIC_URL` env → `X-Forwarded-Proto`/`X-Forwarded-Host` →
raw request URL.

```python
def public_base(request):
    if PUBLIC_URL: return PUBLIC_URL.rstrip("/")               # set this in prod, e.g. https://itsm.example.com
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host  = (request.headers.get("x-forwarded-host")
             or request.headers.get("host") or "").split(",")[0].strip()
    if proto and host: return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")

def redirect_uri(base): return f"{base.rstrip('/')}/api/auth/oidc/callback"
```

**In the IdP client's Valid Redirect URIs, register exactly**
`https://<host>/api/auth/oidc/callback` (+ a `https://<host>/*` catch-all). No trailing slash.

---

## 7. Callback — exchange + verify + provision

```python
@get("/auth/oidc/callback")
def oidc_callback(request, code=None, state=None, error=None, error_description=None):
    # code/state OPTIONAL so an IdP error redirect becomes a message, not a 422  ★GOTCHA
    try:
        if error or not code or not state:
            return Redirect("/login?error=" + quote(error_description or error or "no login code"))
        base = public_base(request)
        ok, pid, nonce = state_consume(state)
        if not ok: return Redirect("/login?error=invalid+state")
        prov = provider(pid) or first_provider()
        try:
            info = exchange(prov, code, base, expected_nonce=nonce)   # {email, name, sub, email_verified}
        except OidcError as e:
            return Redirect("/login?error=" + quote(str(e)))
        try:
            u = find_or_create(info["email"], info["name"], "oidc",
                               oauth_sub=info["sub"], email_verified=info["email_verified"])
        except MergeBlocked as e:
            return Redirect("/login?error=" + quote(str(e)))
        if not u.active or u.role == "pending": return Redirect("/login?pending=1")
        touch_login(u.id); record_auth_method(u.id, "oidc"); add_to_users_group(u.id)
        return Redirect(f"/login#token={make_token(u)}")     # token in fragment, kept out of logs
    except Exception as e:
        log_traceback(); return Redirect("/login?error=" + quote(f"sso failed: {e}"))
    # NEVER let the callback return a raw 500 — wrap the whole body.  ★GOTCHA
```

### exchange()
```python
def exchange(prov, code, base, expected_nonce=None):
    disc = discover(prov["issuer"])
    tok = httpx.post(disc["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri(base),                  # MUST equal the one from login
        "client_id": prov["client_id"], "client_secret": prov.get("client_secret",""),
    }, headers={"User-Agent": UA["User-Agent"]}, timeout=10)
    if tok.status_code != 200: raise OidcError(f"token exchange failed: {tok.text[:200]}")
    tj = tok.json(); id_token = tj.get("id_token"); access_token = tj.get("access_token")
    if not id_token: raise OidcError("no id_token in response")

    # verify signature — fetch JWKS via httpx (NOT PyJWKClient), build key from matching kid
    try:
        jr = httpx.get(disc["jwks_uri"], headers=UA, timeout=10); jr.raise_for_status()
        keys = jr.json().get("keys", [])
        kid = jwt.get_unverified_header(id_token).get("kid")
        jwk = next((k for k in keys if k.get("kid")==kid), None) or (keys[0] if keys else None)
        if not jwk: raise OidcError("no signing key in JWKS")
        signing_key = jwt.PyJWK(jwk).key
        claims = jwt.decode(id_token, signing_key, algorithms=["RS256","ES256"],
                            issuer=issuer_base(prov["issuer"]),
                            options={"verify_at_hash": False, "verify_aud": False})  # ★see §8
    except OidcError: raise
    except jwt.PyJWTError as e: raise OidcError(f"id_token verify failed: {e}")
    except Exception as e:      raise OidcError(f"id_token verify error: {e}")

    # audience — Keycloak puts aud="account", client in azp  ★GOTCHA
    aud = claims.get("aud"); aud = aud if isinstance(aud, list) else [aud]
    if prov["client_id"] not in aud and claims.get("azp") != prov["client_id"]:
        raise OidcError("id_token audience does not match client id")

    # nonce replay protection
    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise OidcError("id_token nonce mismatch")

    # email: id_token first, fall back to /userinfo  ★GOTCHA (some clients omit email from id_token)
    if not claims.get("email") and disc.get("userinfo_endpoint") and access_token:
        try:
            ur = httpx.get(disc["userinfo_endpoint"],
                           headers={**UA, "Authorization": f"Bearer {access_token}"}, timeout=10)
            if ur.status_code == 200:
                for k,v in ur.json().items(): claims.setdefault(k, v)
        except Exception: pass

    email = claims.get("email")
    if not email: raise OidcError("no email claim in id_token or userinfo")
    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    ev = claims.get("email_verified"); email_verified = ev is True or str(ev).lower()=="true"
    return {"email": email, "name": name, "sub": claims.get("sub",""), "email_verified": email_verified}
```

---

## 8. Audience — why `verify_aud=False`  ★GOTCHA

Keycloak's id_token `aud` is frequently `"account"` (or an array omitting your client); the
real client id lives in `azp`. If you let pyjwt enforce `audience=client_id` it raises
`InvalidAudienceError` → uncaught → 500. So disable pyjwt's aud check and validate yourself:
accept if `client_id in aud` OR `azp == client_id`. (Optionally add a Keycloak "Audience"
protocol mapper so `aud` contains the client — but don't rely on it.)

---

## 9. find_or_create + merge-by-email  ★shared with LDAP

```python
def find_or_create(email, name, source, oauth_sub=None, email_verified=False):
    u = get_by_email(email)
    if u:
        if u.auth_source == source: return u                 # same provider, fine
        if get_config().get("merge_by_email"):               # match Open WebUI: merge on email
            return u                                          # (we do NOT also require email_verified)
        raise MergeBlocked(f"{email} already exists via '{u.auth_source}'")
    return create_user(email, name, source, oauth_sub=oauth_sub)   # JIT provision
```

Open WebUI's `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true` = this flag. It merges on the email claim
alone (corporate IdP email is authoritative). `email_verified` still flows through if you want
a stricter mode later.

---

## 10. Provider config

```jsonc
{
  "id": "298f4c1f", "name": "Office 365", "enabled": true,
  "provider": "generic",                      // icon only
  "issuer":    "https://iam.example.com/realms/city-group",   // BASE — no .well-known
  "client_id": "MyApp-Client",
  "client_secret": "••••••",                  // encrypted at rest
  "scopes": "openid email profile",
  "label": ""                                 // login button text; blank = auto
}
```

Open WebUI mapping:

| Open WebUI env | This field | Note |
|---|---|---|
| `OPENID_PROVIDER_URL` | `issuer` | OWUI wants the FULL `.well-known` URL; here paste the BASE (we append). Normaliser tolerates either. |
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | `client_id` / `client_secret` | **Each app needs its OWN client** — never share one client across apps. |
| `OAUTH_SCOPES` | `scopes` | |
| `OAUTH_MERGE_ACCOUNTS_BY_EMAIL` | `merge_by_email` (global) | |
| `OPENID_REDIRECT_URI` | derived from `PUBLIC_URL` + `/api/auth/oidc/callback` | |

---

## 11. Keycloak client setup checklist

1. Clients → Create client → **Client ID = your app's own id** (not another app's).
2. Client authentication = ON (confidential) → copy the **Client secret**.
3. Valid redirect URIs: `https://<host>/api/auth/oidc/callback` (+ `https://<host>/*`).
4. Standard flow = ON (authorization code).
5. (Optional) Dedicated scope → add an **Audience** mapper → Included Client Audience = the client.
6. Ensure the `email` claim is mapped (email scope + a mail source in user federation).

---

## 12. Landmines checklist (each cost a debug round)

- ★ **Doubled `.well-known`** — normalise the issuer (§3). Symptom: 404 discovery → 500.
- ★ **403 Forbidden on JWKS** — WAF blocks urllib UA. Use httpx + browser UA, not PyJWKClient (§4).
- ★ **Invalid parameter: redirect_uri** — scheme/host mismatch behind proxy. `PUBLIC_URL` +
  X-Forwarded headers (§6); register the EXACT https string in the IdP.
- ★ **InvalidAudienceError → 500** — Keycloak `aud=account`. `verify_aud=False` + check aud/azp (§8).
- ★ **422 on callback** — IdP error redirect has no code/state. Make them optional (§7).
- ★ **Raw 500 on callback** — wrap the whole handler; every failure → `/login?error=` (§7).
- ★ **State lost across workers** — store in Postgres one-shot, not memory (§2).
- **No email claim** — some clients omit email from the id_token → userinfo fallback (§7).
- **Reusing another app's client** — each app = its own client + own redirect. Never share.
- Put the session token in the URL **fragment** (`#token=`), not the query, so it stays out of
  server/proxy logs.
