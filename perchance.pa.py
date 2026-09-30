from __future__ import annotations

import json
import time
import random
import asyncio
import urllib.request
import urllib.parse
from typing import Optional

from g4f.Provider.base_provider import AsyncGeneratorProvider, ProviderModelMixin
from g4f.Provider.helper import format_prompt, format_media_prompt
from g4f.providers.response import ImageResponse
from g4f.providers.cache import FileStorage
from g4f.requests.cdp import CDPSession
from g4f.requests import StreamSession
from g4f.typing import AsyncResult, Messages
from g4f import debug


# FileStorage cache keys for verified userKeys
TOKEN_CACHE_KEY = "perchance/userKey"
IMAGE_TOKEN_CACHE_KEY = "perchance/imageUserKey"


def check_key_status(user_key: str, domain: str = "text-generation.perchance.org") -> bool:
    """Query Perchance API to check if userKey is verified."""
    url = f"https://{domain}/api/checkUserVerificationStatus?userKey={user_key}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": f"https://{domain}",
        "Referer": f"https://{domain}/embed",
        "Accept": "*/*"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") in ("verified", "already_verified", "success")
    except Exception:
        pass
    return False


class Perchance(AsyncGeneratorProvider, ProviderModelMixin):
    label = "Perchance"
    url = "https://perchance.org/ai-chat"
    api_endpoint = "https://text-generation.perchance.org/api/generate"
    image_api_endpoint = "https://image-generation.perchance.org/api/generate"
    verify_url = "https://text-generation.perchance.org/embed?thread=0"

    working = True
    supports_stream = True
    supports_system_message = True
    supports_message_history = True

    default_model = "perchance"
    default_image_model = "perchance-image"
    image_models = ["perchance-image"]
    models = [default_model] + image_models

    model_aliases = {
        "ai-chat": "perchance",
        "perchance-chat": "perchance",
        "perchance-image": "perchance-image",
    }

    _user_key: Optional[str] = None
    _image_user_key: Optional[str] = None
    _lock = asyncio.Lock()

    @classmethod
    def _load_cached_token(cls, is_image: bool = False) -> Optional[str]:
        cache_key = IMAGE_TOKEN_CACHE_KEY if is_image else TOKEN_CACHE_KEY
        domain = "image-generation.perchance.org" if is_image else "text-generation.perchance.org"
        try:
            data = FileStorage().get(cache_key)
        except Exception:
            data = None
        if isinstance(data, dict):
            k = data.get("userKey")
            if k and len(k) >= 16 and check_key_status(k, domain):
                return k
        elif isinstance(data, str) and len(data) >= 16:
            if check_key_status(data, domain):
                return data
        return None

    @classmethod
    def _save_cached_token(cls, user_key: str, is_image: bool = False):
        cache_key = IMAGE_TOKEN_CACHE_KEY if is_image else TOKEN_CACHE_KEY
        try:
            FileStorage().set(cache_key, {
                "userKey": user_key,
                "timestamp": time.time(),
                "domain": "image-generation.perchance.org" if is_image else "text-generation.perchance.org"
            })
        except Exception as e:
            debug.log(f"Failed to save Perchance token cache: {e}")

    @classmethod
    async def _obtain_token_via_cdp(cls, is_image: bool = False, timeout: int = 35) -> str:
        """Launch CDP session, solve Turnstile, and retrieve userKey."""
        domain = "image-generation.perchance.org" if is_image else "text-generation.perchance.org"
        if is_image:
            hash_param = urllib.parse.quote(json.dumps({"prompt": "a", "resolution": "512x512", "guidanceScale": 7}))
            url = f"https://{domain}/embed#{hash_param}"
        else:
            url = f"https://{domain}/embed?thread=0"

        debug.log(f"Starting CDP session for Perchance token ({domain})...")
        session = CDPSession(headless=False)
        await session.start()

        try:
            resp_queue = asyncio.Queue()
            session.add_event_handler("Network.responseReceived", resp_queue)

            try:
                await session.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
                await session.call("Network.enable")
            except Exception:
                pass

            await session.navigate(url)
            await asyncio.sleep(2)

            # Trigger verifyUser in page context
            try:
                await session.evaluate_js("if(typeof verifyUser === 'function') { window.verifyUserPromise = verifyUser(typeof window.thread !== 'undefined' ? window.thread : 0); } else if(typeof start === 'function') { start({reloadPageOnFail: false}); }")
            except Exception:
                pass

            await session.bypass_turnstile()

            start_time = time.time()
            while time.time() - start_time < timeout:
                # 1. Check network responses
                while not resp_queue.empty():
                    resp_event = resp_queue.get_nowait()
                    req_id = resp_event.get("requestId")
                    resp_url = resp_event.get("response", {}).get("url", "")
                    if "verifyUser" in resp_url and "token=" in resp_url:
                        try:
                            res = await session.call("Network.getResponseBody", requestId=req_id)
                            data = json.loads(res.get("body", "{}"))
                            if "userKey" in data and data["userKey"]:
                                if check_key_status(data["userKey"], domain):
                                    return data["userKey"]
                        except Exception:
                            pass

                # 2. Check localStorage
                try:
                    ls_key = await session.evaluate_js('localStorage.getItem("userKey-0") || localStorage.getItem("userKey") || localStorage.getItem("userKey-1")')
                    if ls_key and len(ls_key) >= 16:
                        if check_key_status(ls_key, domain):
                            return ls_key
                except Exception:
                    pass

                # 3. Check network requests for userKey
                for req_event in session.network_requests:
                    req_url = req_event.get("request", {}).get("url", "")
                    if "userKey=" in req_url:
                        try:
                            parsed = urllib.parse.urlparse(req_url)
                            qs = urllib.parse.parse_qs(parsed.query)
                            if "userKey" in qs and qs["userKey"] and qs["userKey"][0]:
                                net_key = qs["userKey"][0]
                                if check_key_status(net_key, domain):
                                    return net_key
                        except Exception:
                            pass

                # Re-trigger Turnstile bypass
                try:
                    await session.bypass_turnstile()
                except Exception:
                    pass

                await asyncio.sleep(1)

            raise RuntimeError(f"Failed to obtain verified Perchance userKey for {domain} within {timeout}s")
        finally:
            await session.close()

    @classmethod
    async def get_valid_user_key(cls, is_image: bool = False, force_refresh: bool = False) -> str:
        """Get verified userKey from memory, FileStorage cache, or fresh CDP run."""
        async with cls._lock:
            domain = "image-generation.perchance.org" if is_image else "text-generation.perchance.org"

            if not force_refresh:
                # 1. In-memory check
                key = cls._image_user_key if is_image else cls._user_key
                if key and check_key_status(key, domain):
                    return key

                # 2. FileStorage cache check
                cached = cls._load_cached_token(is_image)
                if cached:
                    if is_image:
                        cls._image_user_key = cached
                    else:
                        cls._user_key = cached
                    return cached

            # 3. Universal Cross-Platform CDP fallback
            fresh_key = await cls._obtain_token_via_cdp(is_image)
            if is_image:
                cls._image_user_key = fresh_key
            else:
                cls._user_key = fresh_key
            cls._save_cached_token(fresh_key, is_image)
            return fresh_key

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        prompt: str = None,
        proxy: str = None,
        **kwargs
    ) -> AsyncResult:
        model = cls.model_aliases.get(model, model)
        is_image = model in cls.image_models or kwargs.get("image", False)

        # -------------------------------------------------------------
        # Image Generation Flow
        # -------------------------------------------------------------
        if is_image:
            if not prompt:
                prompt = format_media_prompt(messages)

            for retry in range(2):
                user_key = await cls.get_valid_user_key(is_image=True, force_refresh=(retry > 0))
                request_id = str(random.random())
                cache_bust = random.random()

                gen_url = (
                    f"{cls.image_api_endpoint}"
                    f"?userKey={user_key}"
                    f"&requestId={request_id}"
                    f"&adAccessCode="
                    f"&__cacheBust={cache_bust}"
                )

                payload = {
                    "prompt": prompt,
                    "negativePrompt": kwargs.get("negative_prompt", ""),
                    "seed": kwargs.get("seed", -1),
                    "resolution": kwargs.get("resolution", "768x768"),
                    "guidanceScale": kwargs.get("guidance_scale", 7.0),
                    "channel": "ai-text-to-image-generator",
                    "subChannel": "public",
                    "userKey": user_key,
                    "adAccessCode": "",
                    "requestId": request_id,
                }

                headers = {
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Accept": "*/*",
                    "Origin": "https://image-generation.perchance.org",
                    "Referer": "https://image-generation.perchance.org/embed",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }

                async with StreamSession(headers=headers, impersonate="chrome") as session:
                    key_invalid = False
                    for _ in range(15):
                        async with session.post(gen_url, data=json.dumps(payload), proxy=proxy) as resp:
                            if resp.status != 200:
                                cls._image_user_key = None
                                key_invalid = True
                                break

                            data = await resp.json()
                            status = data.get("status")
                            if status == "success":
                                download_url = data.get("imageDownloadUrl")
                                if not download_url and data.get("imageId"):
                                    download_url = f"https://image-generation.perchance.org/api/downloadTemporaryImageViaProxy?t={data['imageId']}"
                                elif download_url and download_url.startswith("/"):
                                    download_url = "https://image-generation.perchance.org" + download_url
                                if download_url:
                                    yield ImageResponse([download_url], alt=prompt)
                                    return
                            elif status in ("waiting", "pending", "queue", "waiting_for_prev_request_to_finish"):
                                await asyncio.sleep(2)
                                continue
                            elif status == "invalid_key":
                                cls._image_user_key = None
                                key_invalid = True
                                break
                            else:
                                raise RuntimeError(f"Image generation failed: {data}")
                    if not key_invalid:
                        break
            return

        # -------------------------------------------------------------
        # Text Generation Flow
        # -------------------------------------------------------------
        user_key = await cls.get_valid_user_key(is_image=False)
        user_prompt = messages[-1]["content"] if isinstance(messages, list) and messages else str(messages)
        instruction = (
            "Please write the next 1-2 messages for the following chat/RP. "
            "Default to a helpful, authentic tone.\n\n"
            "# Reminders:\n"
            "- Avoid unnecessary repetition.\n\n"
            "# Here's the initial scenario and world info:\n---\n(None specified. Freeform.)\n---\n\n"
            "# Here's what has happened so far in this roleplay/chat/story:\n<MESSAGES>\n(No prior chat messages.)\n</MESSAGES>\n---\n\n"
            "Your task is to write the response for Assistant in this roleplay/chat."
        )
        start_with = f"User: {user_prompt}\n\nAssistant:"
        stop_sequences = ["\n\nUser:", "\nUser:", "\nAssistant:"]

        payload = {
            "instruction": instruction,
            "startWith": start_with,
            "stopSequences": stop_sequences,
            "generatorName": "ai-chat",
            "startWithTokenCount": len(start_with.split()),
            "instructionTokenCount": max(1, len(instruction.split())),
        }

        request_id = f"aiTextCompletion{random.randint(1000000000000000, 9999999999999999)}"
        cache_bust = round(random.random(), 16)

        gen_url = (
            f"{cls.api_endpoint}"
            f"?userKey={user_key}"
            f"&thread=0"
            f"&requestId={request_id}"
            f"&__cacheBust={cache_bust}"
        )

        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*",
            "Origin": "https://text-generation.perchance.org",
            "Referer": "https://text-generation.perchance.org/embed",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        async with StreamSession(headers=headers, impersonate="chrome") as session:
            async with session.post(gen_url, data=json.dumps(payload), proxy=proxy) as resp:
                if resp.status != 200:
                    cls._user_key = None
                    text = await resp.text()
                    raise RuntimeError(f"Perchance API error {resp.status}: {text}")

                current_text = ""
                buffer = ""
                async for chunk_bytes in resp.iter_content():
                    if not chunk_bytes:
                        continue
                    if isinstance(chunk_bytes, bytes):
                        chunk_str = chunk_bytes.decode("utf-8", errors="replace")
                    else:
                        chunk_str = str(chunk_bytes)
                    buffer += chunk_str
                    lines = buffer.split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('t:"'):
                            try:
                                chunk = json.loads(line[2:])
                                if chunk.startswith(current_text):
                                    delta = chunk[len(current_text):]
                                    current_text = chunk
                                elif current_text.startswith(chunk):
                                    delta = ""
                                else:
                                    delta = chunk
                                    current_text += chunk
                                if delta:
                                    yield delta
                            except Exception:
                                pass

Provider = Perchance
