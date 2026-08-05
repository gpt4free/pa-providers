from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


def _fresh_guest_hash() -> str:
    raw = os.urandom(32).hex() + str(os.urandom(8).hex())
    return hashlib.sha256(raw.encode()).hexdigest()


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "JollyGen"
    url = "https://chat.jollyai.online"
    api_endpoint = "https://jollygenapi.space/ai/chat-guest"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "jolly-rp"
    models = ["jolly-rp"]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] in ("user", "assistant")]

        if len(convo) == 1:
            message = convo[0]["content"]
        else:
            parts = []
            for m in convo:
                prefix = "[User]" if m["role"] == "user" else "[Assistant]"
                parts.append(f"{prefix}: {m['content']}")
            message = "\n".join(parts)

        if sys:
            message = f"{sys}\n\n{message}"

        payload = {
            "message": message,
            "stream": True,
            "guest_hash": _fresh_guest_hash(),
        }

        headers = {
            "Content-Type": "application/json",
            "Origin": "https://chat.jollyai.online",
            "Referer": "https://chat.jollyai.online/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        }

        async with ClientSession(headers=headers) as session:
            async with session.post(
                cls.api_endpoint, json=payload, proxy=proxy
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode("utf-8").rstrip("\n\r")
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(evt.get("delta"), str):
                        yield evt["delta"]
                    if evt.get("done"):
                        return
                    detail = evt.get("detail")
                    if isinstance(detail, dict) and detail.get("message"):
                        raise RuntimeError(f"JollyGen: {detail['message']}")