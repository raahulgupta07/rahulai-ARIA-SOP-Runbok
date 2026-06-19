"""LDAP / Active Directory: service-bind → search user → rebind as user."""
from ldap3 import Server, Connection, ALL, SUBTREE


class LdapError(Exception):
    pass


def ldap_login(cfg: dict, username: str, password: str) -> dict:
    """Returns {email, name} on success, raises LdapError otherwise.
    cfg = auth_config['ldap'] block."""
    lc = cfg.get("ldap", cfg)   # accepts a single directory dict OR legacy {ldap:{...}}
    if not lc.get("host"):
        raise LdapError("LDAP not configured")
    if not password:
        raise LdapError("password required")

    server = Server(lc["host"], port=int(lc.get("port", 389)), get_info=ALL)

    # 1) bind as the service account
    try:
        svc = Connection(server, user=lc["bind_dn"], password=lc["bind_password"],
                         auto_bind=True)
    except Exception as e:
        raise LdapError(f"service bind failed: {e}")

    # 2) search for the user
    flt = lc["user_filter"].replace("{username}", username)
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
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()
    except Exception:
        raise LdapError("invalid credentials")

    return {"email": email, "name": name}


def ldap_test(cfg: dict) -> dict:
    """Admin 'Test connection' — just the service bind."""
    lc = cfg.get("ldap", cfg)   # accepts a single directory dict OR legacy {ldap:{...}}
    server = Server(lc["host"], port=int(lc.get("port", 389)), get_info=ALL)
    try:
        c = Connection(server, user=lc["bind_dn"], password=lc["bind_password"],
                       auto_bind=True)
        c.unbind()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
