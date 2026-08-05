from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "UnlimitedAI"
    url = "https://app.unlimitedai.chat"
    api_endpoint = "https://app.unlimitedai.chat/api/chat"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "chat-model-reasoning"
    models = [
        "chat-model-reasoning",
        "chat-model-reasoning-with-search",
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

        now_iso = datetime.now(timezone.utc).isoformat()
        formatted_messages = []
        for m in messages:
            formatted_messages.append({
                "id": str(uuid.uuid4()),
                "role": m["role"],
                "content": m["content"],
                "parts": [{"type": "text", "text": m["content"]}],
                "createdAt": now_iso,
            })

        payload = {
            "chatId": str(uuid.uuid4()),
            "messages": formatted_messages,
            "selectedChatModel": model,
            "selectedCharacter": None,
            "selectedStory": None,
            "deviceId": str(uuid.uuid4()),
            "locale": "en",
        }

        headers = {
            "Content-Type": "application/json",
            "x-next-intl-locale": "en",
            "Origin": "https://app.unlimitedai.chat",
            "Referer": "https://app.unlimitedai.chat/",
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
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    evt_type = evt.get("type")
                    if evt_type == "delta" and isinstance(evt.get("delta"), str):
                        yield evt["delta"]
                    elif evt_type == "error" and evt.get("error"):
                        raise RuntimeError(f"UnlimitedAI error: {evt['error']}")