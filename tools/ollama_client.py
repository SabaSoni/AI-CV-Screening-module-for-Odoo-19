# -*- coding: utf-8 -*-
"""Minimal Ollama HTTP client (chat with JSON-schema output + embeddings).

Only `requests` is needed, which ships with Odoo. Ollama runs locally, so no
API key, no credits, and CV data never leaves the machine.
"""
import json
import re

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class OllamaError(Exception):
    pass


def parse_json(text):
    """Tolerant JSON parsing: strips code fences and leading/trailing prose."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise OllamaError("Model did not return valid JSON: %s" % text[:200])


class OllamaClient:
    def __init__(self, base_url="http://127.0.0.1:11434", timeout=600):
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.timeout = timeout

    def _url(self, path):
        return self.base_url + path

    def _require_requests(self):
        if requests is None:
            raise OllamaError("The 'requests' library is not available.")

    def is_available(self):
        try:
            self.list_models()
            return True
        except Exception:
            return False

    def version(self):
        self._require_requests()
        r = requests.get(self._url("/api/version"), timeout=10)
        r.raise_for_status()
        return r.json().get("version", "")

    def list_models(self, timeout=10):
        self._require_requests()
        r = requests.get(self._url("/api/tags"), timeout=timeout)
        r.raise_for_status()
        return [m.get("name") or m.get("model") for m in r.json().get("models", [])]

    def has_model(self, name, names=None):
        names = set(names if names is not None else self.list_models())
        return name in names or (":" not in name and name + ":latest" in names)

    def chat_json(self, model, system, user, schema=None, num_ctx=8192,
                  temperature=0.0, num_predict=2048, think=None, seed=7):
        """Run one chat turn and return (parsed_json, raw_response).

        `seed` makes the answer repeatable (same CV + same criteria => same answer); the
        caller may pass another seed to retry after a broken answer."""
        self._require_requests()
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema if schema else "json",
            "options": {
                "temperature": temperature,
                "seed": int(seed),
                "num_ctx": int(num_ctx),
                "num_predict": int(num_predict),
            },
            "keep_alive": "15m",
        }
        if think is not None:
            payload["think"] = bool(think)
        r = requests.post(self._url("/api/chat"), json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise OllamaError("Ollama /api/chat returned %s: %s" % (r.status_code, r.text[:300]))
        data = r.json()
        content = (data.get("message") or {}).get("content") or ""
        if data.get("done_reason") == "length":
            # Small models sometimes loop on whitespace inside a formatted JSON answer until
            # the token limit; the visible answer is then a cut-off fragment.
            raise OllamaError("Answer cut off at the token limit (%s tokens): %s"
                              % (data.get("eval_count"), content.strip()[:120]))
        return parse_json(content), data

    def chat_text(self, model, system, user, num_ctx=4096, temperature=0.0,
                  num_predict=1024, think=None):
        """Run one chat turn and return (plain text, raw_response)."""
        self._require_requests()
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": temperature,
                "seed": 7,  # same CV + same criteria => same answer, run after run
                "num_ctx": int(num_ctx),
                "num_predict": int(num_predict),
            },
            "keep_alive": "15m",
        }
        if think is not None:
            payload["think"] = bool(think)
        r = requests.post(self._url("/api/chat"), json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise OllamaError("Ollama /api/chat returned %s: %s" % (r.status_code, r.text[:300]))
        data = r.json()
        return ((data.get("message") or {}).get("content") or "").strip(), data

    def embed(self, model, inputs, options=None):
        """Return one vector per input string.

        options={"num_gpu": 0} keeps the embedding model on the CPU so it never
        competes with the chat model for VRAM (which would force reloads)."""
        self._require_requests()
        payload = {"model": model, "input": list(inputs), "keep_alive": "15m"}
        if options:
            payload["options"] = dict(options)
        r = requests.post(self._url("/api/embed"), json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise OllamaError("Ollama /api/embed returned %s: %s" % (r.status_code, r.text[:300]))
        return r.json().get("embeddings") or []
