from __future__ import annotations

import json
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


class JailGPT(AsyncGeneratorProvider, ProviderModelMixin):
    label = "JailGPT"
    url = "https://jailgpt.ru"
    api_endpoint = "https://jailgpt.ru/api/chat/stream"

    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "jailgpt"
    models = ["jailgpt"]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        language: str = "en",
        **kwargs: Any,
    ) -> AsyncResult:
        # Extract history and current message
        history = []
        user_message = ""

        for m in messages[:-1]:
            role = m.get("role")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})
            elif role == "system" and content:
                history.append({"role": "user", "content": f"System prompt: {content}"})

        if messages:
            last = messages[-1]
            user_message = last.get("content", "")
            if last.get("role") == "system":
                user_message = f"System prompt: {user_message}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Origin": cls.url,
            "Referer": f"{cls.url}/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }

        payload = {
            "message": user_message,
            "history": history,
            "language": language,
        }

        async with StreamSession(
            headers=headers,
            impersonate="chrome",
            proxy=proxy,
            timeout=kwargs.get("timeout", 120),
        ) as session:
            async with session.post(
                cls.api_endpoint,
                json=payload,
                proxy=proxy,
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"JailGPT returned HTTP {response.status}: {text[:200]}")

                async for line in response.iter_lines():
                    line_str = line.strip() if isinstance(line, str) else line.decode("utf-8", errors="ignore").strip()
                    if not line_str or not line_str.startswith("data:"):
                        continue
                    data_str = line_str[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                        delta = data.get("delta")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError):
                        pass


Provider = JailGPT
