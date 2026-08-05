from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _rand_str(length: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "NSFWLover"
    url = "https://www.nsfwlover.com"
    api_endpoint = "https://www.nsfwlover.com/api/openai/chat/completions"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "llama3-8b"
    models = ["llama3-8b"]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        now = datetime.now()
        date_str = now.strftime("%Y/%m/%d %H:%M:%S")

        sysprompt = "\n\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        convo = [m for m in messages if m["role"] in ("user", "assistant")]

        input_messages = []
        for i, m in enumerate(convo):
            input_messages.append({
                "id": _rand_str(20) if i == len(convo) - 1 else str(uuid.uuid4()),
                "type": "text",
                "date": date_str,
                "role": m["role"],
                "content": m["content"],
            })

        payload = {
            "builtin": True,
            "char": None,
            "char_id": "Linda",
            "session_id": _rand_str(22),
            "lang": "en",
            "sysprompt": sysprompt,
            "description": "",
            "input_messages": input_messages,
            "stream": True,
            "model_type": model,
            "isSummary": False,
            "charname": "Linda",
            "shortname": "",
            "username": "Guest",
            "session_date": date_str,
            "gender": "Unknown",
            "userGender": "Unknown",
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-local-id": _rand_str(60),
            "Origin": "https://www.nsfwlover.com",
            "Referer": "https://www.nsfwlover.com/",
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
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if isinstance(delta, str) and delta:
                            yield delta
                    except json.JSONDecodeError:
                        continue