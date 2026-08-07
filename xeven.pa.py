from __future__ import annotations

from urllib.parse import quote

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import ImageResponse
from g4f.typing import AsyncResult, Messages


class Provider(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Xeven AI Image"
    url = "https://ai-image-api.xeven.workers.dev"
    api_url = "https://ai-image-api.xeven.workers.dev/img"
    working = True

    default_model = "auto"
    default_image_model = default_model
    image_models = ["auto"]
    models = image_models

    @classmethod
    def get_models(cls, **kwargs):
        return cls.models

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        prompt: str = None,
        **kwargs
    ) -> AsyncResult:
        from g4f.Provider.helper import format_media_prompt

        prompt = format_media_prompt(messages, prompt)

        url = f"{cls.api_url}?prompt={quote(prompt)}"
        yield ImageResponse(
            urls=url,
            alt=prompt
        )