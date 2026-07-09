from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import JsonConversation
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "HeckAI"
    url = "https://heck.ai"
    session_endpoint = "https://api.heckai.weight-wave.com/api/ha/v1/session/create"
    chat_endpoint = "https://api.heckai.weight-wave.com/api/ha/v1/chat"
    working = True

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
            "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "authorization": "",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "pragma": "no-cache",
            "origin": "https://heck.ai",
            "referer": "https://heck.ai/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

        # Extract last user question and previous Q&A from history
        user_messages = [m for m in messages if m["role"] == "user"]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]

        question = user_messages[-1]["content"] if user_messages else ""
        previous_question = user_messages[-2]["content"] if len(user_messages) >= 2 else None
        previous_answer = assistant_messages[-1]["content"] if assistant_messages else None

        if conversation is None:
            conversation = JsonConversation()

        async with ClientSession(headers=headers) as session:
            if not getattr(conversation, "session_id", None):
                # Create a new session if no session_id is provided
                session_payload = {"title": question[:100] if question else "hi"}
                async with session.post(
                    cls.session_endpoint, json=session_payload, proxy=proxy
                ) as resp:
                    resp.raise_for_status()
                    session_data = await resp.json()
                    conversation.session_id = (
                        session_data.get("sessionId")
                        or session_data.get("id")
                        or session_data.get("data", {}).get("sessionId")
                    )
            yield conversation

            # Step 2: Send chat request and stream response
            chat_payload = {
                "model": model,
                "question": question,
                "language": "English",
                "sessionId": conversation.session_id,
                "previousQuestion": previous_question,
                "previousAnswer": previous_answer,
                "imgUrls": [],
                "superSmartMode": False,
            }
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
                    if data == "[ANSWER_START]":
                        in_answer = True
                        continue
                    if data == "[ANSWER_DONE]":
                        break
                    if in_answer and data:
                        yield data
