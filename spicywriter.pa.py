from __future__ import annotations

import json
import secrets
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


def _make_anon_id() -> str:
    return f"anon_{secrets.token_hex(3)}"


def _make_traceparent() -> str:
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01"


_CLIENT_DIAG = json.dumps({
    "csrf": False,
    "uid": None,
    "lastUid": None,
    "authed": False,
    "sinceCsrfMs": None,
    "persisted": False,
    "resumeMs": None,
    "pwa": False,
    "online": True,
})


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "SpicyWriter"
    url = "https://spicywriter.com"
    api_endpoint = "https://spicywriter.com/api/conversations/new"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "Ling 2.6 Flash"
    models = [
        "Ling 2.6 Flash",
        "Nemo",
    ]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        # Build SpicyWriter messages with parent chain
        messages_to_create = []
        last_id = 0
        has_system = False

        for m in messages:
            if m["role"] == "system":
                has_system = True
                messages_to_create.append({
                    "id": 0,
                    "content": m["content"],
                    "role": "system",
                    "parent": None,
                    "writer": "spicy",
                })
                last_id = 0
                break

        if not has_system:
            messages_to_create.append({
                "id": 0,
                "content": "",
                "role": "system",
                "parent": None,
                "writer": "spicy",
            })
            last_id = 0

        neg_id = -1
        for m in messages:
            if m["role"] == "system":
                continue
            messages_to_create.append({
                "id": neg_id,
                "content": m["content"],
                "role": "assistant" if m["role"] == "assistant" else "user",
                "parent": last_id,
                "writer": "spicy",
            })
            last_id = neg_id
            neg_id -= 1

        submit_message_id = last_id

        payload = {
            "messagesToCreate": messages_to_create,
            "messagesToEdit": [],
            "submitMessageId": submit_message_id,
            "model": model,
            "writer": "spicy",
            "thinking": False,
            "responseId": submit_message_id - 1,
            "title": f"Chat {json.dumps(list(messages_to_create[0].keys()))}",
        }

        headers = {
            "Content-Type": "application/json",
            "X-Anonymous-User-Id": _make_anon_id(),
            "X-Client-Diag": _CLIENT_DIAG,
            "traceparent": _make_traceparent(),
            "Accept": "text/event-stream",
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
                    # Don't trim leading spaces — they're significant in SpicyWriter
                    line = line.decode("utf-8")
                    # Only strip trailing newline
                    rtrimmed = line.rstrip("\r\n")
                    if not rtrimmed.startswith("data:"):
                        continue
                    data = rtrimmed[5:]
                    if data.startswith(" "):
                        data = data[1:]
                    if not data:
                        continue
                    if data.startswith("{"):
                        try:
                            json.loads(data)
                        except json.JSONDecodeError:
                            pass
                        else:
                            continue
                    # Plain text delta — convert literal \n to newlines
                    yield data.replace("\\n", "\n")