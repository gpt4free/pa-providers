import json
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Reasoning, Usage
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "FreeChat"
    url = "https://freechat.org"
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
        model = cls.get_model(model)

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
                cls.api_endpoint, json=payload, proxy=proxy
            ) as response:
                response.raise_for_status()
                async for chunk in response.iter_lines():
                    if not chunk:
                        continue
                    line = chunk.decode("utf-8")
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        json_data = json.loads(data)
                        if json_data.get("usage"):
                            yield Usage.from_dict(json_data["usage"])
                        choices = json_data.get("choices")
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        reasoning = delta.get("reasoning")
                        if reasoning:
                            yield Reasoning(reasoning)
                        content = delta.get("content")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        pass