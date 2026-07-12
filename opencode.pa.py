from __future__ import annotations

from g4f.Provider.template import OpenaiTemplate
from g4f.typing import Messages, AsyncResult


class Provider(OpenaiTemplate):
    label = "OpenCode Zen"
    url = "https://opencode.ai"
    base_url = "https://opencode.ai/zen/v1"

    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "big-pickle"
    models = [
        "big-pickle",
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
        "hy3-free",
        "nemotron-3-ultra-free",
        "north-mini-code-free",
    ]
    model_aliases = {
        "deepseek-v4-flash": "deepseek-v4-flash-free",
        "mimo-v2.5": "mimo-v2.5-free",
        "hy3": "hy3-free",
        "nemotron-3-ultra": "nemotron-3-ultra-free",
        "north-mini-code": "north-mini-code-free",
    }

    @classmethod
    def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        stream: bool = False,
        **kwargs,
    ) -> AsyncResult:
        # No Authorization header — the zen endpoint is keyless.
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": cls.url,
            "Referer": f"{cls.url}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Authorization": f"Bearer ",
        }
        return super().create_async_generator(
            model=model,
            messages=messages,
            stream=stream,
            headers=headers,
            **kwargs,
        )