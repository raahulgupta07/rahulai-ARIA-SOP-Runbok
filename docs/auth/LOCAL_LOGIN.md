# Local Login — Build Spec

Email + password auth with bcrypt, signup + admin approval, brute-force lockout.
This is the baseline every other method (LDAP, SSO) layers on top of. Build this first.

---

## 1. Data model — `users` table

```sql
CREATE TABLE users (
  id            SERIAL PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,          -- lowercased before store
  name          TEXT NOT NULL DEFAULT '',
  password_hash TEXT,                           -- bcrypt; NULL for LDAP/SSO-only users
  role          TEXT NOT NULL DEFAULT 'user',   -- superadmin | admin | user | pending | widget
  auth_source   TEXT NOT NULL DEFAULT 'local',  -- local | ldap | oidc  (the FIRST method that created the row)
  auth_methods  TEXT[] DEFAULT '{}',            -- EVERY method this email has used (merge-by-email)
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login    TIMESTAMPTZ
);
```

Roles:
- `superadmin` — full control incl. Settings. Exactly one, seeded from env on boot.
- `admin` — content + user management, NOT auth/settings secrets.
- `user` — normal.
- `pending` — signed up, awaiting admin approval (can't log in yet).
- `widget` — embed-only principal, never a real login.

`auth_source` = the method that first created the row. `auth_methods` = union of all methods
(so a person who used local + LDAP + SSO is ONE row showing all three). See merge-by-email
in the LDAP/OIDC specs.

---

## 2. Password hashing

Use bcrypt. Never store plaintext, never log the password.

```python
# security.py
import bcrypt
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
def verify_password(pw: str, h: str | None) -> bool:
    if not h: return False
    return bcrypt.checkpw(pw.encode(), h.encode())
```

Session token: signed JWT (HS256) with a server secret from env, `{sub: id, role, exp}`.
Never derive the session secret from a hardcoded fallback — generate + persist one if absent.

---

## 3. Config (org-wide toggles)

Stored in a single JSON config row/blob (`auth_config`), NOT env, so admins flip them live:

```jsonc
{
  "enable_local":  true,    // email+password login on/off
  "enable_signup": true,    // self-service signup on/off
  "sso_enabled":   true,    // (see OIDC spec)
  "ldap_enabled":  true,    // (see LDAP spec)
  "merge_by_email": false,  // (see LDAP/OIDC spec)
  "default_role":  "user"   // "user" = auto-active, "pending" = needs approval
}
```

Public subset served to the login page (NO secrets):
```python
def public_config():
    c = get_config()
    return {
      "enable_local":  bool(c.get("enable_local", True)),
      "enable_signup": bool(c.get("enable_signup", True)),
      "enable_ldap":   bool(dirs)  and bool(c.get("ldap_enabled", True)),   # only if a directory exists
      "enable_oidc":   bool(provs) and bool(c.get("sso_enabled", True)),    # only if a provider exists
    }
```
Login page renders ONLY the methods that are enabled AND configured.

---

## 4. Endpoints

```
POST /api/auth/signup   {email, password, name?}     -> issue token OR {pending:true}
POST /api/auth/login    {email, password, from_admin?}-> issue token
GET  /api/auth/config                                 -> public_config()
GET  /api/auth/me                                     -> current user + features + capabilities
```

### signup
```python
if not cfg["enable_local"] or not cfg["enable_signup"]:
    raise 403 "signup disabled"
if get_by_email(email): raise 409 "email already registered"
u = create_user(email, name, "local", password_hash=hash_password(password))  # role = default_role
if u.role == "pending": return {"pending": True}          # notify admins
return issue(u, "local")
```

### login
```python
if not cfg["enable_local"] and not from_admin:
    raise 403 "local login disabled"
if recent_fail_count(email, LOCK_MIN) >= MAX_FAIL:        # brute-force lockout
    raise 429 "too many failed attempts"
u = get_by_email(email)
if not u or u.auth_source != "local" or not verify_password(password, u.password_hash):
    log_fail(email); raise 401 "invalid email or password"
return issue(u, "local")
```

### issue() — the single chokepoint every method returns through
```python
def issue(u, method):
    if not u.active:          raise 403 "account disabled"
    if u.role == "pending":   raise 403 "account awaiting admin approval"
    touch_login(u.id)
    record_auth_method(u.id, method)      # union into auth_methods[]
    add_to_users_group(u.id)              # default group membership (if you have groups)
    return {"token": make_token(u), "user": public(u)}
```

---

## 5. Superadmin seed (boot)

On every startup, ensure exactly one superadmin exists, reset from env so you can never
lock yourself out:

```python
def ensure_superadmin():
    email = env("SUPERADMIN_EMAIL"); pw = env("SUPERADMIN_PASSWORD")
    u = get_by_email(email)
    if u: update(u.id, role="superadmin", active=True, password_hash=hash_password(pw))
    else: create_user(email, "Super Admin", "local", password_hash=hash_password(pw), role="superadmin")
```

---

## 6. The admin escape hatch (CRITICAL — prevents total lockout)

If an admin turns OFF every login method (local + LDAP + SSO), nobody can get in.
Provide a hidden route `/login/admin` that sets `from_admin=true`, which bypasses the
`enable_local` toggle for **admin/superadmin only**:

```python
# inside login(), after the password check succeeds:
if from_admin and not cfg["enable_local"] and u.role not in ("admin", "superadmin"):
    raise 403 "admin sign-in only"     # a normal user can't use the escape hatch
```

- The `/login/admin` link is NOT advertised on the public login page — reachable by URL only.
- BOTH `admin` and `superadmin` must qualify (a superadmin who disabled local login must
  still get back in). This exact bug (`role != "admin"` excluded superadmin) caused a real
  full lockout — use `role not in ("admin","superadmin")`.

---

## 7. Landmines (learned the hard way)

- **Superadmin lockout**: escape hatch must allow BOTH admin and superadmin. Always test:
  disable all 3 methods, then log in via `/login/admin`.
- **Recovery** if locked out anyway (run in the container):
  `python -c "from app.auth import store; c=store.get_config(); c['enable_local']=True; store.save_config(c)"`
- **auth_source vs auth_methods**: `auth_source` never changes after creation; `auth_methods`
  grows. Don't gate login on `auth_methods` — gate on the actual credential check.
- **Config is DB-owned, not env** — so admins change it live. Env only seeds defaults + superadmin.
- Lowercase the email on every read/write, or you get duplicate accounts.
