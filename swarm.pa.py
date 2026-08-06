from __future__ import annotations

import asyncio
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import warnings
warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")
warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")
import requests as _requests

from g4f.Provider.template import OpenaiTemplate
from g4f.errors import BadRequestError
from g4f.providers.cache import FileStorage
from g4f.typing import AsyncResult, Messages
from g4f import debug

# ---------------------------------------------------------------------------
# Worker seed list (nip.io public Ollama instances used by the CF Worker)
# ---------------------------------------------------------------------------
_SEED_URL = "https://raw.githubusercontent.com/gpt4free/g4f.dev/refs/heads/main/dist/js/swarm_seeds.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_WORKER_BASE = "https://swarm.g4f-dev.workers.dev"
_PROBE_TIMEOUT = 5
_TTFT_TIMEOUT = 10.0
_PROBE_WORKERS = 20
_CACHE_TTL = 3600  # seconds


def _fetch_seed_servers() -> list[str]:
    """Fetch the seed list from the remote JSON, deduplicated, order-preserved."""
    try:
        resp = _requests.get(_SEED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        seeds = data if isinstance(data, list) else data.get("seeds", [])
        seeds = [s for s in seeds if isinstance(s, str) and s]
        return list(dict.fromkeys(seeds))
    except Exception as e:
        debug.error(f"Swarm: failed to fetch seed list from {_SEED_URL}: {e}")
        return []


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _probe_server(url: str) -> tuple[str, list[str]] | None:
    """Probe one server's /v1/models. Returns (url, [model_id, ...]) or None."""
    try:
        resp = _requests.get(f"{url}/v1/models", timeout=_PROBE_TIMEOUT, verify=False)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = [m.get("id", "") for m in data if m.get("id")]
        if models:
            return url, models
    except Exception:
        pass
    return None


def _fetch_worker_servers() -> dict[str, list[str]]:
    """
    Ask the CF Worker for its already-discovered server map.
    GET /servers/all → { data: { serverUrl: [models] }, ... }
    Returns {} on failure.
    """
    try:
        resp = _requests.get(f"{_WORKER_BASE}/servers/all", timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", {})
        if isinstance(data, dict) and data:
            debug.log(f"Swarm: fetched {len(data)} servers from worker cache")
            return data
    except Exception as e:
        debug.error(f"Swarm: /servers/all failed: {e}")
    return {}


def _probe_seeds(seeds: list[str]) -> dict[str, list[str]]:
    """Probe seed list concurrently. Returns {url: [models]}."""
    alive: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
        futures = {pool.submit(_probe_server, url): url for url in seeds}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                url, models = result
                alive[url] = models
    debug.log(f"Swarm: {len(alive)}/{len(seeds)} seed servers alive")
    return alive


def _discover() -> dict[str, list[str]]:
    """
    Build the full alive-servers map.

    Strategy:
    1. Check the local FileStorage cache (TTL-bucketed key).
    2. Fetch pre-discovered servers from the CF Worker cache (/servers/all).
    3. Also probe the seed list directly in parallel.
    4. Merge — direct probe results overwrite worker cache for same URL.
    5. Persist the merged map back to FileStorage.
    """
    cache_key = f"swarm_worker_servers_{int(time.time() // _CACHE_TTL)}"
    storage = FileStorage()
    cached = storage.get(cache_key)
    if cached is not None:
        debug.log(f"Swarm: using cached server map ({len(cached)} servers)")
        return cached
    worker_servers = _fetch_worker_servers()
    seed_alive = _probe_seeds(_fetch_seed_servers())
    result = {**worker_servers, **seed_alive}
    try:
        storage.set(cache_key, result)
    except Exception as e:
        debug.error(f"Swarm: failed to persist server map cache: {e}")
    return result


def _build_model_map(alive: dict[str, list[str]]) -> dict[str, list[str]]:
    """Build {model: [server_url, ...]} from alive map."""
    model_to_servers: dict[str, list[str]] = {}
    for server_url, models in alive.items():
        for m in models:
            model_to_servers.setdefault(m, []).append(server_url)
    return model_to_servers


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class Provider(OpenaiTemplate):
    label = "Swarm"
    url = _WORKER_BASE
    base_url = f"{_WORKER_BASE}/v1"
    working = True

    models: list[str] = []
    models_count: dict[str, int] = {}
    image_models: list[str] = []

    # Direct-routing state
    model_to_servers: dict[str, list[str]] = {}
    _cache_time: float = 0.0

    # ---------------------------------------------------------------------------

    @classmethod
    def _refresh(cls) -> None:
        """Discover nodes and rebuild model map."""
        alive = _discover()
        model_to_servers = _build_model_map(alive)

        cls.model_to_servers = model_to_servers
        cls.models_count = {m: len(servers) for m, servers in model_to_servers.items()}
        cls.models = sorted(
            model_to_servers.keys(),
            key=lambda m: cls.models_count[m],
            reverse=True,
        )
        cls.image_models = [
            m for m in cls.models
            if any(kw in m.lower() for kw in ("flux", "sdxl", "diffusion", "imagen", "dall-e"))
        ]
        if cls.default_model not in cls.models and cls.models:
            cls.default_model = cls.models[0]
        cls._cache_time = time.time()
        debug.log(f"Swarm: {len(cls.models)} models across {len(alive)} servers")

    @classmethod
    def get_models(cls, api_key: str = None, base_url: str = None, **kwargs) -> list[str]:
        if not cls.models or (time.time() - cls._cache_time) > _CACHE_TTL:
            cls._refresh()
        # Last-resort: pull model list from worker's /v1/models
        if not cls.models:
            try:
                resp = _requests.get(f"{cls.base_url}/models", timeout=10)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                cls.models = [m["id"] for m in data if m.get("id")]
                cls.models_count = {m["id"]: m.get("count", 1) for m in data if m.get("id")}
            except Exception:
                pass
        return cls.models

    # ---------------------------------------------------------------------------

    @classmethod
    async def create_async_generator(
        cls,
        model: str,
        messages: Messages,
        api_key: str = None,
        base_url: str = None,
        **kwargs: Any,
    ) -> AsyncResult:
        if not cls.models:
            cls.get_models()

        if not model or model == "default":
            model = cls.default_model

        # Resolve aliases
        server_urls = cls.model_to_servers.get(model)
        if server_urls is None:
            try:
                resolved = cls.get_model(model)
                server_urls = cls.model_to_servers.get(resolved)
                if server_urls is not None:
                    model = resolved
            except Exception:
                pass

        # No direct servers known — route through CF Worker
        if not server_urls:
            debug.log(f"Swarm: no direct nodes for '{model}', routing via CF Worker")
            async for chunk in super().create_async_generator(
                model, messages,
                api_key=api_key,
                base_url=cls.base_url,
                **kwargs,
            ):
                yield chunk
            return

        # Shuffle for load distribution
        candidates = random.sample(server_urls, len(server_urls))
        first_chunk_yielded = False

        for server_url in candidates:
            node_base = f"{server_url}/v1"
            debug.log(f"Swarm: trying direct node {server_url} for '{model}'")
            first_chunk_yielded = False
            try:
                gen = super().create_async_generator(
                    model, messages,
                    api_key=api_key,
                    base_url=node_base,
                    yield_request=False,
                    **kwargs,
                )

                # TTFT gate — wait at most _TTFT_TIMEOUT seconds for the first chunk
                try:
                    first = await asyncio.wait_for(gen.__anext__(), timeout=_TTFT_TIMEOUT)
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError:
                    raise Exception(f"TTFT timeout (>{_TTFT_TIMEOUT}s) on {server_url}")

                first_chunk_yielded = True
                yield first

                async for chunk in gen:
                    yield chunk

                # Promote winning server to front for next request
                cls.model_to_servers[model] = [server_url] + [
                    s for s in cls.model_to_servers[model] if s != server_url
                ]
                return

            except BadRequestError:
                raise  # model invalid — no point trying other servers

            except Exception as e:
                debug.error(f"Swarm: {server_url} failed: {e}")
                if first_chunk_yielded:
                    raise  # already streaming — can't switch mid-stream
                continue  # try next server

        # All direct nodes failed — fall back to CF Worker
        debug.log(f"Swarm: all {len(candidates)} direct nodes failed, falling back to CF Worker")
        async for chunk in super().create_async_generator(
            model, messages,
            api_key=api_key,
            base_url=cls.base_url,
            **kwargs,
        ):
            yield chunk
