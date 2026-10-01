"""
title: Aria (Runbooks)
author: CityGPT platform
version: 0.3.0
description: Ask City Agent Aria (company runbooks / SOPs) as the signed-in user. No shared keys.
requirements: httpx
"""

# Paste into OpenWebUI → Admin → Functions → New Function, save, enable.
#
# How it works: OpenWebUI hands this function the signed-in user's Keycloak
# access token (__oauth_token__). It is forwarded unchanged to Aria's
# /api/ask/stream, which verifies it against the Keycloak realm (Aria 2.26.0+,
# Settings → Authentication → "Access from other apps" ON, plus EITHER an app
# key for this server in the aria_app_key valve (Aria 2.27.0+, recommended) OR
# this server's OAUTH_CLIENT_ID in Aria's allow-list). Aria answers AS that user, so each person
# only sees the runbooks their own account may see.
#
# Aria's raw token stream contains its internal citation lines (PAGES: …,
# FOLLOWUPS: …) which its own UI swaps for the cleaned text when the answer
# finishes. By default this pipe therefore shows Aria's live progress steps
# and writes the finished, cleaned answer once (live_stream = False).

import base64
import json
import os
import re
import time
from typing import AsyncGenerator, Optional

import httpx
from pydantic import BaseModel, Field

_SENTINELS = ("PAGES:", "FOLLOWUPS:")


class Pipe:
    class Valves(BaseModel):
        model_name: str = Field(
            default="Aria · Runbooks",
            description="Name shown in the model picker (a name set under Admin → Models wins).",
        )
        base_url: str = Field(
            default="https://itsm.citygpt.xyz",
            description="Aria base URL. A trailing slash is stripped.",
        )
        aria_app_key: str = Field(
            default="",
            description=(
                "This server's Aria app key (Aria → Settings → Authentication → App keys). "
                "Identifies this CityGPT server to Aria; the person is still identified by "
                "their own sign-in. Leave blank only if Aria lists this server's client ID instead."
            ),
        )
        answer_mode: str = Field(
            default="auto", description="quick | deep | auto (Aria picks)."
        )
        request_timeout: int = Field(
            default=180, description="Seconds to wait on one answer, end to end."
        )
        live_stream: bool = Field(
            default=False,
            description=(
                "Stream words as Aria writes them. Off = show progress steps, then "
                "the finished clean answer (recommended: citations render correctly)."
            ),
        )
        show_steps: bool = Field(default=True, description="Show Aria's progress steps.")
        show_sources: bool = Field(default=True, description="List the cited runbook pages.")
        show_followups: bool = Field(default=True, description="Offer Aria's follow-up questions.")
        auto_refresh_token: bool = Field(
            default=True,
            description=(
                "Renew the Keycloak access token when it has expired (it lives ~5 min; "
                "the CityGPT login lasts days). Needs 'Revoke Refresh Token' OFF on the client."
            ),
        )
        oauth_token_url: str = Field(
            default="", description="Keycloak token endpoint. Blank = derive from OPENID_PROVIDER_URL."
        )
        oauth_client_id: str = Field(
            default="", description="Blank = read OAUTH_CLIENT_ID from the environment."
        )
        oauth_client_secret: str = Field(
            default="", description="Blank = read OAUTH_CLIENT_SECRET from the environment."
        )

    CACHE_LIMIT = 500

    def __init__(self):
        self.type = "manifold"
        self.id = "aria"
        self.valves = self.Valves()
        self._threads: dict = {}   # OpenWebUI chat id -> Aria conversation_id
        self._tokens: dict = {}    # OpenWebUI user id -> {access_token, refresh_token}
        self._titled: set = set()  # chats already renamed

    def _trim(self, store) -> None:
        while len(store) > self.CACHE_LIMIT:
            if isinstance(store, set):
                store.pop()
            else:
                store.pop(next(iter(store)))

    def pipes(self):
        return [{"id": "aria", "name": self.valves.model_name}]

    @property
    def _base(self) -> str:
        # "//api/ask/stream" is a 404 that looks like a missing route.
        return (self.valves.base_url or "").rstrip("/")

    # ---- identity -------------------------------------------------------

    @staticmethod
    def _seconds_left(token: Optional[str]) -> Optional[int]:
        """Seconds until this access token expires, read locally. None if unreadable."""
        if not token:
            return None
        try:
            part = token.split(".")[1]
            part += "=" * (-len(part) % 4)
            claims = json.loads(base64.urlsafe_b64decode(part))
            return int(claims["exp"]) - int(time.time())
        except Exception:
            return None

    def _token_endpoint(self) -> str:
        if self.valves.oauth_token_url:
            return self.valves.oauth_token_url
        url = os.environ.get("OPENID_PROVIDER_URL", "")
        if not url:
            return ""
        root = url.split("/.well-known/")[0].rstrip("/")
        return f"{root}/protocol/openid-connect/token"

    async def _renew(self, user_id: str, oauth: Optional[dict]) -> Optional[str]:
        """Refresh-token grant. Never raises; failure → None (fail closed)."""
        cached = self._tokens.get(user_id) or {}
        refresh = cached.get("refresh_token") or (oauth or {}).get("refresh_token")
        endpoint = self._token_endpoint()
        client_id = self.valves.oauth_client_id or os.environ.get("OAUTH_CLIENT_ID", "")
        secret = self.valves.oauth_client_secret or os.environ.get("OAUTH_CLIENT_SECRET", "")
        if not (refresh and endpoint and client_id):
            return None
        form = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}
        if secret:
            form["client_secret"] = secret
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(endpoint, data=form, headers={"User-Agent": "Mozilla/5.0 (CityGPT Aria pipe)"})
            if r.status_code != 200:
                self._tokens.pop(user_id, None)
                return None
            body = r.json()
        except Exception:
            return None
        access = body.get("access_token")
        if not access:
            return None
        self._tokens[user_id] = {
            "access_token": access,
            "refresh_token": body.get("refresh_token") or refresh,
        }
        self._trim(self._tokens)
        return access

    async def _usable_token(self, user: Optional[dict], oauth: Optional[dict]) -> Optional[str]:
        user_id = str((user or {}).get("id") or "")
        token = (oauth or {}).get("access_token")
        cached = self._tokens.get(user_id) or {}
        # once we have renewed, OpenWebUI's stored copy is the stale one
        if cached.get("access_token"):
            left = self._seconds_left(cached["access_token"])
            if left is None or left > 30:
                token = cached["access_token"]
        if not self.valves.auto_refresh_token or not user_id:
            return token
        left = self._seconds_left(token)
        if token and (left is None or left > 30):
            return token
        renewed = await self._renew(user_id, oauth)
        if renewed:
            return renewed
        # never send a token we KNOW is dead — a clear "session ended" beats a 401
        if token and left is not None and left <= 0:
            return None
        return token

    # ---- helpers --------------------------------------------------------

    def _headers(self, token: str) -> dict:
        h = {"Authorization": f"Bearer {token}", "Accept": "application/x-ndjson"}
        key = (self.valves.aria_app_key or "").strip()
        if key:
            h["X-Aria-App-Key"] = key
        return h

    @staticmethod
    def _doc_title(raw: str) -> str:
        """'SOP_012_New_Staff_Account_Setup.pdf' -> 'New Staff Account Setup'."""
        name = re.sub(r"\.(pdf|png|jpe?g|docx?)$", "", raw or "", flags=re.I)
        name = re.sub(r"^(sop|doc)?[_\-\s]*\d+[_\-\s]+", "", name, flags=re.I)
        name = re.sub(r"[_]+", " ", name).strip()
        return name or (raw or "Document")

    @staticmethod
    def _strip_internal(text: str) -> str:
        """Drop Aria's trailing PAGES:/FOLLOWUPS: lines from raw streamed text."""
        out = []
        for line in (text or "").splitlines():
            if line.strip().upper().startswith(_SENTINELS):
                continue
            out.append(line)
        return "\n".join(out).rstrip()

    # ---- the call -------------------------------------------------------

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __oauth_token__: Optional[dict] = None,
        __event_emitter__=None,
        __chat_id__: str = "",
        **_,
    ):
        token = await self._usable_token(__user__, __oauth_token__)
        if not token:
            return (
                "**Your sign-in has expired.** Sign out of CityGPT, sign in again and re-send.\n\n"
                "Aria only answers with your identity — it shows each person the runbooks "
                "their own account may see."
            )

        question = ""
        for m in reversed(body.get("messages") or []):
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, list):  # multimodal message: take the text parts
                    c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                question = (c or "").strip()
                break
        if not question:
            return "Ask a question about a runbook or procedure."

        async def status(text: str, done: bool = False) -> None:
            if __event_emitter__ and (self.valves.show_steps or done):
                await __event_emitter__({"type": "status", "data": {"description": text, "done": done}})

        # empty chat id = unsaved chat; caching under "" would share one conversation
        cacheable = bool(__chat_id__)
        conv_id = self._threads.get(__chat_id__) if cacheable else None

        async def run() -> AsyncGenerator[str, None]:
            nonlocal conv_id
            raw = ""        # every token Aria sent (for short-circuit answers w/o `clean`)
            shown = ""      # what live mode has already written
            try:
                await status("Asking Aria…")
                timeout = httpx.Timeout(self.valves.request_timeout, connect=15)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    for attempt in (0, 1):
                        payload = {"q": question, "mode": self.valves.answer_mode}
                        if conv_id:
                            payload["conversation_id"] = conv_id
                        async with client.stream(
                            "POST",
                            f"{self._base}/api/ask/stream",
                            headers=self._headers(token),
                            json=payload,
                        ) as resp:
                            if resp.status_code == 404 and conv_id and attempt == 0:
                                # conversation gone or not this user's → start a new one, once
                                conv_id = None
                                self._threads.pop(__chat_id__, None)
                                continue
                            if resp.status_code in (401, 403):
                                # Aria's reason string never contains the token — safe to show
                                try:
                                    detail = (json.loads(await resp.aread()) or {}).get("detail") or ""
                                except Exception:
                                    detail = ""
                                why = f"\n\n_Aria said: {detail}_" if detail else ""
                                if resp.status_code == 401:
                                    yield ("**Aria didn't accept your CityGPT sign-in.** Sign out of CityGPT, "
                                           "sign in again and re-send." + why)
                                else:
                                    yield ("**You don't have an Aria account yet.**\n\n"
                                           "Aria takes your identity from CityGPT but your access from its own "
                                           "user list. Ask the Aria admin to add your email, then try again." + why)
                                return
                            if resp.status_code >= 400:
                                yield (f"Aria returned `{resp.status_code}`. Nothing was answered — "
                                       "check the base_url valve or tell the Aria team.")
                                return

                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    ev = json.loads(line)
                                except ValueError:
                                    continue
                                kind = ev.get("type")

                                if kind == "meta":
                                    conv_id = ev.get("conversation_id") or conv_id
                                    if cacheable and conv_id:
                                        self._threads[__chat_id__] = conv_id
                                        self._trim(self._threads)

                                elif kind == "step":
                                    if ev.get("reset"):   # Aria discarded its first attempt
                                        raw = ""
                                    label = ev.get("label") or ""
                                    detail = ev.get("detail") or ""
                                    await status(f"{label} — {detail}" if detail else label)

                                elif kind == "token":
                                    chunk = ev.get("v") or ""
                                    raw += chunk
                                    if self.valves.live_stream:
                                        # hold back the last line until it can't be a sentinel
                                        safe = raw[: raw.rfind("\n") + 1] if "\n" in raw else ""
                                        safe = self._strip_internal(safe) + ("\n" if safe else "")
                                        if len(safe) > len(shown) and safe.startswith(shown):
                                            yield safe[len(shown):]
                                            shown = safe

                                elif kind == "done":
                                    final = ev.get("clean")
                                    final = self._strip_internal(final if final is not None else raw)
                                    if self.valves.live_stream:
                                        if final.startswith(shown.rstrip()):
                                            rest = final[len(shown.rstrip()):]
                                            if rest:
                                                yield rest
                                        else:  # Aria rewrote the answer (e.g. retried wider)
                                            yield "\n\n---\n" + final
                                    else:
                                        yield final
                                    async for extra in self._finish(ev, __event_emitter__, __chat_id__):
                                        yield extra
                                    return
                        return
            except httpx.RequestError as e:
                if shown:
                    yield (f"\n\n_⚠️ The connection to Aria dropped mid-answer (`{type(e).__name__}`). Ask again._")
                else:
                    yield (f"Could not reach Aria (`{type(e).__name__}`). Nothing was answered — "
                           "try again in a minute.")
            finally:
                await status("", done=True)

        return run()

    async def _finish(self, ev: dict, emitter, chat_id: str):
        """Sources, ungrounded warning, chat title and follow-ups from the done event."""
        pages = ev.get("pages") or []
        if self.valves.show_sources and pages:
            lines = ["\n\n---\n**Sources**"]
            seen = set()
            for p in pages[:8]:
                doc = self._doc_title(p.get("doc_name") or p.get("doc_title") or "")
                page = p.get("page_no") or p.get("page")
                key = (doc, page)
                if key in seen:
                    continue
                seen.add(key)
                label = f"{doc}" + (f", p. {page}" if page else "")
                url = p.get("image_url")
                lines.append(f"- [{label}]({self._base}{url})" if url else f"- {label}")
            yield "\n".join(lines)

        if ev.get("blind") and not ev.get("grounded"):
            yield "\n\n_No runbook covers this yet — the answer above is not backed by a source._"

        if not emitter:
            return
        title = (ev.get("title") or "").strip()
        if title and chat_id and chat_id not in self._titled:
            self._titled.add(chat_id)
            self._trim(self._titled)
            await emitter({"type": "chat:title", "data": title})

        fups = [f for f in (ev.get("followups") or []) if isinstance(f, str)][:3]
        if self.valves.show_followups and fups:
            await emitter({"type": "chat:message:follow_ups", "data": {"follow_ups": fups}})
