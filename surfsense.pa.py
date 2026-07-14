import json
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Reasoning, Usage
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "SurfSense"
    url = "https://www.surfsense.com"
    api_endpoint = "https://api.surfsense.com/api/v1/public/anon-chat/stream"
    working = True
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "gpt-5.4-mini-no-login"
    models = [
        "gpt-5.4-mini-no-login",
        "gpt-o4-mini-no-login",
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
            "accept": "*/*",
            "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": "https://www.surfsense.com",
            "pragma": "no-cache",
            "referer": "https://www.surfsense.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }

        payload = {
            "model_slug": model,
            "messages": messages,
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
                    except json.JSONDecodeError:
                        continue

                    msg_type = json_data.get("type")

                    # Yield thinking steps as reasoning
                    if msg_type == "data-thinking-step":
                        step_data = json_data.get("data", {})
                        if step_data.get("status") == "completed":
                            items = step_data.get("items", [])
                            if items:
                                yield Reasoning("\n".join(items))

                    # Yield text deltas
                    elif msg_type == "text-delta":
                        delta = json_data.get("delta")
                        if delta:
                            yield delta

                    # Yield token usage
                    elif msg_type == "data-token-usage":
                        usage_data = json_data.get("data", {})
                        if usage_data:
                            yield Usage.from_dict(usage_data)