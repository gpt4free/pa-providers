import json
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Koala"
    url = "https://koala.sh"
    api_endpoint = "https://koala.sh/api/gpt/"
    working = True

    default_model = "gemini-3-flash"
    models = [
        "gemini-3-flash",
        "gpt-5.3-chat-latest",
        "claude-4.5-haiku",
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

        headers = {
            "accept": "text/event-stream",
            "content-type": "application/json",
            "flag-real-time-data": "true",
            "origin": "https://koala.sh",
            "referer": "https://koala.sh/chat",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        # Attach empty attachments list required by the API
        formatted_messages = [
            {**msg, "attachments": []} if "attachments" not in msg else msg
            for msg in messages
        ]

        payload = {
            "messages": formatted_messages,
            "model": model,
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
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            yield data
