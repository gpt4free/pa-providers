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
        "kilo-auto/small",
        "stepfun/step-3.7-flash:free",
        "tencent/hy3:free",
        "poolside/laguna-s-2.1:free",
        "nvidia/nemotron-3.5-lightning:free",
        "minimax/minimax-m3:free",
        "minimax/minimax-m2.7:free",
    ]
    model_aliases = {
        "stepfun/step-3.7-flash": "stepfun/step-3.7-flash:free",
        "hy3": "tencent/hy3:free",
        "laguna-s-2.1": "poolside/laguna-s-2.1:free",
        "nemotron-3.5": "nvidia/nemotron-3.5-lightning:free",
    }
