from __future__ import annotations

import os
import sys
import json
import time
import uuid
import asyncio
import urllib.request
from pathlib import Path
from typing import Any, Optional

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.providers.response import Sources
from g4f.requests import StreamSession
from g4f.requests.raise_for_status import raise_for_status
from g4f.requests.cdp import CDPSession
from g4f.typing import AsyncResult, Messages
from g4f import debug


CANDIDATE_TOKEN_FILES = [
    Path.home() / ".config" / "g4f" / "felo_cf_token.json",
    Path.home() / ".local" / "share" / "g4f" / "felo_cf_token.json",
]

SITEKEY = "0x4AAAAAAAylbZ7VusOzg5FJ"


class Felo(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Felo"
    url = "https://felo.ai"
    working = True
    needs_auth = False
    supports_stream = True
    supports_system_message = False
    supports_message_history = False

    default_model = "felo-chat"

    # Mapping of g4f model names to Felo categories
    model_aliases = {
        "felo-chat": "chat",
        "felo-search": "google",
        "felo-scholar": "scholar",
        "felo-social": "social",
        "felo-document": "document",
    }

    models = list(model_aliases.keys())

    _cached_cf_token: Optional[str] = None
    _cached_token_time: float = 0
    _lock = asyncio.Lock()

    @classmethod
    def _load_cached_token(cls) -> Optional[str]:
        for p in CANDIDATE_TOKEN_FILES:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        token = data.get("cf_token")
                        ts = data.get("timestamp", 0)
                        # cf_token is valid for ~1 hour, keep 45 min margin
                        if token and (time.time() - ts < 45 * 60):
                            return token
                except Exception:
                    pass
        return None

    @classmethod
    def _save_cached_token(cls, token: str):
        target = CANDIDATE_TOKEN_FILES[0]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"cf_token": token, "timestamp": time.time()}, f)
        except Exception as e:
            debug.log(f"Failed to save Felo cf_token: {e}")

    @classmethod
    async def _obtain_cf_token_via_cdp(cls, timeout: int = 30) -> str:
        """Launch CDP session, solve Turnstile on felo.ai, and exchange for cf_token."""
        debug.log("Starting CDP session for Felo Turnstile...")
        session = CDPSession(headless=False)
        await session.start()

        try:
            await session.navigate("https://felo.ai/search")
            await asyncio.sleep(2)

            js_code = f"""
            (() => {{
                if (!document.getElementById("felo-turnstile-box")) {{
                    const div = document.createElement("div");
                    div.id = "felo-turnstile-box";
                    div.style.position = "fixed";
                    div.style.bottom = "20px";
                    div.style.right = "20px";
                    div.style.zIndex = "999999";
                    document.body.appendChild(div);
                }}

                window.feloTurnstileToken = null;

                function renderWidget() {{
                    if (window.turnstile) {{
                        window.turnstile.render("#felo-turnstile-box", {{
                            sitekey: "{SITEKEY}",
                            callback: function(token) {{
                                window.feloTurnstileToken = token;
                            }}
                        }});
                    }}
                }}

                if (!window.turnstile) {{
                    window.onloadTurnstileCallback = renderWidget;
                    const script = document.createElement("script");
                    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onloadTurnstileCallback&render=explicit";
                    document.body.appendChild(script);
                }} else {{
                    renderWidget();
                }}
            }})()
            """
            await session.evaluate_js(js_code)

            start_time = time.time()
            while time.time() - start_time < timeout:
                await asyncio.sleep(1)
                await session.bypass_turnstile()

                raw_token = await session.evaluate_js("window.feloTurnstileToken")
                if raw_token:
                    verify_url = "https://felo.ai/api-proxy/main/search/turnstile/verify"
                    req = urllib.request.Request(
                        verify_url,
                        data=raw_token.encode(),
                        headers={
                            "Content-Type": "text/plain;charset=UTF-8",
                            "Origin": "https://felo.ai",
                            "Referer": "https://felo.ai/search",
                            "User-Agent": await session.get_user_agent(),
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                        cf_token = data.get("cf_token")
                        if cf_token:
                            cls._save_cached_token(cf_token)
                            return cf_token

            raise RuntimeError("Failed to obtain Turnstile token for Felo AI within timeout.")
        finally:
            await session.close()

    @classmethod
    async def get_valid_cf_token(cls, force_refresh: bool = False) -> str:
        async with cls._lock:
            if not force_refresh:
                if cls._cached_cf_token and (time.time() - cls._cached_token_time < 45 * 60):
                    return cls._cached_cf_token
                disk_token = cls._load_cached_token()
                if disk_token:
                    cls._cached_cf_token = disk_token
                    cls._cached_token_time = time.time()
                    return disk_token

            fresh = await cls._obtain_cf_token_via_cdp()
            cls._cached_cf_token = fresh
            cls._cached_token_time = time.time()
            return fresh

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        proxy: str = None,
        **kwargs: Any,
    ) -> AsyncResult:
        prompt = messages[-1]["content"] if isinstance(messages, list) and messages else str(messages)
        category = cls.get_model(model)

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": cls.url,
            "Referer": f"{cls.url}/search",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }

        for retry in range(2):
            cf_token = await cls.get_valid_cf_token(force_refresh=(retry > 0))
            search_uuid = str(uuid.uuid4())

            payload = {
                "query": prompt,
                "search_uuid": search_uuid,
                "lang": "",
                "agent_lang": "en",
                "search_options": {"langcode": "en-US"},
                "search_video": True,
                "query_from": "default",
                "category": category,
                "model": "",
                "auto_routing": True,
                "mode": "concise",
                "device_id": uuid.uuid4().hex,
                "source_message_rid": "",
                "documents": [],
                "document_action": "",
                "slides_source": {"type": "ask_question", "files": {}},
                "slide_template_uid": "",
                "selected_resource_ids": [],
                "cf_token": cf_token,
                "process_id": search_uuid,
                "stream_protocol": "message_center_v1",
                "enable_task_state": True,
            }

            async with StreamSession(
                headers=headers,
                impersonate="chrome",
                proxy=proxy,
                timeout=120,
            ) as session:
                # 1. Start thread
                threads_url = f"{cls.url}/api-proxy/main/search/threads"
                async with session.post(threads_url, json=payload) as response:
                    if response.status == 400:
                        err_text = await response.text()
                        if "turnstile" in err_text:
                            cls._cached_cf_token = None
                            continue
                    await raise_for_status(response)
                    res_json = await response.json()
                    stream_key = res_json.get("stream_key")
                    if not stream_key:
                        raise RuntimeError("Failed to get stream_key from Felo response")

                # 2. Stream response
                stream_url = f"{cls.url}/api/message/v1/stream/{stream_key}?offset=0"
                async with session.get(stream_url) as stream_res:
                    await raise_for_status(stream_res)

                    previous_text = ""
                    sources_to_yield = None
                    async for line in stream_res.iter_lines():
                        line = line.decode("utf-8").strip()
                        if line.startswith("data:{"):
                            try:
                                data = json.loads(line[5:])
                                if "content" in data:
                                    content_str = data["content"]
                                    if isinstance(content_str, str):
                                        content_json = json.loads(content_str)
                                        data_type = content_json.get("data", {}).get("type")

                                        if data_type == "answer":
                                            text = (
                                                content_json.get("data", {})
                                                .get("data", {})
                                                .get("text", "")
                                            )
                                            if text.startswith(previous_text):
                                                new_part = text[len(previous_text):]
                                                if new_part:
                                                    yield new_part
                                                    previous_text = text
                                            else:
                                                yield text
                                                previous_text = text

                                        elif data_type == "final_contexts":
                                            sources_list = (
                                                content_json.get("data", {})
                                                .get("data", {})
                                                .get("sources", [])
                                            )
                                            if sources_list:
                                                formatted = [
                                                    {"url": s.get("link"), "title": s.get("title")}
                                                    for s in sources_list
                                                    if s.get("link")
                                                ]
                                                if formatted:
                                                    sources_to_yield = Sources(formatted)
                            except Exception:
                                continue

                    if sources_to_yield:
                        yield sources_to_yield
                return


Provider = Felo
