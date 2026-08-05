from __future__ import annotations

from g4f.Provider.template import OpenaiTemplate


class Provider(OpenaiTemplate):
    label = "Pollinations"
    url = "https://pollinations.ai"
    base_url = "https://text.pollinations.ai/v1"
    working = True

    default_model = "openai-fast"
    models = [
        "openai-fast",
    ]