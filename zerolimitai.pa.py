from __future__ import annotations

import json
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.Provider.helper import format_prompt
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "ZeroLimitAI"
    url = "https://www.zerolimitai.com"
    api_endpoint = "https://www.zerolimitai.com/api/demo-compare"
    working = True
    needs_auth = False
    supports_stream = False
    supports_system_message = True
    supports_message_history = True

    default_model = "minimax/minimax-m3.1-flash:free"
    models = [
        "minimax/minimax-m3.1-flash:free",
        "google/gemma-4-26b-a4b-it:free",
        "xingchen-agi/xing4.0-29b-a4b:free",
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
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": cls.url,
            "referer": f"{cls.url}/tools/compare",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
            ),
        }

        payload = {"prompt": format_prompt(messages)}

        async with ClientSession(headers=headers) as session:
            async with session.post(
                cls.api_endpoint, json=payload, proxy=proxy
            ) as response:
                content_type = response.headers.get("content-type", "")
                if response.status != 200 or "text/event-stream" not in content_type:
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        data = await response.text()
                    if isinstance(data, dict):
                        message = data.get("error") or data.get("message") or data
                    else:
                        message = data
                    raise RuntimeError(f"ZeroLimitAI error ({response.status}): {message}")

                fallback: str | None = None
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

                    evt_type = evt.get("type")
                    if evt_type == "error":
                        raise RuntimeError(
                            f"ZeroLimitAI error: {evt.get('error', evt)}"
                        )
                    if evt_type != "result":
                        continue

                    content = evt.get("content")
                    if not content:
                        continue
                    if evt.get("id") == model:
                        yield content
                        return
                    if fallback is None:
                        fallback = content

                if fallback is not None:
                    yield fallback