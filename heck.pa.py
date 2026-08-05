import uuid
from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import JsonConversation
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "HeckAI"
    url = "https://heck.ai"
    chat_endpoint = "https://api.heckai.weight-wave.com/api/ha/v1/chat"
    # API became a proxy to OpenRouter and returns 402 Payment Required
    # (free credit pool exhausted). Re-enable when credits are available.
    working = False

    default_model = "openai/gpt-5.4-mini"
    models = [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "tencent/hy3-preview",
        "qwen/qwen3.7-plus",
        "stepfun/step-3.7-flash",
        "google/gemini-3.1-flash-lite",
        "google/gemini-3-flash-preview",
        "openai/gpt-5.4-mini",
        "minimax/minimax-m3",
    ]

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        conversation: JsonConversation | None = None,
        proxy: str | None = None,
        **kwargs: Any,
    ) -> AsyncResult:
        model = cls.get_model(model)

        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://heck.ai",
            "referer": "https://heck.ai/",
        }

        # Extract system, user, and assistant messages
        system_messages = [m for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] == "user"]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]

        # Concatenate system messages to the question
        system_content = "\n".join(m["content"] for m in system_messages)
        question = user_messages[-1]["content"] if user_messages else ""
        if system_content:
            question = f"{system_content}\n\n{question}"

        previous_question = user_messages[-2]["content"] if len(user_messages) >= 2 else None
        previous_answer = assistant_messages[-1]["content"] if assistant_messages else None

        # Generate session ID client-side (no server session creation needed)
        session_id = str(uuid.uuid4())

        chat_payload = {
            "model": model,
            "question": question,
            "language": "English",
            "sessionId": session_id,
            "previousQuestion": previous_question,
            "previousAnswer": previous_answer,
            "imgUrls": [],
            "superSmartMode": False,
        }

        async with ClientSession(headers=headers) as session:
            async with session.post(
                cls.chat_endpoint, json=chat_payload, proxy=proxy
            ) as response:
                response.raise_for_status()
                in_answer = False
                async for line in response.content:
                    line = line.decode("utf-8").rstrip("\n\r")
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    # Skip non-answer SSE markers
                    if data in (
                        "[REASON_START]",
                        "[REASON_DONE]",
                        "[RELATE_Q_START]",
                        "[RELATE_Q_DONE]",
                        "[DONE]",
                    ):
                        continue
                    if data == "[ANSWER_START]":
                        in_answer = True
                        continue
                    if data == "[ANSWER_DONE]":
                        break
                    if data == "[ERROR]":
                        break
                    if in_answer and data:
                        yield data
