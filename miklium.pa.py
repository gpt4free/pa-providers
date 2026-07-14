from typing import Any

from aiohttp import ClientSession
from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "MIKLIUM"
    url = "https://miklium.vercel.app"
    api_endpoint = "https://miklium.vercel.app/api/chatbot"
    working = True

    default_model = "miklium"
    models = ["miklium", "personalityless", "male", "female", "all"]

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
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://miklium.vercel.app",
            "referer": "https://miklium.vercel.app/",
        }

        # Extract last user message as the chatbot input
        user_messages = [m for m in messages if m["role"] == "user"]
        message = user_messages[-1]["content"] if user_messages else ""

        payload = {
            "message": message,
            "response_stacking": 4,
            "personality": model,
        }

        async with ClientSession(headers=headers) as session:
            async with session.post(
                cls.api_endpoint, json=payload, proxy=proxy
            ) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get("success") == "true" or data.get("success") is True:
                    yield data.get("response", "")
                else:
                    raise RuntimeError(data.get("error", "Unknown error from MIKLIUM API"))