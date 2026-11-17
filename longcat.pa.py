from __future__ import annotations

import json
import secrets
from typing import Any

from aiohttp import ClientSession

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "LongCat"
    url = "https://longcat.ai"
    api_endpoint = "https://longcat.ai/api/v1/chat-completion-oversea-V2"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "LongCat-2.5-Agent"
    models = [default_model]

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)
        system_prompt = "\n".join(
            cls._content_text(message.get("content"))
            for message in messages
            if message.get("role") == "system"
        )

        conversation = []
        latest_user_message = None
        for message in messages:
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue

            content = cls._content_text(message.get("content"))
            if role == "user" and system_prompt and latest_user_message is None:
                content = f"{system_prompt}\n\n{content}".strip()
            if role == "user":
                latest_user_message = content

            conversation.append(
                {
                    "role": role,
                    "events": [
                        {
                            "type": "userMsg" if role == "user" else "content",
                            "content": content,
                            "status": "FINISHED",
                        }
                    ],
                    "chatStatus": "FINISHED",
                    "messageId": secrets.randbelow(100_000_000),
                    "idType": "custom",
                }
            )

        if latest_user_message is None:
            raise ValueError("LongCat requires at least one user message")

        conversation.append(
            {
                "role": "assistant",
                "events": [],
                "chatStatus": "LOADING",
                "messageId": secrets.randbelow(100_000_000),
                "idType": "custom",
            }
        )

        payload = {
            "content": latest_user_message,
            "agentId": "1",
            "messages": conversation,
            "reasonEnabled": 0,
            "searchEnabled": 0,
            "regenerate": 0,
        }
        headers = {
            "accept": "text/event-stream,application/json",
            "content-type": "application/json",
            "origin": cls.url,
            "referer": f"{cls.url}/chat/",
            "x-requested-with": "XMLHttpRequest",
            "x-client-language": "en",
        }

        async with ClientSession(headers=headers) as session:
            async with session.post(
                cls.api_endpoint,
                json=payload,
                proxy=proxy,
            ) as response:
                if response.status >= 400:
                    detail = (await response.text())[:1000]
                    raise RuntimeError(f"LongCat error ({response.status}): {detail}")

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
                        raise RuntimeError(f"LongCat error: {chunk['error']}")

                    event = chunk.get("event") or {}
                    if event.get("type") == "error":
                        raise RuntimeError(
                            event.get("message") or "LongCat returned an error"
                        )
                    content = event.get("content")
                    if isinstance(content, str) and content:
                        yield content
