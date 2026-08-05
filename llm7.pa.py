from __future__ import annotations

from g4f.Provider.template import OpenaiTemplate


class Provider(OpenaiTemplate):
    label = "LLM7"
    url = "https://llm7.io"
    base_url = "https://api.llm7.io/v1"
    working = True
    
    default_model = "gpt-oss:20b"
    models = [
        "gpt-oss:20b",
        "codestral-latest",
        "gemini-3.1-flash-lite",
        "minimax-m2.7",
        "mistral-Nemo-Instruct-2407",
    ]
