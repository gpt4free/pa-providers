from __future__ import annotations

from g4f.Provider.template import OpenaiTemplate


class Provider(OpenaiTemplate):
    label = "Kilo Code"
    url = "https://kilo.ai"
    base_url = "https://api.kilo.ai/api/gateway"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True
    default_model = "kilo-auto/free"
    models = [
        "kilo-auto/free",
        "kilo-auto/frontier",
        "kilo-auto/balanced",
        "kilo-auto/efficient",
        "kilo-auto/small",
        "stepfun/step-3.7-flash:free",
        "inclusionai/ling-3.0-flash:free",
        "poolside/laguna-s-2.1:free",
    ]