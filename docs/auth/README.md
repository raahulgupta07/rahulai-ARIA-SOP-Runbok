# Auth Build Specs

Reference blueprints to rebuild this app's authentication in another project.
Each is self-contained but they share one `users` table, one `issue()` chokepoint,
and one `find_or_create` merge-by-email path.

**Build order:**

1. [LOCAL_LOGIN.md](LOCAL_LOGIN.md) — email + bcrypt, signup + approval, roles, superadmin
   seed, the admin escape hatch. **Build this first** — the others extend it.
2. [LDAP.md](LDAP.md) — AD/OpenLDAP bind-search-rebind, injection-safe filter, TLS, JIT provision.
3. [KEYCLOAK_OIDC.md](KEYCLOAK_OIDC.md) — OIDC authorization-code SSO, all production gotchas
   (issuer normalise, WAF UA, redirect_uri scheme, Keycloak aud, nonce, userinfo fallback).

**Cross-cutting (defined in LOCAL, used by all three):**
- `users` table with `auth_source` (first method) + `auth_methods[]` (all methods).
- `issue(user, method)` — the single login chokepoint (checks active/pending, records method, mints session JWT).
- `find_or_create(email, name, source, email_verified)` — JIT provision + merge-by-email.
- Config is DB-owned (live-editable by admins), env only seeds defaults + superadmin.

**Stack:** FastAPI · Postgres · `bcrypt` · `ldap3` · `httpx` · `pyjwt`.
Each method has an org-wide on/off toggle; the login page renders only the enabled+configured ones.
