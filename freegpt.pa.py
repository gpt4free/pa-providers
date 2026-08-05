"""
FreeGPT.tech provider — WASM-secured OpenAI-compatible gateway.

Host: https://standalone.freegpt.win:3001

Each request goes through a proof-of-work challenge handshake:
  1. Generate a fresh UUID (per-request identity).
  2. GET /api/challenge with the uuid → server returns a challenge + difficulty level.
  3. Run the WASM signer to compute the secure payload (signature, nonce, timestamp)
     bound to (uuid, challenge, clientIp, difficulty).
  4. POST /api/openai/oneapi/v1/chat/completions with all x-secure-* headers
     plus the OpenAI-shaped request body.
  5. Parse the OpenAI-format response — streaming (SSE) or non-streaming (JSON).

The WASM signer uses wasmtime to instantiate wasm_signer_bg.wasm with browser
API mocks for canvas fingerprinting. Challenges are single-use and valid for
~5 minutes, so we mint a new one for every request.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid as uuid_module
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages

from freegpt_wasm_signer import get_signer  # noqa: E402


def _make_nonce() -> str:
    """Random hex nonce, 32 chars (16 bytes)."""
    return secrets.token_hex(16)


def _secure_payload_to_headers(payload: dict | str) -> dict[str, str]:
    """Flatten the secure-payload object into x-secure-* HTTP headers.

    Nested objects are flattened with `-` and snake_case → kebab-case,
    then every key is prefixed with `x-secure-`.
    """
    if isinstance(payload, str):
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return {"x-secure-signature": payload}
    else:
        obj = payload

    headers: dict[str, str] = {}

    def walk(prefix: str, value: Any):
        if value is None:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                key = k.replace("_", "-")
                walk(f"{prefix}-{key}" if prefix else f"x-secure-{key}", v)
            return
        if isinstance(value, (str, int, float, bool)):
            headers[prefix] = str(value)

    walk("", obj)
    return headers


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "FreeGPT"
    url = "https://freegpt.tech"
    api_endpoint = "https://standalone.freegpt.win:3001/api/openai/oneapi/v1/chat/completions"
    challenge_endpoint = "https://standalone.freegpt.win:3001/api/challenge"
    working = True
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "gpt-4o-mini"
    models = [
        "gpt-4o-mini",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-free",
        "deepseek-chat",
        "deepseek-reasoner",
        "claude-3.5-sonnet",
        "gemini-2.0-flash",
        "grok-2",
        "llama-3.3-70b",
        "qwen-2.5-72b",
    ]

    @classmethod
    async def _fetch_challenge(
        cls, session: StreamSession, request_uuid: str
    ) -> dict[str, Any]:
        """Fetch a fresh challenge for the given uuid."""
        headers = {
            "uuid": request_uuid,
            "x-origin": "https://freegpt.tech",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
        }
        async with session.get(
            cls.challenge_endpoint, headers=headers
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(
                    f"FreeGPT challenge returned HTTP {response.status}: "
                    f"{text[:200]}"
                )
            data = await response.json()

        challenge = (
            data.get("challenge")
            or data.get("token")
            or data.get("challenge_token")
            or ""
        )
        difficulty = data.get("difficulty", data.get("level", 2))
        challenge_id = data.get("challengeId", "")
        expires_at = data.get("expiresAt", 0)
        version = data.get("version", "1.0")

        if not challenge or not challenge_id:
            raise RuntimeError(
                f"FreeGPT challenge response unexpected: "
                f"{json.dumps(data)[:300]}"
            )

        return {
            "challenge": challenge,
            "difficulty": difficulty,
            "challenge_id": challenge_id,
            "expires_at": expires_at,
            "version": version,
        }

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        """Create an async generator for streaming completions."""
        # 1. Fresh UUID per request
        request_uuid = str(uuid_module.uuid4())

        # 2. Initialize WASM signer (singleton, lazy-loaded)
        signer = get_signer()

        # 3. Build the OpenAI-shaped request body
        body = {
            "model": model,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ],
            "stream": True,
            "temperature": kwargs.get("temperature", 0.5),
            "presence_penalty": kwargs.get("presence_penalty", 0),
            "frequency_penalty": kwargs.get("frequency_penalty", 0),
            "top_p": kwargs.get("top_p", 1),
        }

        # Pass through tools if provided
        tools = kwargs.get("tools")
        if tools and len(tools) > 0:
            body["tools"] = tools
            body["tool_choice"] = kwargs.get("tool_choice", "auto")

        # 4. Open a streaming session
        async with StreamSession(
            proxy=proxy,
            timeout=kwargs.get("timeout", 120),
        ) as session:
            # 5. Fetch challenge
            chal = await cls._fetch_challenge(session, request_uuid)

            # 6. Generate secure payload via WASM signer
            timestamp = str(int(time.time() * 1000))
            nonce = _make_nonce()
            client_ip = "127.0.0.1"

            payload = signer.generate_secure_payload(
                request_uuid,
                timestamp,
                nonce,
                chal["challenge"],
                client_ip,
                chal["difficulty"],
            )

            # 7. Build headers — secure payload fields + explicit challenge info
            secure_headers = _secure_payload_to_headers(payload)
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                ),
                "uuid": request_uuid,
                "x-secure-challenge": chal["challenge"],
                "x-secure-challenge-id": chal["challenge_id"],
                "x-secure-challenge-expires-at": str(chal["expires_at"]),
                "x-secure-challenge-version": chal["version"],
                "x-secure-client-ip": client_ip,
                "x-origin": "https://freegpt.tech",
                "cf-turnstile-token": "",
                "x-secure-timestamp": timestamp,
                "x-secure-nonce": nonce,
                "x-secure-version": "3.0",
            }
            headers.update(secure_headers)

            # 8. POST completion (streaming)
            async with session.post(
                cls.api_endpoint,
                json=body,
                headers=headers,
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    if response.status == 400 and "没有可用的tokens" in text:
                        raise RuntimeError(
                            "FreeGPT's upstream token pool is temporarily "
                            "exhausted. Please try a different model or retry "
                            "in a few minutes."
                        )
                    if response.status == 400 and "Provider failed" in text:
                        raise RuntimeError(
                            f"FreeGPT upstream provider error: {text[:150]}. "
                            "Try a different model or retry shortly."
                        )
                    if response.status == 401 and "订阅" in text:
                        raise RuntimeError(
                            "This FreeGPT model requires a subscription. "
                            "Try a different model — the free-tier models "
                            "(gpt-4o-mini, gpt-5.4-mini, gpt-5.4-nano, "
                            "deepseek-chat, etc.) work without subscription."
                        )
                    raise RuntimeError(
                        f"FreeGPT returned HTTP {response.status}: "
                        f"{text[:200]}"
                    )

                # 9. Parse SSE stream
                async for line in response.iter_lines():
                    line = line.strip() if isinstance(line, str) else line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass