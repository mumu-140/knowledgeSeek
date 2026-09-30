from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict
from itertools import count
import os
import re
import requests
from threading import Lock
import time

from paperseek_core.network import EgressRouter, redact_network_text


class LLMError(Exception):
    pass


def _llm_timeout_seconds() -> int:
    try:
        return max(30, int(os.environ.get("LLM_TIMEOUT_SECONDS", "180")))
    except (TypeError, ValueError):
        return 180


def _llm_max_tokens() -> int:
    raw = os.environ.get("LLM_MAX_TOKENS")
    try:
        return max(0, int(raw) if raw not in (None, "") else 2048)
    except (TypeError, ValueError):
        return 2048


DEFAULT_LLM_TIMEOUT_SECONDS = _llm_timeout_seconds()
DEFAULT_LLM_MAX_TOKENS = _llm_max_tokens()
_API_KEY_COUNTER_LOCK = Lock()
_API_KEY_COUNTERS = {}


def _coerce_timeout_seconds(value, default=DEFAULT_LLM_TIMEOUT_SECONDS) -> int:
    try:
        return max(1, int(value if value not in (None, "") else default))
    except (TypeError, ValueError):
        return default


def _split_api_keys(api_key: str) -> List[str]:
    keys = [item.strip() for item in re.split(r"[\s,;]+", api_key or "") if item.strip()]
    return list(dict.fromkeys(keys))


def _next_api_key(keys: List[str]) -> str:
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]
    pool_key = "\0".join(keys)
    with _API_KEY_COUNTER_LOCK:
        counter = _API_KEY_COUNTERS.setdefault(pool_key, count())
        index = next(counter)
    return keys[index % len(keys)]


def _api_key_attempts(api_key: str, max_attempts: int = 3) -> List[str]:
    keys = _split_api_keys(api_key)
    if not keys:
        return [""]
    attempts = min(max(1, int(max_attempts or 1)), len(keys))
    return [_next_api_key(keys) for _ in range(attempts)]


def _retryable_external_status(status_code: int) -> bool:
    return status_code in (401, 403, 408, 409, 425, 429) or status_code >= 500


class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
        ...


def _auth_headers(api_key: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _egress_request(router, method: str, url: str, **kwargs):
    if router is None:
        caller = getattr(requests, method.lower())
        return caller(url, **kwargs)
    return router.request(method, url, **kwargs)


def _egress_label(router) -> str:
    return getattr(router, "last_route", "") if router is not None else ""


MODELSCOPE_QUOTA_HEADERS = {
    "user_limit": "modelscope-ratelimit-requests-limit",
    "user_remaining": "modelscope-ratelimit-requests-remaining",
    "model_limit": "modelscope-ratelimit-model-requests-limit",
    "model_remaining": "modelscope-ratelimit-model-requests-remaining",
}


def extract_modelscope_quota(headers) -> Dict[str, str]:
    quota = {}
    for key, header_name in MODELSCOPE_QUOTA_HEADERS.items():
        value = headers.get(header_name) if headers else None
        if value is not None and str(value).strip():
            quota[key] = str(value).strip()
    return quota


def format_modelscope_quota(quota: Dict[str, str]) -> str:
    if not quota:
        return ""
    user_remaining = quota.get("user_remaining", "?")
    user_limit = quota.get("user_limit", "?")
    model_remaining = quota.get("model_remaining", "?")
    model_limit = quota.get("model_limit", "?")
    return f"user remaining {user_remaining}/{user_limit}; model remaining {model_remaining}/{model_limit}"


class OpenAIChatClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "",
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS,
        egress_router=None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
        self.max_tokens = max(0, int(max_tokens or 0))
        self.timeout_seconds = _coerce_timeout_seconds(timeout_seconds)
        self.last_response_info = {}
        self.egress_router = egress_router

    def chat(self, messages, temperature=0.3):
        url = f"{self.base_url}/chat/completions"
        started = time.perf_counter()
        kimi_coding_endpoint = self.model == "kimi-for-coding" and "api.kimi.com/coding" in self.base_url
        if kimi_coding_endpoint:
            temperature = 0.6
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        if kimi_coding_endpoint:
            body["thinking"] = {"type": "disabled"}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        attempts = _api_key_attempts(self.api_key)
        last_error = None
        for attempt_index, api_key in enumerate(attempts, 1):
            try:
                resp = _egress_request(
                    self.egress_router,
                    "POST",
                    url,
                    headers=_auth_headers(api_key),
                    json=body,
                    timeout=self.timeout_seconds,
                )
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": resp.status_code,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "quota": extract_modelscope_quota(resp.headers),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                if resp.status_code < 200 or resp.status_code >= 300:
                    detail = resp.text
                    quota_text = format_modelscope_quota((self.last_response_info or {}).get("quota", {}))
                    quota_suffix = f" Quota: {quota_text}." if quota_text else ""
                    last_error = LLMError(f"LLM API error ({resp.status_code}): {detail}{quota_suffix}")
                    if attempt_index < len(attempts) and _retryable_external_status(resp.status_code):
                        continue
                    raise last_error
                try:
                    return _extract_openai_chat_content(resp.json())
                except (KeyError, IndexError, TypeError, ValueError) as e:
                    raise LLMError(f"LLM Chat Completions response did not contain message content: {e}") from e
            except requests.Timeout:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "timeout",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM request timed out after {self.timeout_seconds}s")
                if attempt_index < len(attempts):
                    continue
                raise last_error
            except requests.RequestException as e:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "request_error",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM network error: {redact_network_text(e)}")
                if attempt_index < len(attempts):
                    continue
                raise last_error from e
        raise last_error or LLMError("LLM request failed.")


class OpenAIResponsesClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "",
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS,
        egress_router=None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
        self.max_tokens = max(0, int(max_tokens or 0))
        self.timeout_seconds = _coerce_timeout_seconds(timeout_seconds)
        self.last_response_info = {}
        self.egress_router = egress_router

    def chat(self, messages, temperature=0.3):
        url = f"{self.base_url}/responses"
        started = time.perf_counter()
        instructions = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        input_messages = [m for m in messages if m.get("role") != "system"]
        body = {
            "model": self.model,
            "input": input_messages,
            "temperature": temperature,
        }
        if self.max_tokens:
            body["max_output_tokens"] = self.max_tokens
        if instructions:
            body["instructions"] = instructions
        attempts = _api_key_attempts(self.api_key)
        last_error = None
        for attempt_index, api_key in enumerate(attempts, 1):
            try:
                resp = _egress_request(
                    self.egress_router,
                    "POST",
                    url,
                    headers=_auth_headers(api_key),
                    json=body,
                    timeout=self.timeout_seconds,
                )
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": resp.status_code,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "quota": extract_modelscope_quota(resp.headers),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                if resp.status_code < 200 or resp.status_code >= 300:
                    detail = resp.text
                    quota_text = format_modelscope_quota((self.last_response_info or {}).get("quota", {}))
                    quota_suffix = f" Quota: {quota_text}." if quota_text else ""
                    last_error = LLMError(f"LLM API error ({resp.status_code}): {detail}{quota_suffix}")
                    if attempt_index < len(attempts) and _retryable_external_status(resp.status_code):
                        continue
                    raise last_error
                return self._extract_text(resp.json())
            except requests.Timeout:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "timeout",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM request timed out after {self.timeout_seconds}s")
                if attempt_index < len(attempts):
                    continue
                raise last_error
            except requests.RequestException as e:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "request_error",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM network error: {redact_network_text(e)}")
                if attempt_index < len(attempts):
                    continue
                raise last_error from e
        raise last_error or LLMError("LLM request failed.")

    @staticmethod
    def _extract_text(payload) -> str:
        if payload.get("output_text"):
            return payload["output_text"]
        texts = []
        for item in payload.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") in ("output_text", "text") and content.get("text"):
                    texts.append(content["text"])
        if texts:
            return "\n".join(texts)
        raise LLMError("LLM Responses API returned no output text")


class AnthropicClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-haiku-20240307",
        base_url: str = "",
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS,
        egress_router=None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else "https://api.anthropic.com"
        self.max_tokens = max(1, int(max_tokens or DEFAULT_LLM_MAX_TOKENS))
        self.timeout_seconds = _coerce_timeout_seconds(timeout_seconds)
        self.last_response_info = {}
        self.egress_router = egress_router

    def chat(self, messages, temperature=0.3):
        system_msg = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_messages.append(m)

        body = {
            "model": self.model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if system_msg:
            body["system"] = system_msg

        url = f"{self.base_url}/v1/messages"
        started = time.perf_counter()
        attempts = _api_key_attempts(self.api_key)
        last_error = None
        for attempt_index, api_key in enumerate(attempts, 1):
            try:
                resp = _egress_request(
                    self.egress_router,
                    "POST",
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self.timeout_seconds,
                )
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": resp.status_code,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "quota": extract_modelscope_quota(resp.headers),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                if resp.status_code < 200 or resp.status_code >= 300:
                    detail = resp.text
                    quota_text = format_modelscope_quota((self.last_response_info or {}).get("quota", {}))
                    quota_suffix = f" Quota: {quota_text}." if quota_text else ""
                    last_error = LLMError(f"LLM API error ({resp.status_code}): {detail}{quota_suffix}")
                    if attempt_index < len(attempts) and _retryable_external_status(resp.status_code):
                        continue
                    raise last_error
                return _extract_anthropic_messages_text(resp.json())
            except requests.Timeout:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "timeout",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM request timed out after {self.timeout_seconds}s")
                if attempt_index < len(attempts):
                    continue
                raise last_error
            except requests.RequestException as e:
                self.last_response_info = {
                    "method": "POST",
                    "url": url,
                    "status": "request_error",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt_index,
                    "egress": _egress_label(self.egress_router),
                }
                last_error = LLMError(f"LLM network error: {redact_network_text(e)}")
                if attempt_index < len(attempts):
                    continue
                raise last_error from e
        raise last_error or LLMError("LLM request failed.")


def _extract_anthropic_messages_text(payload) -> str:
    texts = []
    for item in payload.get("content", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text"):
            texts.append(str(item["text"]))
    if texts:
        return "\n".join(texts)
    for item in payload.get("content", []) or []:
        if isinstance(item, dict) and item.get("text"):
            texts.append(str(item["text"]))
    if texts:
        return "\n".join(texts)
    raise LLMError("LLM Anthropic Messages response did not contain text content")


def create_llm_client(config) -> LLMClient:
    api_type = (getattr(config, "llm_api_type", "") or "").lower()
    max_tokens = getattr(config, "llm_max_tokens", DEFAULT_LLM_MAX_TOKENS)
    timeout_seconds = getattr(config, "llm_timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS)
    provider = (getattr(config, "llm_provider", "") or "").lower()
    allow_http_endpoint = os.environ.get("ALLOW_INSECURE_CUSTOM_ENDPOINTS", "").strip().lower() in {"1", "true", "yes", "on"}
    router = EgressRouter(
        mode=getattr(config, "egress_mode", "auto") or "auto",
        proxy_ids=getattr(config, "egress_proxy_ids", ()) or (),
        protect_url=provider == "custom",
        allow_http_endpoint=allow_http_endpoint,
    )
    common = {
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "egress_router": router,
    }
    if api_type == "anthropic_messages":
        return AnthropicClient(config.llm_api_key, config.llm_model, config.llm_base_url, **common)
    if api_type == "openai_responses":
        return OpenAIResponsesClient(config.llm_api_key, config.llm_model, config.llm_base_url, **common)
    return OpenAIChatClient(config.llm_api_key, config.llm_model, config.llm_base_url, **common)


def _extract_openai_chat_content(payload) -> str:
    choice = (payload.get("choices") or [])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if content:
        return content
    reasoning = message.get("reasoning_content")
    if reasoning:
        return reasoning
    delta = choice.get("delta") or {}
    if delta.get("content"):
        return delta["content"]
    raise KeyError("choices[0].message.content")

def fetch_remote_models(
    provider: str,
    base_url: str = "",
    api_key: str = "",
    timeout: float = 8.0,
    egress_router=None,
) -> tuple[list[str], str]:
    """Fetch available models from an LLM provider endpoint.

    Returns:
        (models, error_message): list of model ids and error description (if any)
    """
    provider = (provider or "openai").lower()
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return [], "No base_url provided"

    urls_to_try = []
    headers = {"Content-Type": "application/json"}

    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        if base_url.endswith("/v1"):
            urls_to_try.append(f"{base_url}/models")
        else:
            urls_to_try.append(f"{base_url}/v1/models")
    elif provider == "ollama" or ":11434" in base_url:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if base_url.endswith("/v1"):
            clean_base = base_url[:-3].rstrip("/")
            urls_to_try.extend([f"{base_url}/models", f"{clean_base}/api/tags"])
        else:
            urls_to_try.extend([f"{base_url}/api/tags", f"{base_url}/v1/models", f"{base_url}/models"])
    else:
        # Standard OpenAI-compatible
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if base_url.endswith("/v1"):
            urls_to_try.append(f"{base_url}/models")
        else:
            urls_to_try.extend([f"{base_url}/models", f"{base_url}/v1/models"])

    last_error = ""
    for url in urls_to_try:
        try:
            resp = _egress_request(egress_router, "GET", url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                raw_items = []
                if isinstance(data, list):
                    raw_items = data
                elif isinstance(data, dict):
                    raw_items = data.get("data") or data.get("models") or []

                models = []
                for item in raw_items:
                    if isinstance(item, dict):
                        mid = item.get("id") or item.get("name") or item.get("model")
                    elif isinstance(item, str):
                        mid = item
                    else:
                        mid = None
                    if mid and isinstance(mid, str) and mid.strip():
                        models.append(mid.strip())

                if models:
                    seen = set()
                    unique_models = []
                    for m in models:
                        if m not in seen:
                            seen.add(m)
                            unique_models.append(m)
                    return unique_models, ""
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.Timeout:
            last_error = f"Connection timeout to {url}"
        except requests.ConnectionError:
            last_error = f"Connection refused to {url}"
        except Exception as e:
            last_error = redact_network_text(e)

    return [], last_error
