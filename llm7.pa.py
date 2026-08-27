from __future__ import annotations

from g4f.Provider.template import OpenaiTemplate


class Provider(OpenaiTemplate):
    label = "LLM7"
    url = "https://llm7.io"
    base_url = "https://api.llm7.io/v1"
    working = True

    default_model = "gpt-oss"
    models = [
        "gpt-oss",
        "codestral-latest",
        "minimax-m2.7",
        "mistral-Nemo-Instruct-2407",
    ]
