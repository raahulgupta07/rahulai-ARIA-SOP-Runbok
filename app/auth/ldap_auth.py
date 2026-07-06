"""LDAP / Active Directory: service-bind → search user → rebind as user.

Login accepts EITHER a bare username OR a full email/UPN (OpenWebUI model):
the typed value is matched, injection-safe, against the username attribute
(bare uid), plus userPrincipalName and mail, so both forms resolve to the
same account. An optional extra `search_filter` is ANDed for access control.
"""
import ssl

from ldap3 import Server, Connection, ALL, SUBTREE, Tls
from ldap3.utils.conv import escape_filter_chars


class LdapError(Exception):
    pass


def _server(lc: dict) -> Server:
    """Build the Server, honoring TLS. use_ssl → LDAPS (usually port 636).
    Cert validation is disabled by default (internal AD often uses a private CA);
    the channel is still encrypted. Port defaults to 636 when use_ssl else 389."""
    use_ssl = bool(lc.get("use_ssl"))
    port = int(lc.get("port") or (636 if use_ssl else 389))
    tls = Tls(validate=ssl.CERT_NONE) if (use_ssl or lc.get("start_tls")) else None
    return Server(lc["host"], port=port, use_ssl=use_ssl, tls=tls, get_info=ALL)


def _connect(server: Server, user: str, password: str, lc: dict) -> Connection:
    """Bind, applying StartTLS first when configured (and not already LDAPS)."""
    if lc.get("start_tls") and not lc.get("use_ssl"):
        conn = Connection(server, user=user, password=password)
        if not conn.start_tls():
            raise LdapError("StartTLS failed")
        if not conn.bind():
            raise LdapError("bind failed after StartTLS")
        return conn
    return Connection(server, user=user, password=password, auto_bind=True)


def _build_filter(lc: dict, username: str) -> str:
    """OpenWebUI-style, injection-safe. Matches bare uid OR full email/UPN.

    - username_attr (LDAP_ATTRIBUTE_FOR_USERNAME, def sAMAccountName): matched
      against the DOMAIN-STRIPPED login (AD sAMAccountName is the bare name).
    - userPrincipalName + email_attr (LDAP_ATTRIBUTE_FOR_MAIL, def mail):
      matched against the FULL typed value so an email/UPN login also resolves.
    - search_filter (LDAP_SEARCH_FILTER): optional extra constraint, ANDed.
    - Legacy `user_filter` with a `{username}`/`{uid}` placeholder is still
      honored when the admin authored a genuinely custom one.
    """
    raw = (username or "").strip()
    uid = raw.split("@", 1)[0]                       # bare sAMAccountName
    u_esc, uid_esc = escape_filter_chars(raw), escape_filter_chars(uid)

    uname_attr = lc.get("username_attr") or "sAMAccountName"
    mail_attr = lc.get("email_attr", "mail")

    ident = (f"(|({uname_attr}={uid_esc})"
             f"(userPrincipalName={u_esc})"
             f"({mail_attr}={u_esc}))")

    # Honor a real custom filter (but NOT the legacy single-attr default).
    custom = (lc.get("user_filter") or "").strip()
    if custom and "{username}" in custom and custom not in (
            "(sAMAccountName={username})", "(uid={username})"):
        ident = custom.replace("{uid}", uid_esc).replace("{username}", u_esc)

    extra = (lc.get("search_filter") or "").strip()
    return f"(&{ident}{extra})" if extra else ident


def ldap_login(cfg: dict, username: str, password: str) -> dict:
    """Returns {email, name} on success, raises LdapError otherwise.
    cfg = auth_config['ldap'] block."""
    lc = cfg.get("ldap", cfg)   # accepts a single directory dict OR legacy {ldap:{...}}
    if not lc.get("host"):
        raise LdapError("LDAP not configured")
    if not password:
        raise LdapError("password required")

    server = _server(lc)

    # 1) bind as the service account
    try:
        svc = _connect(server, lc["bind_dn"], lc["bind_password"], lc)
    except LdapError:
        raise
    except Exception as e:
        raise LdapError(f"service bind failed: {e}")

    # 2) search for the user (injection-safe, username OR full email both work)
    flt = _build_filter(lc, username)
    email_attr = lc.get("email_attr", "mail")
    name_attr = lc.get("name_attr", "cn")
    svc.search(lc["base_dn"], flt, search_scope=SUBTREE,
               attributes=[email_attr, name_attr])
    if not svc.entries:
        svc.unbind()
        raise LdapError("user not found")
    entry = svc.entries[0]
    user_dn = entry.entry_dn
    email = str(entry[email_attr].value) if email_attr in entry else ""
    name = str(entry[name_attr].value) if name_attr in entry else username
    svc.unbind()

    if not email:
        raise LdapError(f"user has no '{email_attr}' attribute")

    # 3) rebind AS the user to verify the password
    try:
        user_conn = _connect(server, user_dn, password, lc)
        user_conn.unbind()
    except Exception:
        raise LdapError("invalid credentials")

    return {"email": email, "name": name}


def ldap_test(cfg: dict) -> dict:
    """Admin 'Test connection' — just the service bind (honors TLS)."""
    lc = cfg.get("ldap", cfg)   # accepts a single directory dict OR legacy {ldap:{...}}
    try:
        c = _connect(_server(lc), lc["bind_dn"], lc["bind_password"], lc)
        c.unbind()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
