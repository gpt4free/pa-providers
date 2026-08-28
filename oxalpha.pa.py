from __future__ import annotations

import json
import time
import asyncio
from typing import Any, Optional

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.requests import StreamSession
from g4f.requests.cdp import CDPSession
from g4f.typing import AsyncResult, Messages
from g4f import debug


SITEKEY = "0x4AAAAAABp_UYRVl7snkQ51"


class OxAlpha(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Ox Alpha"
    url = "https://oxalpha.com"
    api_endpoint = "https://oxalpha.com/api/chat"

    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "z-ai/glm-5.3-flash"
    models = [
        "z-ai/glm-5.3-flash",
        "stealth/ox-alpha",
    ]
    model_aliases = {
        "ox-alpha": "stealth/ox-alpha",
        "glm-5.3-flash": "z-ai/glm-5.3-flash",
        "glm-5.3": "z-ai/glm-5.3-flash",
    }

    _turnstile_token: Optional[str] = None
    _turnstile_time: float = 0
    _lock = asyncio.Lock()

    @classmethod
    async def _obtain_turnstile_token(cls, timeout: int = 30) -> str:
        """Launch CDP session on oxalpha.com to solve Turnstile when challenged."""
        debug.log("Starting CDP session for OxAlpha Turnstile token...")
        session = CDPSession(headless=False)
        await session.start()

        try:
            await session.navigate("https://oxalpha.com/chat")
            await asyncio.sleep(2)

            js_code = f"""
            (() => {{
                if (!document.getElementById("ox-turnstile-box")) {{
                    const div = document.createElement("div");
                    div.id = "ox-turnstile-box";
                    div.style.position = "fixed";
                    div.style.bottom = "20px";
                    div.style.right = "20px";
                    div.style.zIndex = "999999";
                    document.body.appendChild(div);
                }}
                window.oxTurnstileToken = null;
                if (window.turnstile) {{
                    window.turnstile.render("#ox-turnstile-box", {{
                        sitekey: "{SITEKEY}",
                        callback: function(token) {{
                            window.oxTurnstileToken = token;
                        }}
                    }});
                }}
            }})()
            """
            await session.evaluate_js(js_code)

            start_time = time.time()
            while time.time() - start_time < timeout:
                await asyncio.sleep(1)
                await session.bypass_turnstile()

                token = await session.evaluate_js("window.oxTurnstileToken")
                if token:
                    cls._turnstile_token = token
                    cls._turnstile_time = time.time()
                    return token

            raise RuntimeError("Failed to obtain Turnstile token for OxAlpha within timeout.")
        finally:
            await session.close()

    @classmethod
    async def get_turnstile_token(cls, force_refresh: bool = False) -> str:
        async with cls._lock:
            if not force_refresh and cls._turnstile_token and (time.time() - cls._turnstile_time < 120):
                return cls._turnstile_token
            return await cls._obtain_turnstile_token()

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
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": cls.url,
            "Referer": f"{cls.url}/chat",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ]
        }

        for attempt in range(2):
            if cls._turnstile_token and (time.time() - cls._turnstile_time < 120):
                headers["x-turnstile-token"] = cls._turnstile_token
            elif "x-turnstile-token" in headers:
                del headers["x-turnstile-token"]

            async with StreamSession(
                headers=headers,
                impersonate="chrome",
                proxy=proxy,
                timeout=120,
            ) as session:
                async with session.post(
                    cls.api_endpoint,
                    json=payload,
                    proxy=proxy,
                ) as response:
                    # 428 Precondition Required -> turnstile_required checkpoint
                    if response.status == 428:
                        debug.log("OxAlpha: Turnstile verification required (HTTP 428). Solving...")
                        await cls.get_turnstile_token(force_refresh=True)
                        continue

                    if response.status != 200:
                        text = await response.text()
                        raise RuntimeError(f"OxAlpha returned HTTP {response.status}: {text[:200]}")

                    async for line in response.iter_lines():
                        line_str = line.strip() if isinstance(line, str) else line.decode("utf-8", errors="ignore").strip()
                        if not line_str or not line_str.startswith("data:"):
                            continue
                        data_str = line_str[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                            choice = data.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass
                    return


Provider = OxAlpha
