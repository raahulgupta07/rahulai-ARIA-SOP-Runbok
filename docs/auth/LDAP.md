# LDAP / Active Directory Login — Build Spec

Bind-search-rebind auth against AD/OpenLDAP. Modeled on Open WebUI's LDAP flow.
Depends on the LOCAL_LOGIN spec (shares `users`, `issue()`, merge-by-email).

Library: `ldap3` (pure-python, no system libldap needed).

---

## 1. The flow — service-bind → search → rebind

Never bind the end-user directly by a guessed DN. Always:

1. **Service bind** — connect as a read-only service account (`bind_dn` + `bind_password`).
2. **Search** — find the user's entry by their typed login; read their real DN + email + name.
3. **Rebind as the user** — open a NEW connection binding with the user's DN + the password
   they typed. Success = password correct. Then unbind.

Rebinding as the user is what actually verifies the password — the service account is only
used to look up the DN.

```python
import ssl
from ldap3 import Server, Connection, ALL, SUBTREE, Tls
from ldap3.utils.conv import escape_filter_chars

class LdapError(Exception): pass

def _server(lc):
    use_ssl = bool(lc.get("use_ssl"))                       # LDAPS (636)
    port = int(lc.get("port") or (636 if use_ssl else 389))
    tls = Tls(validate=ssl.CERT_NONE) if (use_ssl or lc.get("start_tls")) else None
    return Server(lc["host"], port=port, use_ssl=use_ssl, tls=tls, get_info=ALL)

def _connect(server, user, password, lc):
    if lc.get("start_tls") and not lc.get("use_ssl"):      # StartTLS on 389
        conn = Connection(server, user=user, password=password)
        if not conn.start_tls(): raise LdapError("StartTLS failed")
        if not conn.bind():      raise LdapError("bind failed after StartTLS")
        return conn
    return Connection(server, user=user, password=password, auto_bind=True)

def ldap_login(lc, username, password) -> dict:            # -> {email, name}
    if not lc.get("host"):  raise LdapError("LDAP not configured")
    if not password:        raise LdapError("password required")   # empty pw = anonymous bind = false positive!
    server = _server(lc)

    # 1) service bind
    try:
        svc = _connect(server, lc["bind_dn"], lc["bind_password"], lc)
    except Exception as e:
        raise LdapError(f"service bind failed: {e}")

    # 2) search (injection-safe)
    flt = _build_filter(lc, username)
    email_attr = lc.get("email_attr", "mail")
    name_attr  = lc.get("name_attr",  "cn")
    svc.search(lc["base_dn"], flt, search_scope=SUBTREE, attributes=[email_attr, name_attr])
    if not svc.entries:
        svc.unbind(); raise LdapError("user not found")
    entry = svc.entries[0]
    user_dn = entry.entry_dn
    email = str(entry[email_attr].value) if email_attr in entry else ""
    name  = str(entry[name_attr].value)  if name_attr  in entry else username
    svc.unbind()
    if not email: raise LdapError(f"user has no '{email_attr}' attribute")

    # 3) rebind AS the user to verify the password
    try:
        uc = _connect(server, user_dn, password, lc); uc.unbind()
    except Exception:
        raise LdapError("invalid credentials")
    return {"email": email, "name": name}
```

---

## 2. Injection-safe filter — accept username OR email/UPN

Users may type either `jdoe` or `jdoe@corp.com`. Match both, safely escaped:

```python
def _build_filter(lc, username):
    raw = (username or "").strip()
    uid = raw.split("@", 1)[0]                              # bare sAMAccountName
    u_esc, uid_esc = escape_filter_chars(raw), escape_filter_chars(uid)
    uname_attr = lc.get("username_attr") or "sAMAccountName"
    mail_attr  = lc.get("email_attr", "mail")
    ident = (f"(|({uname_attr}={uid_esc})"
             f"(userPrincipalName={u_esc})"
             f"({mail_attr}={u_esc}))")
    extra = (lc.get("search_filter") or "").strip()         # e.g. (memberOf=CN=ITSM,...)
    return f"(&{ident}{extra})" if extra else ident
```

**ALWAYS `escape_filter_chars`** the user input — otherwise `*)(uid=*` style LDAP injection.

`search_filter` is an optional extra constraint ANDed in for access control (only members of
a group may log in).

---

## 3. Config — one dict per directory (multi-directory supported)

Store a LIST of directory dicts in `auth_config.ldap_directories`:

```jsonc
{
  "name": "HQ Active Directory",
  "enabled": true,
  "host": "10.16.73.60",
  "port": 389,
  "bind_dn": "CN=svc-aria,OU=Service,DC=corp,DC=com",
  "bind_password": "••••••",           // encrypted at rest, never returned to the client
  "base_dn": "DC=corp,DC=com",
  "username_attr": "sAMAccountName",   // AD; use "uid" for OpenLDAP
  "email_attr": "mail",                // or "userPrincipalName"
  "name_attr": "cn",
  "search_filter": "",                 // optional extra, e.g. "(memberOf=CN=ITSM,OU=Groups,DC=corp,DC=com)"
  "use_ssl": false,                    // LDAPS on 636
  "start_tls": false                   // StartTLS on 389
}
```

Open WebUI mapping (so you can copy an existing OpenWebUI LDAP config 1:1):

| Open WebUI env | This field |
|---|---|
| `LDAP_SERVER_HOST` / `LDAP_SERVER_PORT` | `host` / `port` |
| `LDAP_APP_DN` / `LDAP_APP_PASSWORD` | `bind_dn` / `bind_password` |
| `LDAP_SEARCH_BASE` | `base_dn` |
| `LDAP_ATTRIBUTE_FOR_USERNAME` | `username_attr` |
| `LDAP_ATTRIBUTE_FOR_MAIL` | `email_attr` |
| `LDAP_SEARCH_FILTER` | `search_filter` |
| `LDAP_USE_TLS` / `LDAP_CIPHERS` | `use_ssl` / (fixed CERT_NONE) |

---

## 4. Endpoints

```
POST /api/auth/ldap              {username, password, directory?}  -> issue token
POST /api/admin/auth-config/test-ldap  {directory dict}            -> {ok} | {ok:false,error}
```

Route:
```python
@post("/auth/ldap")
def ldap(body):
    dirs = [d for d in ldap_directories() if d.enabled and d.host]
    if not dirs: raise 403 "LDAP disabled"
    directory = pick(body.directory) or dirs[0]
    try:
        info = ldap_login(directory, body.username, body.password)
    except LdapError as e:
        raise 401 str(e)
    # directory mail attribute is trusted -> treat as verified email
    try:
        u = find_or_create(info["email"], info["name"], "ldap", email_verified=True)
    except MergeBlocked as e:
        raise 409 str(e)
    return issue(u, "ldap")
```

`find_or_create` — JIT-provision on first login, merge by email if enabled (see OIDC spec §7,
same function). LDAP's mail attribute is authoritative → pass `email_verified=True`.

---

## 5. Landmines

- **Empty password = anonymous bind = false-positive login.** Reject empty password BEFORE
  binding. Many AD servers treat bind with empty password as a successful anonymous bind.
- **Always escape** the search input (`escape_filter_chars`) — LDAP injection is real.
- **Rebind, don't compare.** Verify the password by binding as the user, never by reading a
  password hash (AD won't give you one anyway).
- **StartTLS vs LDAPS** are different: `use_ssl`=LDAPS on 636 (TLS from connect); `start_tls`
  = plain 389 then upgrade. Don't set both.
- Internal AD usually has a private CA → `Tls(validate=ssl.CERT_NONE)` (still encrypted).
  Expose a `validate_cert` toggle only if you need public-CA enforcement.
- **sAMAccountName is the bare name** (`jdoe`), `userPrincipalName`/`mail` is the full form
  (`jdoe@corp.com`). Strip the domain before matching sAMAccountName.
- Test with the admin "Test connection" (service bind only) before any user tries to log in.
