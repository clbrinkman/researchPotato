#!/usr/bin/env python3
from __future__ import annotations
"""
KB query embedding via Qwen3-VL-Embedding.
Usage: python3 embed.py <text>
Output: JSON float list (4096-dim) on stdout, or exits with code 1 if unavailable.

Fallback chain:
  1. HTTP embedding proxy — if KB_EMBED_PROXY_URL is set (injected by the desktop main process)
  2. octo_rpc Qwen3-VL-Embedding — works in server environments
  3. KB_EMBED_URL custom HTTP endpoint — if set
  4. Exit code 1 (caller falls back to FTS5-only mode)
"""
import ast
import json
import os
import sys

# HTTP proxy config — read from env (injected by the desktop main process).
# Empty URL means the proxy is unavailable; embed_via_proxy() returns None immediately.
_PROXY_URL = os.environ.get("KB_EMBED_PROXY_URL", "")
_PROXY_SCENE_ID = int(os.environ.get("KB_EMBED_PROXY_SCENE_ID", "0") or "0")
_PROXY_ABILITY_ID = int(os.environ.get("KB_EMBED_PROXY_ABILITY_ID", "0") or "0")

# Query-side instruction for Qwen3-VL-Embedding (asymmetric retrieval):
# Documents are indexed with a generic instruction; queries use a task-specific one
# to bridge the "short query vs long document" distribution gap.
QUERY_INSTRUCTION = (
    "Retrieve the most relevant AI and machine learning "
    "research papers and news articles for the following query"
)


def _parse_raw_embedding(raw) -> list[float] | None:
    """Parse embedding from raw value: list or string-encoded list."""
    if isinstance(raw, str):
        # Try JSON first (superset of Python literal syntax), then ast.literal_eval
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            try:
                raw = ast.literal_eval(raw)
            except Exception:
                return None
    if isinstance(raw, list) and raw:
        if isinstance(raw[0], list):
            raw = raw[0]  # unwrap batch dimension
        if not raw:  # empty after batch unwrap
            return None
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
    return None


def embed_via_proxy(text: str, max_retries: int = 3) -> list[float] | None:
    """Call Qwen3-VL-Embedding via the configured HTTP proxy (works locally).

    Retries on rate-limit (code=5) and transient network errors with
    exponential backoff: 1s, 2s, 4s between attempts.
    Returns None immediately if KB_EMBED_PROXY_URL is not configured.
    """
    if not _PROXY_URL:
        return None
    import time
    import urllib.request

    body = json.dumps({
        "sceneId": _PROXY_SCENE_ID,
        "abilityId": _PROXY_ABILITY_ID,
        "requestToken": "",
        "requestData": {
            "text": text,
            "scene": "text",
            "version": "v2",
            "instruction": QUERY_INSTRUCTION,
        },
    }).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                _PROXY_URL,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            if data.get("code") == 0:
                raw = data.get("responseData", {}).get("Embedding")
                if raw is None:
                    print("[embed] proxy: responseData.Embedding missing", file=sys.stderr)
                    return None
                return _parse_raw_embedding(raw)

            # Non-zero code: log and decide whether to retry
            code = data.get("code")
            msg = data.get("message", "")
            print(f"[embed] proxy code={code}: {msg}", file=sys.stderr)
            # Only retry on rate-limit (5) or server errors (≥500 analogs)
            if code not in (5,) or attempt == max_retries - 1:
                return None

        except Exception as e:
            print(f"[embed] proxy attempt {attempt + 1} error: {e}", file=sys.stderr)
            if attempt == max_retries - 1:
                return None

        delay = 2 ** attempt  # 1s, 2s, 4s
        print(f"[embed] proxy retry {attempt + 1}/{max_retries - 1} in {delay}s...", file=sys.stderr)
        time.sleep(delay)

    return None


def embed_via_octo_rpc(text: str) -> list[float] | None:
    """Call Qwen3-VL-Embedding via octo_rpc (server environments only).

    Tries with task-specific instruction first; falls back to text-only
    if the service rejects the instruction field.
    """
    try:
        from octo_rpc import OctoRpcClient  # type: ignore
        client = OctoRpcClient(
            service_name="tagcv-serving-embedding-hbc",
            model_name="Qwen3-VL-Embedding",
        )
        # Attempt 1: with query instruction (asymmetric retrieval)
        try:
            resp = client.infer(inputs={"text": text, "instruction": QUERY_INSTRUCTION})
            result = _parse_raw_embedding(
                resp.get("embedding") or resp.get("embeddings")
            )
            if result is not None:
                return result
        except Exception:
            pass  # instruction field not supported — retry without it
        # Attempt 2: text only
        resp = client.infer(inputs={"text": text})
        return _parse_raw_embedding(resp.get("embedding") or resp.get("embeddings"))
    except ImportError:
        pass  # octo_rpc not installed
    except Exception as e:
        print(f"[embed] octo_rpc error: {e}", file=sys.stderr)
    return None


def embed_via_custom_url(text: str) -> list[float] | None:
    """Custom HTTP endpoint fallback — set KB_EMBED_URL env to enable."""
    url = os.environ.get("KB_EMBED_URL")
    if not url:
        return None
    try:
        import urllib.request
        data = json.dumps({
            "text": text,
            "model": "Qwen3-VL-Embedding",
            "instruction": QUERY_INSTRUCTION,
        }).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        return _parse_raw_embedding(
            body.get("embedding") or body.get("embeddings")
        )
    except Exception as e:
        print(f"[embed] custom URL error: {e}", file=sys.stderr)
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: embed.py <text>", file=sys.stderr)
        sys.exit(1)

    text = " ".join(sys.argv[1:])

    embedding = embed_via_proxy(text)
    if embedding is None:
        embedding = embed_via_octo_rpc(text)
    if embedding is None:
        embedding = embed_via_custom_url(text)
    if not embedding:  # catches None and []
        print("[embed] No embedding service available (FTS5-only mode)", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(embedding))


if __name__ == "__main__":
    main()
