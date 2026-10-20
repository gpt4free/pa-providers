import json
import uuid
from typing import Any

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Usage
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Assistant UI"
    url = "https://www.assistant-ui.com"
    api_endpoint = "https://www.assistant-ui.com/api/chat"
    session_endpoint = "https://www.assistant-ui.com/api/anonymous-session"
    working = True
    supports_stream = True
    supports_message_history = True
    supports_system_message = False

    default_model = "deepseek-v4.1-flash"
    models = [
        "deepseek-v4.1-flash",
        "moonshotai/kimi-k3",
        "z-ai/glm-5.3",
        "xiaomi/mimo-v2.6-pro",
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
            "origin": "https://www.assistant-ui.com",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.assistant-ui.com/demos/base",
            "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }

        # The API rejects a dedicated "system" role, so fold it into the first
        # user message instead.
        system_prompt = ""
        formatted_messages = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            content = content or ""
            role = msg.get("role")
            if role == "system":
                system_prompt = f"{system_prompt}\n{content}".strip()
                continue
            if role not in ("user", "assistant"):
                continue
            if role == "user" and system_prompt:
                content = f"{system_prompt}\n\n{content}"
                system_prompt = ""
            formatted_messages.append(
                {
                    "role": role,
                    "parts": [{"type": "text", "text": content}],
                    "metadata": {"custom": {}},
                    "id": uuid.uuid4().hex[:16],
                }
            )

        payload = {
            "config": {"modelName": model},
            "id": f"thread_{uuid.uuid4().hex}",
            "messages": formatted_messages,
            "trigger": "submit-message",
            "metadata": {},
        }

        async with StreamSession(
            headers=headers, impersonate="chrome", proxy=proxy
        ) as session:
            # The chat endpoint requires the anonymous session cookie.
            async with session.get(cls.session_endpoint) as response:
                await response.text()

            async with session.post(cls.api_endpoint, json=payload) as response:
                response.raise_for_status()
                async for line in response.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8")
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
                    if msg_type == "text-delta":
                        delta = json_data.get("delta")
                        if delta:
                            yield delta
                    elif msg_type == "error":
                        raise RuntimeError(
                            json_data.get("errorText", "An error occurred.")
                        )
                    elif msg_type == "finish":
                        usage = json_data.get("messageMetadata", {}).get("usage")
                        if usage:
                            yield Usage(
                                prompt_tokens=usage.get("inputTokens"),
                                completion_tokens=usage.get("outputTokens"),
                                total_tokens=usage.get("totalTokens"),
                            )