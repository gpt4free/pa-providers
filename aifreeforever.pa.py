from __future__ import annotations

import json
import time
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "AI Free Forever"
    url = "https://aifreeforever.com"
    api_endpoint = "https://aifreeforever.com/api/generate-ai-answer"
    nonce_endpoint = "https://aifreeforever.com/api/chat-nonce"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "gpt-5"

    # model id -> backend endpoint suffix
    model_endpoints = {
        "gpt-5": "/api/generate-ai-answer",
        "deepseek-v4-flash": "/api/generate-ai-answer-deepseek",
        "kimi-k2-6": "/api/generate-ai-answer-foundry",
        "gpt-5-mini": "/api/generate-ai-answer-foundry",
        "deepseek-v3-2": "/api/generate-ai-answer-foundry",
        "gpt-5-4": "/api/generate-ai-answer-orbio",
        "gemini-3-1": "/api/generate-ai-answer-orbio",
        "grok-4": "/api/generate-ai-answer-orbio",
        "qwen3-7": "/api/generate-ai-answer-orbio",
        "llama-3-1": "/api/generate-ai-answer-orbio",
        "phi-4": "/api/generate-ai-answer-orbio",
        "gpt-5-nano": "/api/generate-ai-answer-ci",
        "gpt-4-1": "/api/generate-ai-answer-ci",
        "gpt-oss-120b": "/api/generate-ai-answer-ci",
        "gemini-3": "/api/generate-ai-answer-ci",
        "claude-haiku-4-5": "/api/generate-ai-answer-ci",
        "glm-5-2": "/api/generate-ai-answer-ci",
        "qwen3-6": "/api/generate-ai-answer-ci",
        "claude-sonnet-4-6": "/api/generate-ai-answer-vyce",
        "gpt-astra": "/api/generate-ai-answer-vyce",
        "gemini-3-5": "/api/generate-ai-answer-vertex",
        "claude-opus-5-5": "/api/generate-ai-answer-vertex",
        "claude-haiku-5-5": "/api/generate-ai-answer-vertex",
        "claude-fable-5-1": "/api/generate-ai-answer-anthropic",
        "claude-sonnet-5-5": "/api/generate-ai-answer-anthropic",
        "claude-haiku-5-5-anthropic": "/api/generate-ai-answer-anthropic",
    }

    # Models currently enabled and working on the site. The remaining entries
    # in ``model_endpoints`` are kept for when the site re-enables them, but
    # they currently answer with 409 model_unavailable or a 500 upstream error.
    models = [
        "gpt-5",
        "deepseek-v4-flash",
        "claude-haiku-5-5-anthropic",
    ]

    @classmethod
    async def _get_nonce(cls, session: ClientSession, proxy: str | None) -> str:
        try:
            async with session.get(
                cls.nonce_endpoint, proxy=proxy
            ) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data.get("nonce", "")
        except Exception:
            pass
        return ""

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str | None = None,
        tone: str = "friendly",
        response_format: str = "paragraph",
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)
        endpoint = cls.model_endpoints.get(model, cls.api_endpoint)

        history = []
        question = ""
        for message in messages:
            content = message["content"]
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "")
                    for part in content
                    if part.get("type") == "text"
                )
            if message["role"] == "assistant":
                history.append({"role": "assistant", "content": content})
            else:
                history.append({"role": message["role"], "content": content})

        if history and history[-1]["role"] == "user":
            question = history.pop()["content"]

        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": cls.url,
            "referer": f"{cls.url}/chat/{model}",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
            ),
        }

        async with ClientSession(headers=headers) as session:
            nonce = await cls._get_nonce(session, proxy)
            now = int(time.time() * 1000)
            payload = {
                "model": model,
                "question": question,
                "tone": tone,
                "format": response_format,
                "file": None,
                "conversationHistory": history,
                "interactionProof": {
                    "nonce": nonce,
                    "keystrokeCount": max(len(question), 1),
                    "pasteEvents": 0,
                    "totalTypingTime": 3000,
                    "startTime": now - 3000,
                    "submitTime": now,
                },
            }

            async with session.post(
                f"{cls.url}{endpoint}", json=payload, proxy=proxy
            ) as response:
                if response.status == 409:
                    data = await response.json(content_type=None)
                    available = [
                        m["id"]
                        for m in data.get("models", [])
                        if m.get("available")
                    ]
                    raise RuntimeError(
                        f"AI Free Forever: model '{model}' is unavailable. "
                        f"Available models: {', '.join(available) or 'none'}"
                    )
                response.raise_for_status()

                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("token")
                    if token:
                        yield token