#!/usr/bin/env python3
"""
Test all PA providers with their default model.

Discovers every .pa.py file in this directory, imports its Provider class,
and calls create_async_generator with a simple "Hello" prompt.  Results
are printed in a compact table with timing and the first ~120 chars of
the response.

Usage:
    python test_all.py [--timeout 30] [--proxy http://...]

Exit codes:
    0  — all providers returned a response
    1  — at least one provider failed
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# -- g4f bootstrap ----------------------------------------------------------
# Make sure the g4f package from the main workspace is importable.
_HERE = Path(__file__).resolve().parent
for candidate in [
    _HERE / ".." / ".." / "Documents" / "gpt4free",   # sibling workspace layout
    _HERE.parent / "gpt4free",                         # pa-providers/../gpt4free
    Path(r"c:\Users\heine\Documents\gpt4free"),        # absolute fallback
    _HERE,                                             # if g4f is alongside
]:
    candidate = candidate.resolve()
    if (candidate / "g4f" / "__init__.py").exists():
        sys.path.insert(0, str(candidate))
        break

from g4f.typing import Messages  # noqa: E402

# -- Helpers ---------------------------------------------------------------

PROMPT: Messages = [{"role": "user", "content": "Hello! Reply with one short sentence."}]

# ANSI colours
C_RESET = "\033[0m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"


def _discover_providers(directory: Path) -> list[tuple[str, str, type]]:
    """Return [(file_name, label, Provider_class), ...] sorted by file name."""
    results: list[tuple[str, str, type]] = []
    for pa_file in sorted(directory.glob("*.pa.py")):
        module_name = pa_file.stem.replace("-", "_")  # e.g. "llm7.pa" → "llm7_pa"
        spec = importlib.util.spec_from_file_location(module_name, pa_file)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001
            print(f"  {C_RED}X  import failed: {pa_file.name}: {exc}{C_RESET}")
            continue
        provider_cls = getattr(mod, "Provider", None)
        if provider_cls is None:
            continue
        label = getattr(provider_cls, "label", pa_file.stem)
        results.append((pa_file.name, label, provider_cls))
    return results


async def _test_one(
    file_name: str,
    label: str,
    provider_cls: type,
    timeout: float,
    proxy: str | None,
) -> dict[str, Any]:
    """Run a single provider and return a result dict."""
    result: dict[str, Any] = {
        "file": file_name,
        "label": label,
        "model": getattr(provider_cls, "default_model", "?"),
        "working_flag": getattr(provider_cls, "working", "?"),
        "ok": False,
        "text": "",
        "elapsed": 0.0,
        "error": "",
    }

    # Skip providers explicitly marked as not working
    if result["working_flag"] is False:
        result["error"] = "marked working=False (skipped)"
        return result

    start = time.perf_counter()
    try:
        text_parts: list[str] = []
        full_response_parts: list[str] = []  # fallback if no streaming strings
        gen = provider_cls.create_async_generator(
            model=result["model"],
            messages=PROMPT,
            proxy=proxy,
        )
        async for chunk in gen:
            if isinstance(chunk, str):
                text_parts.append(chunk)
            elif isinstance(chunk, dict):
                choices = chunk.get("choices")
                if choices and isinstance(choices, list):
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        text_parts.append(content)
                    else:
                        msg = choices[0].get("message", {})
                        content = msg.get("content")
                        if content:
                            full_response_parts.append(content)
                else:
                    content = chunk.get("content") or chunk.get("text")
                    if content:
                        full_response_parts.append(content)
            elif hasattr(chunk, "get_dict"):
                d = chunk.get_dict()
                choices = d.get("choices")
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message", {})
                    content = msg.get("content")
                    if content:
                        full_response_parts.append(content)
                else:
                    content = d.get("content") or d.get("text")
                    if content:
                        full_response_parts.append(content)
        # Prefer streaming strings; fall back to full response objects
        result["text"] = ("".join(text_parts) if text_parts else "".join(full_response_parts)).strip()
        result["ok"] = bool(result["text"])
        if not result["ok"]:
            result["error"] = "empty response"
    except asyncio.TimeoutError:
        result["error"] = f"timeout after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        # Include traceback for debugging in verbose mode
        result["_tb"] = traceback.format_exc()
    finally:
        result["elapsed"] = time.perf_counter() - start
    return result


async def _run_all(timeout: float, proxy: str | None, verbose: bool) -> int:
    directory = Path(__file__).resolve().parent
    providers = _discover_providers(directory)

    if not providers:
        print(f"{C_RED}No PA providers found in {directory}{C_RESET}")
        return 1

    print(f"{C_BOLD}Testing {len(providers)} PA providers{C_RESET}")
    print(f"  Prompt: {PROMPT[0]['content']}")
    print(f"  Timeout: {timeout:.0f}s per provider")
    if proxy:
        print(f"  Proxy: {proxy}")
    print()

    # Run providers sequentially to avoid rate-limit interference
    results: list[dict[str, Any]] = []
    for file_name, label, cls in providers:
        # Wrap in timeout
        coro = _test_one(file_name, label, cls, timeout, proxy)
        try:
            res = await asyncio.wait_for(coro, timeout=timeout + 5)
        except asyncio.TimeoutError:
            res = {
                "file": file_name,
                "label": label,
                "model": getattr(cls, "default_model", "?"),
                "working_flag": getattr(cls, "working", "?"),
                "ok": False,
                "text": "",
                "elapsed": timeout,
                "error": f"hard timeout after {timeout + 5:.0f}s",
            }
        results.append(res)
        _print_result(res, verbose)

    # Summary
    print()
    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    skipped = sum(1 for r in results if "skipped" in r.get("error", ""))
    print(f"{C_BOLD}--- Summary ---{C_RESET}")
    print(f"  {C_GREEN}OK {ok} succeeded{C_RESET}")
    if skipped:
        print(f"  {C_YELLOW}SK {skipped} skipped{C_RESET}")
    print(f"  {C_RED}X  {fail - skipped} failed{C_RESET}")
    print(f"  Total: {len(results)}")

    # Table
    print()
    print(f"{C_BOLD}{'Provider':<20} {'Model':<30} {'Status':<8} {'Time':>7}  Response / Error{C_RESET}")
    print("-" * 100)
    for r in results:
        status = f"{C_GREEN}OK{C_RESET}" if r["ok"] else f"{C_RED}FAIL{C_RESET}"
        if "skipped" in r.get("error", ""):
            status = f"{C_YELLOW}SKIP{C_RESET}"
        label = r["label"][:19]
        model = r["model"][:29]
        elapsed = f"{r['elapsed']:.1f}s"
        detail = r["text"][:60] if r["ok"] else r["error"][:60]
        print(f"{label:<20} {model:<30} {status:<17} {elapsed:>7}  {detail}")

    return 0 if fail == 0 else 1


def _print_result(res: dict[str, Any], verbose: bool) -> None:
    status = (
        f"{C_GREEN}OK{C_RESET}" if res["ok"]
        else f"{C_YELLOW}SK{C_RESET}" if "skipped" in res.get("error", "")
        else f"{C_RED}X {C_RESET}"
    )
    print(f"  {status} {res['label']:<16} {res['model']:<28} {res['elapsed']:>5.1f}s", end="")
    if res["ok"]:
        snippet = res["text"][:120].replace("\n", " ")
        try:
            print(f"  {C_DIM}{snippet}{C_RESET}")
        except UnicodeEncodeError:
            print(f"  {C_DIM}{snippet.encode('ascii', 'replace').decode()}{C_RESET}")
    else:
        try:
            print(f"  {C_RED}{res['error']}{C_RESET}")
        except UnicodeEncodeError:
            print(f"  {C_RED}{res['error'].encode('ascii', 'replace').decode()}{C_RESET}")
        if verbose and "_tb" in res:
            print(f"  {C_DIM}{res['_tb']}{C_RESET}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test all PA providers")
    parser.add_argument("--timeout", type=float, default=30, help="Timeout per provider in seconds")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tracebacks")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run_all(args.timeout, args.proxy, args.verbose)))


if __name__ == "__main__":
    main()