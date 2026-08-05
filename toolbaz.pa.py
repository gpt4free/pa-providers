from __future__ import annotations

import json
import random
import secrets
import time
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


_DEFAULT_MODEL = "toolbaz-v4.5-fast"

_MODELS = [
    "toolbaz-v4.5-fast",
    "toolbaz_v4",
    "gpt-5",
    "gpt-5.2",
    "gpt-4o-latest",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-free",
    "deepseek-v4",
    "deepseek-r1",
    "claude-3.7-sonnet",
    "claude-3.5-sonnet",
    "gemini-2.0-flash",
    "grok-3",
    "llama-3.3-70b",
    "qwen-2.5-72b",
]

# Hangul Filler (U+3164) — used as message delimiter in the text field
_HANGUL_FILLER = "\u3164"

# Alphabets for generating random strings
_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]

_LANGUAGES = ["en-US", "en-GB", "en-CA", "en-AU"]
_RESOLUTIONS = ["1920x1080", "2560x1440", "1366x768", "1440x900", "1536x864", "412x915"]
_TIMEZONES = ["Asia/Calcutta", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Tokyo"]
_PLATFORMS = {
    "Mozilla/5.0 (Windows": "Win32",
    "Mozilla/5.0 (Macintosh": "MacIntel",
    "Mozilla/5.0 (X11": "Linux x86_64",
    "Mozilla/5.0 (Linux; Android": "Linux armv8l",
}
_COLOR_DEPTHS = [24, 30]
_CORES = [4, 8, 8, 12, 16]


def _random_string(length: int, alphabet: str) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_session_id() -> str:
    """36-char alphanumeric id, matching the shape Toolbaz uses for session ids."""
    return _random_string(36, _ALPHABET)


def _generate_fingerprint_token(user_agent: str) -> str:
    """Build the browser-fingerprint token that token.php expects.

    Wire format: <6 random base64 chars><base64(JSON fingerprint)>
    The JSON uses obfuscated keys matching the Toolbaz web UI.
    """
    platform = "Win32"
    for key, val in _PLATFORMS.items():
        if user_agent.startswith(key):
            platform = val
            break

    fingerprint = {
        "bR6wF": {
            "nV5kP": user_agent,
            "lQ9jX": random.choice(_LANGUAGES),
            "sD2zR": random.choice(_RESOLUTIONS),
            "tY4hL": random.choice(_TIMEZONES),
            "pL8mC": platform,
            "tcQjt": random.choice(_COLOR_DEPTHS),
            "hK7jN": random.choice(_CORES),
        },
        "uT4bX": {"mM9wZ": [], "kP8jY": []},
        "tuTcS": int(time.time()),
        "tDfxy": "0",
        "RtyJt": _random_string(36, _ALPHABET),
    }

    prefix = _random_string(6, _BASE64_ALPHABET)
    import base64
    encoded = base64.b64encode(json.dumps(fingerprint).encode("utf-8")).decode("ascii")
    return prefix + encoded


def _build_headers(user_agent: str) -> dict[str, str]:
    return {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
        "User-Agent": user_agent,
        "Origin": "https://toolbaz.com",
        "Referer": "https://toolbaz.com/",
    }


def _turns_to_text(messages: Messages) -> str:
    """Convert messages into the single `text` string that writing.php expects.

    The prompt is wrapped with Hangul-filler delimiters exactly like the
    Toolbaz web UI does.
    """
    filtered = [m for m in messages if m.get("content")]
    if not filtered:
        return f"{_HANGUL_FILLER} : {_HANGUL_FILLER}"

    # Single user message -> exact same shape as the website
    if len(filtered) == 1 and filtered[0]["role"] == "user":
        return f"{_HANGUL_FILLER} : {filtered[0]['content']}{_HANGUL_FILLER}"

    parts = []
    for m in filtered:
        role = m["role"]
        label = (
            "System" if role == "system"
            else "Assistant" if role == "assistant"
            else "Tool" if role in ("tool", "function")
            else "User"
        )
        parts.append(f"{label}: {m['content']}")
    joined = "\n\n".join(parts)
    return f"{_HANGUL_FILLER} : {joined}{_HANGUL_FILLER}"


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Toolbaz"
    url = "https://toolbaz.com"
    token_endpoint = "https://data.toolbaz.com/token.php"
    writing_endpoint = "https://data.toolbaz.com/writing.php"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = _DEFAULT_MODEL
    models = _MODELS

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        # Fresh identity per request
        session_id = _generate_session_id()
        user_agent = random.choice(_USER_AGENTS)
        fingerprint_token = _generate_fingerprint_token(user_agent)
        headers = _build_headers(user_agent)

        async with ClientSession(headers=headers) as session:
            # Step 1: POST token.php -> captcha token
            # Field name is "token" (the fingerprint token), NOT "fingerprint"
            token_data = {
                "session_id": session_id,
                "token": fingerprint_token,
            }

            async with session.post(
                cls.token_endpoint, data=token_data, proxy=proxy
            ) as token_resp:
                token_resp.raise_for_status()
                token_json = await token_resp.json()
                if not token_json.get("success") or not token_json.get("token"):
                    raise RuntimeError(f"token.php failed: {token_json}")
                captcha_token = token_json["token"]

            # Step 2: Build text field with Hangul Filler delimiters
            text_field = _turns_to_text(messages)

            # Step 3: POST writing.php -> completion text
            # Only 4 fields: text, capcha, model, session_id
            # Note: field name is "capcha" (misspelled), matching the server
            writing_data = {
                "text": text_field,
                "capcha": captcha_token,
                "model": model,
                "session_id": session_id,
            }

            async with session.post(
                cls.writing_endpoint, data=writing_data, proxy=proxy
            ) as response:
                response.raise_for_status()
                text = await response.text()
                # Strip trailing [model: ...] marker
                import re
                text = re.sub(r"\s*\[model:\s*[^\]]*\]\s*$", "", text, flags=re.IGNORECASE).strip()
                if text:
                    yield text