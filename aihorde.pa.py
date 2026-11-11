from __future__ import annotations

import json
import time
from typing import Any

import requests
from aiohttp import ClientSession

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "AI Horde"
    url = "https://aihorde.net"
    base_url = "https://oai.aihorde.net/v1"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "koboldcpp/gemma-4-31B-it"
    models = [default_model]
    _models_checked_at = 0.0

    @classmethod
    def get_models(cls, **kwargs: Any) -> list[str]:
        if time.time() - cls._models_checked_at < 300:
            return cls.models

        try:
            response = requests.get(f"{cls.base_url}/models", timeout=5)
            response.raise_for_status()
            data = response.json().get("data", [])
            models = [
                entry["id"]
                for entry in data
                if entry.get("id")
                and "text" in entry.get("architecture", {}).get(
                    "input_modalities", ["text"]
                )
            ]
            if models:
                cls.models = models
                if cls.default_model not in models:
                    cls.default_model = models[0]
        except (requests.RequestException, ValueError, TypeError):
            pass

        cls._models_checked_at = time.time()
        return cls.models

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        payload: dict[str, Any] = {
            "model": model or cls.default_model,
            "messages": messages,
            "stream": True,
        }
        for key in ("max_tokens", "temperature", "top_p", "stop", "seed"):
            if key in kwargs and kwargs[key] is not None:
                payload[key] = kwargs[key]

        headers = {
            "accept": "text/event-stream",
            "content-type": "application/json",
            "x-title": "gpt4free PA Providers",
        }

        async with ClientSession(headers=headers) as session:
            async with session.post(
                f"{cls.base_url}/chat/completions",
                json=payload,
                proxy=proxy,
            ) as response:
                if response.status >= 400:
                    detail = (await response.text())[:1000]
                    raise RuntimeError(
                        f"AI Horde error ({response.status}): {detail}"
                    )

                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("error"):
                        raise RuntimeError(f"AI Horde error: {chunk['error']}")

                    choices = chunk.get("choices") or []
                    if choices:
                        content = choices[0].get("delta", {}).get("content")
                        if isinstance(content, str) and content:
                            yield content
