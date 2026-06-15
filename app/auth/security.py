"""Password hashing (bcrypt) + our JWT sign/verify."""
import datetime as dt

import bcrypt
import jwt

from ..config import JWT_SECRET, JWT_TTL_DAYS, EMBED_TOKEN_TTL_MIN


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def make_token(user: dict) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "iat": now,
        "exp": now + dt.timedelta(days=JWT_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def make_widget_token(user_id: int, key_id: int, visitor_id: str,
                      name: str | None = None) -> str:
    """Short-lived JWT for an embedded-widget visitor. typ='widget' so the
    principal guard can tell it apart from a member login token."""
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "typ": "widget",
        "role": "widget",
        "kid": key_id,
        "vid": visitor_id,
        "name": name or "",
        "iat": now,
        "exp": now + dt.timedelta(minutes=EMBED_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
