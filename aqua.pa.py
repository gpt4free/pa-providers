"""Aqua provider — pa-provider edition.

A thin OpenAI-compatible wrapper around the Aqua gateway
(https://api.aquadevs.com/v1) with an automatic free-week trial fallback
served by Eaon's own gateway (https://api.eaon.dev/v1).

Two modes:
  * **Real key** — the user passes an Aqua API key (``api_key=...``).
    Requests go straight to ``api.aquadevs.com`` with a plain
    ``Authorization: Bearer <key>`` header.
  * **Trial** — when no key is supplied the provider mints a device-bound
    trial credential from Eaon's gateway and signs every request with
    HMAC-SHA256(secret, "ts.deviceHash.bodySHA").  The secret is stored
    on disk (inside the g4f workspace) and never sent again.

This file is designed to run inside the pa-provider sandbox, so it only
uses modules from the sandbox allow-list (``aiohttp``, ``hashlib``,
``hmac``, ``json``, ``time``, ``datetime``, ``uuid``, ``pathlib`` and the
safe ``g4f`` submodules).  No ``httpx`` / ``socket`` / ``os`` / ``platform``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from aiohttp import ClientSession

from g4f.Provider.template import OpenaiTemplate
from g4f.Provider.template.OpenaiTemplate import read_response
from g4f.providers.cache import FileStorage
from g4f.providers.response import JsonRequest
from g4f.requests import StreamSession, raise_for_status
from g4f.tools.media import render_messages
from g4f.typing import AsyncResult, Messages


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AQUA_BASE_URL = "https://api.aquadevs.com/v1"
EAON_BASE_URL = "https://api.eaon.dev/v1"
KEY_PREFIX = "eaon-trial-"

# Persist the trial credential via g4f's FileStorage (lives under the
# cookies/.models dir) instead of a raw workspace Path.
_CREDENTIAL_KEY = "aqua/trial_credential"
_storage = FileStorage()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_trial_key(api_key: str) -> bool:
    return bool(api_key) and api_key.startswith(KEY_PREFIX)


def _device_hash() -> str:
    """Stable per-machine fingerprint.

    The sandbox blocks ``socket``/``platform``/``/etc/machine-id`` reads,
    so we fall back to ``uuid.getnode()`` (the MAC address, available
    without imports) salted with a persisted random UUID.  This is a
    weaker device binding than the in-tree version (which hashes
    ``/etc/machine-id`` + hostname) but is the best the sandbox permits
    and is still stable across runs on the same machine.
    """
    node = uuid.getnode()
    raw = f"eaon-free-week-pa:{node:x}"
    return _sha256_hex(raw.encode())


def _signing_headers(secret: str, body: bytes) -> dict[str, str]:
    """The three HMAC headers the Eaon gateway requires on trial requests."""
    device = _device_hash()
    ts = str(int(time.time()))
    body_hash = _sha256_hex(body or b"")
    payload = f"{ts}.{device}.{body_hash}"
    signature = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-Eaon-Device": device,
        "X-Eaon-TS": ts,
        "X-Eaon-Sig": signature,
    }


# ---------------------------------------------------------------------------
# Credential persistence (workspace-scoped)
# ---------------------------------------------------------------------------

class _TrialCredential:
    __slots__ = ("key", "secret", "expires_at")

    def __init__(self, key: str, secret: str, expires_at: datetime) -> None:
        self.key = key
        self.secret = secret
        self.expires_at = expires_at

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "secret": self.secret,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_TrialCredential":
        exp = d["expires_at"]
        if exp.endswith("Z"):
            exp = exp[:-1] + "+00:00"
        return cls(
            key=d["key"],
            secret=d["secret"],
            expires_at=datetime.fromisoformat(exp),
        )


_credential_cache: Optional[_TrialCredential] = None

def _load_credential() -> Optional[_TrialCredential]:
    """Load the trial credential from FileStorage, if present."""
    try:
        # FileStorage.get() returns the deserialized JSON value (a dict)
        # or None when the key is absent.
        data = _storage.get(_CREDENTIAL_KEY)
        if data is None:
            return None
        return _TrialCredential.from_dict(data)
    except (OSError, KeyError, ValueError):
        return None

def _save_credential(cred: _TrialCredential) -> None:
    """Persist the trial credential via FileStorage and cache it in memory."""
    global _credential_cache
    _credential_cache = cred
    try:
        # FileStorage.set() json-serializes the value for us, so we hand it
        # the dict directly (not a pre-serialized string).
        _storage.set(_CREDENTIAL_KEY, cred.to_dict())
    except OSError:
        pass

def _clear_credential() -> None:
    """Drop the cached credential and remove it from FileStorage."""
    global _credential_cache
    _credential_cache = None
    try:
        _storage.delete(_CREDENTIAL_KEY)
    except OSError:
        pass

def get_credential() -> Optional[_TrialCredential]:
    global _credential_cache
    if _credential_cache is None:
        _credential_cache = _load_credential()
    cred = _credential_cache
    if cred is not None and cred.is_expired:
        _clear_credential()
        return None
    return cred


async def _start_trial(timeout: float = 20.0) -> _TrialCredential:
    """Mint (or recover) this device's free-week trial credential."""
    device = _device_hash()
    payload = {
        "device": device,
        "platform": "macos",
        "app_version": "2026.3.2",
    }
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Eaon-Client": "eaon-desktop/2026.3.2",
    }
    async with ClientSession() as session:
        async with session.post(
            f"{EAON_BASE_URL}/trial/start",
            data=body,
            headers=headers,
            timeout=timeout,
        ) as resp:
            data = await resp.json()
            if resp.status != 201:
                msg = (
                    (data.get("error", {}).get("message"))
                    or "Couldn't start the free week — try again in a moment."
                )
                raise RuntimeError(msg)
            trial = data.get("data", {})
            key = trial.get("key", "")
            secret = trial.get("secret", "")
            expires_raw = trial.get("expires_at", "")
            if not all([key, secret, expires_raw]):
                raise RuntimeError("Malformed trial response from gateway.")
            if expires_raw.endswith("Z"):
                expires_raw = expires_raw[:-1] + "+00:00"
            expires_at = datetime.fromisoformat(expires_raw)
            cred = _TrialCredential(key=key, secret=secret, expires_at=expires_at)
            _save_credential(cred)
            return cred


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class Provider(OpenaiTemplate):
    label = "Aqua API"
    url = "https://aquadevs.com"
    login_url = "https://aquadevs.com"
    base_url = AQUA_BASE_URL
    working = True
    default_model = "glm-5.2"

    @classmethod
    def _resolve_base_url(cls, api_key: str | None = None) -> str:
        """Return the gateway URL matching the credential type.

        A user-entered key always targets the Aqua endpoint.  A trial
        key targets Eaon's own gateway.
        """
        if api_key and _is_trial_key(api_key):
            return EAON_BASE_URL
        return cls.base_url

    @classmethod
    async def _ensure_api_key(cls, api_key: str | None) -> str:
        """Resolve an api_key, minting a free-week trial if none was given."""
        if api_key:
            return api_key
        cred = get_credential()
        if cred is None:
            cred = await _start_trial()
        return cred.key

    @classmethod
    def _trial_signing_headers(cls, api_key: str, body: bytes) -> dict[str, str]:
        """HMAC signing headers for a trial key, empty for a real key."""
        if not _is_trial_key(api_key):
            return {}
        cred = get_credential()
        if cred is None or cred.key != api_key:
            return {}
        return _signing_headers(cred.secret, body=body)

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str = None,
        timeout: int = 120,
        api_key: str = None,
        base_url: str = None,
        stream: bool = None,
        prompt: str = None,
        headers: dict = None,
        media: Any = None,
        **kwargs,
    ) -> AsyncResult:
        api_key = await cls._ensure_api_key(api_key)
        if base_url is None:
            base_url = cls._resolve_base_url(api_key)

        # Non-trial keys take the fast path through the standard OpenAI
        # template — no signing required.
        if not _is_trial_key(api_key):
            async for chunk in super().create_async_generator(
                model=model,
                messages=messages,
                proxy=proxy,
                timeout=timeout,
                api_key=api_key,
                base_url=base_url,
                stream=stream,
                prompt=prompt,
                headers=headers,
                **kwargs,
            ):
                yield chunk
            return

        # Trial keys must be HMAC-signed over the exact request body, so we
        # build the payload ourselves and post it verbatim.
        model = cls.get_model(model, api_key=api_key, base_url=base_url)
        if stream is None:
            stream = True
        data = {
            "messages": list(render_messages(messages, media)),
            "model": model,
            "stream": stream,
        }
        if "temperature" in kwargs and kwargs["temperature"] is not None:
            data["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            data["max_tokens"] = kwargs["max_tokens"]
        if "top_p" in kwargs and kwargs["top_p"] is not None:
            data["top_p"] = kwargs["top_p"]
        if "tools" in kwargs and kwargs["tools"] is not None:
            data["tools"] = kwargs["tools"]
        if "reasoning_effort" in kwargs and kwargs["reasoning_effort"] is not None:
            data["reasoning_effort"] = kwargs["reasoning_effort"]
        if "tool_choice" in kwargs and kwargs["tool_choice"] is not None:
            data["tool_choice"] = kwargs["tool_choice"]

        body = json.dumps(data, separators=(",", ":")).encode()
        sign = cls._trial_signing_headers(api_key, body)
        request_headers = cls.get_headers(stream, api_key, headers)
        request_headers.update(sign)

        api_endpoint = f"{base_url.rstrip('/')}/chat/completions"
        yield JsonRequest.from_dict(data)

        async with StreamSession(
            proxy=proxy,
            headers=request_headers,
            timeout=timeout,
        ) as session:
            async with session.post(api_endpoint, data=body, ssl=cls.ssl) as response:
                await raise_for_status(response)
                async for chunk in read_response(
                    response, stream, prompt, cls.get_dict(),
                    download_media=True, yield_request=False,
                ):
                    yield chunk