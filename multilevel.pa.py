"""
Multi-Level Provider
====================

A debugging / exploration provider that accepts **any URL** and streams
the response, showing each parsing level in a **different color** so
the user can visually identify which level contains the actual content
and then choose that level.

Two response formats are auto-detected:

1. **SSE streaming** — lines starting with ``data:`` (OpenAI-style)
2. **Plain JSON** — a single JSON body, e.g.::

       {"success": true, "response": "Hello!"}

Parsing levels
--------------
Level 1 — Raw data (SSE ``data:`` payload *or* raw JSON body string)
Level 2 — Parsed JSON (``choices`` array for SSE, full dict for JSON)
Level 3 — Deepest content (``delta.content`` for SSE, ``response`` field for JSON)

Colors
------
Level 1 → 🔴 red
Level 2 → 🔵 blue
Level 3 → 🟢 green

Usage
-----
The user selects which level to use as the "real" content via the
``level`` extra parameter (passed as a model alias or kwargs):

    model="v3#level=3"   → only yield Level 3 content (green)
    model="v3#level=2"   → only yield Level 2 JSON (blue)
    model="v3#level=1"   → only yield Level 1 raw data (red)
    model="v3#level=0"   → yield ALL levels, each colored (default, for debugging)

The URL can be overridden at call time via the ``url`` kwarg or by
setting ``model="v3#url=https://my-endpoint.com/api/chat"``.
"""

import json
import re
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Reasoning, Usage
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


# ---------------------------------------------------------------------------
# Color helpers — produce inline-HTML markdown spans the g4f GUI can render
# ---------------------------------------------------------------------------

_LEVEL_COLORS = {
    1: "#e74c3c",   # red
    2: "#3498db",   # blue
    3: "#2ecc71",   # green
}

_LEVEL_LABELS = {
    1: "L1·raw",
    2: "L2·json",
    3: "L3·content",
}


def _colored(level: int, text: str) -> str:
    """Wrap *text* in an HTML span with the color for *level*."""
    color = _LEVEL_COLORS.get(level, "#888")
    label = _LEVEL_LABELS.get(level, f"L{level}")
    # Escape backticks to avoid breaking markdown code spans
    safe = text.replace("`", "\\`")
    return f'<span style="color:{color}">**[{label}]** {safe}</span>'


# ---------------------------------------------------------------------------
# Model-string parsing:  "v3#level=3&url=https://..."
# ---------------------------------------------------------------------------

def _parse_model_string(model: str) -> tuple[str, dict[str, str]]:
    """Split 'base#key=val&key=val' into (base, {key: val})."""
    options: dict[str, str] = {}
    base = model
    if "#" in model:
        base, _, opt_str = model.partition("#")
        for pair in opt_str.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                options[k.strip().lower()] = v.strip()
    return base, options


# ---------------------------------------------------------------------------
# Content extraction — handles both OpenAI-style and flat JSON responses
# ---------------------------------------------------------------------------

# Flat top-level fields that commonly carry the text content
_FLAT_CONTENT_KEYS = ("response", "content", "text", "message", "answer", "result")


def _extract_content(data: Any) -> str | None:
    """
    Extract the deepest text content from a parsed JSON dict.

    Tries (in order):
      1. choices[0].delta.content        (OpenAI streaming)
      2. choices[0].message.content       (OpenAI non-streaming)
      3. choices[0].delta.reasoning       (reasoning)
      4. Flat keys: response, content, text, message, answer, result
      5. data["response"] when data has "success" key (simple API style)
    """
    if not isinstance(data, dict):
        return None

    # OpenAI-style choices
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        if isinstance(choice0, dict):
            delta = choice0.get("delta", {})
            if isinstance(delta, dict):
                content = delta.get("content")
                if content:
                    return content
            message = choice0.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                if content:
                    return content

    # Flat top-level content keys
    for key in _FLAT_CONTENT_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val:
            return val

    return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "MultiLevel"
    url = "https://example.com"
    api_endpoint = "https://llmproxy.org/api/chat.php"
    working = True
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "v3"
    models = ["v3"]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        web_search: bool = False,
        **kwargs: Any,
    ) -> AsyncResult:
        # --- resolve model + inline options -------------------------------
        base_model, opts = _parse_model_string(model)
        model = cls.get_model(base_model)

        # Determine the target URL (any URL the user supplies)
        url = opts.get("url") or kwargs.get("url") or cls.api_endpoint

        # Determine which level to yield (0 = all, for debugging)
        level: int = 0
        if "level" in opts:
            try:
                level = int(opts["level"])
            except ValueError:
                pass
        elif "level" in kwargs:
            try:
                level = int(kwargs["level"])
            except (ValueError, TypeError):
                pass

        # --- build request ------------------------------------------------
        headers = {
            "accept": "*/*",
            "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": "https://freechat.org",
            "pragma": "no-cache",
            "referer": "https://freechat.org/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

        payload = {
            "messages": messages,
            "model": model,
            "cost": 1,
            "stream": True,
            "web_search": web_search,
        }

        async with StreamSession(headers=headers, impersonate="chrome") as session:
            async with session.post(
                url, json=payload, proxy=proxy
            ) as response:
                response.raise_for_status()

                # Peek at content-type to decide SSE vs plain JSON
                content_type = response.headers.get("content-type", "")
                is_sse = "text/event-stream" in content_type

                # If not SSE by header, try to read lines; if the first
                # meaningful line starts with "data:", treat as SSE.
                if not is_sse:
                    # Read entire body as text (non-streaming JSON)
                    body = await response.text()

                    # Check if body looks like SSE anyway
                    if body.lstrip().startswith("data:"):
                        is_sse = True
                        sse_lines = body.splitlines()
                    else:
                        # ---- Plain JSON (or plain text) response --------
                        # LEVEL 1 — raw body
                        if level == 1:
                            yield body
                            return

                        if level == 0:
                            yield _colored(1, body) + "\n"

                        # LEVEL 2 — parsed JSON
                        try:
                            json_data = json.loads(body)
                        except json.JSONDecodeError:
                            if level == 0:
                                yield _colored(1, body) + "\n"
                            elif level in (2, 3):
                                yield body
                            return

                        if level == 0:
                            yield _colored(2, json.dumps(json_data, ensure_ascii=False)) + "\n"

                        if level == 2:
                            yield json.dumps(json_data, ensure_ascii=False)
                            return

                        # Yield usage if present
                        if json_data.get("usage"):
                            yield Usage.from_dict(json_data["usage"])

                        # LEVEL 3 — deepest content field
                        # Supports: response, content, text, message,
                        #           choices[0].delta.content, choices[0].message.content
                        content = _extract_content(json_data)

                        if content:
                            if level == 0:
                                yield _colored(3, content) + "\n"
                            elif level == 3:
                                yield content
                        return

                # Use the streaming path
                async for chunk in response.iter_lines():
                    if not chunk:
                        continue
                    line = chunk.decode("utf-8")
                    if not line.startswith("data: "):
                        continue

                    # =====================================================
                    # LEVEL 1 — raw data after "data: "
                    # =====================================================
                    data = line[6:]
                    if data == "[DONE]":
                        break

                    if level == 1:
                        yield data
                        continue

                    if level == 0:
                        yield _colored(1, data) + "\n"

                    # =====================================================
                    # LEVEL 2 — parsed JSON
                    # =====================================================
                    try:
                        json_data = json.loads(data)
                    except json.JSONDecodeError:
                        # Not JSON — fall back to level 1
                        if level == 0:
                            yield _colored(1, data) + "\n"
                        elif level in (2, 3):
                            yield data
                        continue

                    # Yield usage if present
                    if json_data.get("usage"):
                        yield Usage.from_dict(json_data["usage"])

                    choices = json_data.get("choices")
                    if level == 0 and choices is not None:
                        yield _colored(2, json.dumps(choices, ensure_ascii=False)) + "\n"

                    if level == 2:
                        yield json.dumps(choices, ensure_ascii=False) if choices is not None else ""
                        continue

                    if not choices:
                        # No choices — try flat content fields too
                        content = _extract_content(json_data)
                        if content:
                            if level == 0:
                                yield _colored(3, content) + "\n"
                            elif level == 3:
                                yield content
                        continue

                    # =====================================================
                    # LEVEL 3 — delta.content / delta.reasoning
                    # =====================================================
                    delta = choices[0].get("delta", {}) if isinstance(choices, list) else {}

                    reasoning = delta.get("reasoning")
                    if reasoning:
                        if level == 0:
                            yield _colored(3, reasoning) + "\n"
                        elif level == 3:
                            yield Reasoning(reasoning)

                    content = delta.get("content")
                    if content:
                        if level == 0:
                            yield _colored(3, content) + "\n"
                        elif level == 3:
                            yield content