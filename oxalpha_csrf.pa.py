"""Ox Alpha provider derived from ``oxalpha.com.har``.

The HAR capture shows the plain browser flow used by ``/chatapp/chat-app.min.js``:

1. ``GET https://oxalpha.com/chat`` — the HTML contains
   ``<meta name="csrf-token" content="…">`` and sets the ``ox_alpha_session``
   and ``XSRF-TOKEN`` cookies.
2. ``POST https://oxalpha.com/api/chat`` with ``X-CSRF-TOKEN`` and those
   cookies, body ``{"model": …, "messages": […]}}``, answering with an
   OpenAI-style ``text/event-stream``.

No Turnstile is involved until the server answers ``428`` with
``{"code": "turnstile_required"}``. The HAR request itself carried no
Turnstile token, so that checkpoint is server-side rate limiting rather than
part of the normal flow; a fresh session usually clears it. Only as a last
resort is a token obtained through a headless CDP session.
"""

from __future__ import annotations

import json
import re
import time
import asyncio
from typing import Any, Optional

import aiohttp

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Reasoning, Usage
from g4f.typing import AsyncResult, Messages
from g4f import debug


SITEKEY = "0x4AAAAAABp_UYRVl7snkQ51"
CSRF_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.I)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Ox Alpha"
    url = "https://oxalpha.com"
    api_endpoint = "https://oxalpha.com/api/chat"
    chat_page = "https://oxalpha.com/chat"

    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "z-ai/glm-5.3-flash"
    models = ["z-ai/glm-5.3-flash"]
    model_aliases = {
        "ox-alpha": "z-ai/glm-5.3-flash",
        "glm-5.3-flash": "z-ai/glm-5.3-flash",
        "glm-5.3": "z-ai/glm-5.3-flash",
    }

    #: How long a scraped CSRF token / cookie pair is reused.
    session_ttl = 1800
    #: How long a solved Turnstile token is reused.
    turnstile_ttl = 120

    _cookies: Optional[dict] = None
    _csrf_token: Optional[str] = None
    _session_time: float = 0
    _turnstile_token: Optional[str] = None
    _turnstile_time: float = 0
    _lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Session handling
    # ------------------------------------------------------------------

    @classmethod
    def _session_valid(cls) -> bool:
        return bool(
            cls._cookies
            and cls._csrf_token
            and time.time() - cls._session_time < cls.session_ttl
        )

    @classmethod
    async def _fetch_session(cls, proxy: Optional[str] = None) -> None:
        """Scrape the CSRF token and session cookies from the chat page."""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": USER_AGENT,
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(cls.chat_page, proxy=proxy) as response:
                response.raise_for_status()
                html = await response.text()
                cookies = {c.key: c.value for c in session.cookie_jar}

        match = CSRF_RE.search(html)
        if not match:
            raise RuntimeError("Ox Alpha: csrf-token meta tag not found on /chat")

        cls._csrf_token = match.group(1)
        cls._cookies = cookies
        cls._session_time = time.time()
        debug.log(f"Ox Alpha: obtained CSRF token and {len(cookies)} cookies")

    @classmethod
    async def _get_session(cls, force_refresh: bool = False, proxy: Optional[str] = None) -> None:
        async with cls._lock:
            if force_refresh or not cls._session_valid():
                await cls._fetch_session(proxy)

    # ------------------------------------------------------------------
    # Turnstile fallback (only needed once the server answers 428)
    # ------------------------------------------------------------------

    @classmethod
    async def _obtain_turnstile_token(cls, timeout: int = 60) -> str:
        from g4f.requests.cdp import CDPSession

        debug.log("Ox Alpha: starting headless CDP session to solve Turnstile...")
        # Headless only: a windowed browser needs a working desktop session
        # (X11/GTK), which is not available on servers or in containers.
        session = CDPSession(headless=True)
        await session.start()
        try:
            await session.navigate(cls.chat_page)

            # The Turnstile script is loaded asynchronously, so wait for it
            # before rendering the widget.
            for _ in range(30):
                if await session.evaluate_js("typeof window.turnstile !== 'undefined'"):
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Ox Alpha: Turnstile script did not load on /chat")

            await session.evaluate_js(
                f"""
                (() => {{
                    if (!document.getElementById("ox-turnstile-box")) {{
                        const div = document.createElement("div");
                        div.id = "ox-turnstile-box";
                        div.style.position = "fixed";
                        div.style.bottom = "20px";
                        div.style.right = "20px";
                        div.style.zIndex = "999999";
                        document.body.appendChild(div);
                    }}
                    window.oxTurnstileToken = null;
                    window.turnstile.render("#ox-turnstile-box", {{
                        sitekey: "{SITEKEY}",
                        callback: function(token) {{
                            window.oxTurnstileToken = token;
                        }}
                    }});
                }})()
                """
            )

            # The widget may also be rendered by the app itself, so accept a
            # token from either the callback or the hidden response input.
            read_token = """
                window.oxTurnstileToken
                || (document.querySelector('input[name="cf-turnstile-response"]') || {}).value
                || null
            """

            start_time = time.time()
            while time.time() - start_time < timeout:
                await asyncio.sleep(1)
                await session.bypass_turnstile()
                token = await session.evaluate_js(read_token)
                if token:
                    cls._turnstile_token = token
                    cls._turnstile_time = time.time()
                    return token

            raise RuntimeError("Ox Alpha: failed to obtain a Turnstile token in time.")
        finally:
            await session.close()

    @classmethod
    async def _get_turnstile_token(cls, force_refresh: bool = False) -> str:
        async with cls._lock:
            if (
                not force_refresh
                and cls._turnstile_token
                and time.time() - cls._turnstile_time < cls.turnstile_ttl
            ):
                return cls._turnstile_token
            return await cls._obtain_turnstile_token()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @classmethod
    async def _stream(cls, payload: dict, headers: dict, proxy: Optional[str]):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                cls.api_endpoint,
                json=payload,
                headers=headers,
                cookies=cls._cookies,
                proxy=proxy,
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    yield response.status, body
                    return

                async for raw in response.content:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if data.get("usage"):
                        yield "usage", data["usage"]

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning")
                    if reasoning:
                        yield "reasoning", reasoning
                    content = delta.get("content")
                    if content:
                        yield "content", content

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        payload = {
            "model": model,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ],
        }

        for attempt in range(3):
            await cls._get_session(force_refresh=attempt > 0, proxy=proxy)

            headers = {
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/json",
                "Origin": cls.url,
                "Referer": cls.chat_page,
                "User-Agent": USER_AGENT,
                "X-CSRF-TOKEN": cls._csrf_token,
                # The browser sends these on the same-origin XHR; the server
                # does not require them, but they keep the request faithful.
                "Sec-CH-UA": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Linux"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            if cls._turnstile_token and time.time() - cls._turnstile_time < cls.turnstile_ttl:
                headers["X-Turnstile-Token"] = cls._turnstile_token

            retry = False
            async for kind, value in cls._stream(payload, headers, proxy):
                if kind == "content":
                    yield value
                elif kind == "reasoning":
                    yield Reasoning(value)
                elif kind == "usage":
                    yield Usage.from_dict(value)
                else:
                    status, body = kind, value
                    if status == 428:
                        # The checkpoint is server-side rate limiting (the HAR
                        # shows a plain CSRF request succeeding), so a fresh
                        # session usually passes on its own. Only solve
                        # Turnstile as a last resort.
                        if attempt < 2:
                            debug.log(f"Ox Alpha: Turnstile checkpoint (HTTP 428), retrying with a fresh session (attempt {attempt + 1})")
                        else:
                            debug.log("Ox Alpha: Turnstile checkpoint (HTTP 428), solving via CDP...")
                            try:
                                await cls._get_turnstile_token(force_refresh=True)
                            except Exception as e:
                                raise RuntimeError(
                                    "Ox Alpha requires a Turnstile verification that could "
                                    "not be completed automatically. This is usually a "
                                    "temporary per-IP rate limit — retry later or use a "
                                    f"proxy. ({e})"
                                ) from e
                        retry = True
                    elif status in (419, 403):
                        debug.log(f"Ox Alpha: HTTP {status}, refreshing session...")
                        retry = True
                    elif status == 429:
                        raise RuntimeError(f"Ox Alpha rate limit reached: {body[:200]}")
                    else:
                        raise RuntimeError(f"Ox Alpha returned HTTP {status}: {body[:200]}")
                    break

            if not retry:
                return