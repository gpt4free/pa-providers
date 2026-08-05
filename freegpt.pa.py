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

The WASM signer is Node-only (uses jsdom for browser API mocking for canvas
fingerprinting). Challenges are single-use and valid for ~5 minutes.

Rate limit: 8 requests/minute per client IP.

NOTE: This provider is marked as not working because the WASM PoW challenge
cannot be replicated in Python without the original WASM binary. The API
structure is documented here for reference.
"""

from __future__ import annotations

import json
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "FreeGPT"
    url = "https://freegpt.tech"
    api_endpoint = "https://standalone.freegpt.win:3001/api/openai/oneapi/v1/chat/completions"
    challenge_endpoint = "https://standalone.freegpt.win:3001/api/challenge"
    working = False  # Requires WASM PoW challenge — not implementable in pure Python
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
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        raise NotImplementedError(
            "FreeGPT requires a WASM proof-of-work challenge that cannot be "
            "replicated in Python. Use the TypeScript implementation instead."
        )