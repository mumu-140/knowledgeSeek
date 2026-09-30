from __future__ import annotations

import inspect
import os
import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Iterable, Mapping, Sequence

import requests

from paperseek_core.network.url_policy import validate_outbound_url


EGRESS_MODES = ("direct", "proxy", "pool", "auto")
_PROXY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HEALTH_LOCK = Lock()
_FAILURES: dict[str, int] = {}
_COOLDOWN_UNTIL: dict[str, float] = {}


@dataclass(frozen=True)
class ProxyProfile:
    id: str
    label: str
    http_url: str
    https_url: str
    priority: int = 100

    @property
    def proxies(self) -> dict[str, str]:
        return {"http": self.http_url, "https": self.https_url}


def _int_env(environ: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(environ.get(name, "") or default))
    except (TypeError, ValueError):
        return max(minimum, default)


def _bool_env(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = str(environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _proxy_env_key(proxy_id: str, suffix: str) -> str:
    return f"PROXY_{proxy_id.upper().replace('-', '_')}_{suffix}"


def load_proxy_profiles(environ: Mapping[str, str] | None = None) -> tuple[ProxyProfile, ...]:
    env = environ or os.environ
    ids = [item.strip().lower() for item in str(env.get("PROXY_POOL", "")).split(",") if item.strip()]
    profiles = []
    for index, proxy_id in enumerate(ids):
        if not _PROXY_ID_RE.match(proxy_id):
            continue
        shared = str(env.get(_proxy_env_key(proxy_id, "URL"), "") or "").strip()
        http_url = str(env.get(_proxy_env_key(proxy_id, "HTTP_URL"), "") or shared).strip()
        https_url = str(env.get(_proxy_env_key(proxy_id, "HTTPS_URL"), "") or shared).strip()
        if not http_url and not https_url:
            continue
        http_url = http_url or https_url
        https_url = https_url or http_url
        label = str(env.get(_proxy_env_key(proxy_id, "LABEL"), "") or proxy_id).strip()
        profiles.append(ProxyProfile(proxy_id, label, http_url, https_url, index))

    if not profiles and _bool_env(env, "EGRESS_USE_ENV_PROXY", False):
        http_url = str(env.get("HTTP_PROXY", "") or env.get("http_proxy", "") or "").strip()
        https_url = str(env.get("HTTPS_PROXY", "") or env.get("https_proxy", "") or "").strip()
        if http_url or https_url:
            profiles.append(ProxyProfile("env", "Environment proxy", http_url or https_url, https_url or http_url, 0))
    return tuple(sorted(profiles, key=lambda item: item.priority))


def _cooldown_active(proxy_id: str, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    with _HEALTH_LOCK:
        return _COOLDOWN_UNTIL.get(proxy_id, 0.0) > current


def _record_success(proxy_id: str) -> None:
    with _HEALTH_LOCK:
        _FAILURES.pop(proxy_id, None)
        _COOLDOWN_UNTIL.pop(proxy_id, None)


def _record_failure(proxy_id: str, threshold: int, cooldown_seconds: int) -> None:
    with _HEALTH_LOCK:
        failures = _FAILURES.get(proxy_id, 0) + 1
        _FAILURES[proxy_id] = failures
        if failures >= threshold:
            _COOLDOWN_UNTIL[proxy_id] = time.monotonic() + cooldown_seconds
            _FAILURES[proxy_id] = 0


def proxy_metadata(environ: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    result = []
    for profile in load_proxy_profiles(environ):
        result.append({
            "id": profile.id,
            "label": profile.label,
            "status": "cooldown" if _cooldown_active(profile.id) else "ready",
        })
    return result


class EgressRouter:
    def __init__(
        self,
        *,
        mode: str | None = None,
        proxy_ids: Sequence[str] | None = None,
        environ: Mapping[str, str] | None = None,
        protect_url: bool = False,
        allow_http_endpoint: bool = False,
    ):
        self.environ = environ or os.environ
        self.mode = (mode or self.environ.get("EGRESS_MODE", "auto") or "auto").strip().lower()
        if self.mode not in EGRESS_MODES:
            raise ValueError(f"Unsupported egress mode: {self.mode}")
        requested = tuple(str(item).strip().lower() for item in (proxy_ids or ()) if str(item).strip())
        self.profiles = load_proxy_profiles(self.environ)
        known = {profile.id for profile in self.profiles}
        unknown = [item for item in requested if item not in known]
        if unknown:
            raise ValueError(f"Unknown proxy profile: {', '.join(unknown)}")
        self.proxy_ids = requested
        self.protect_url = protect_url
        self.allow_http_endpoint = allow_http_endpoint
        self.max_attempts = _int_env(self.environ, "EGRESS_MAX_ATTEMPTS", 4)
        self.connect_timeout = _int_env(self.environ, "EGRESS_CONNECT_TIMEOUT", 8)
        self.auto_direct_connect_timeout = _int_env(self.environ, "EGRESS_AUTO_DIRECT_CONNECT_TIMEOUT", 2)
        self.failure_threshold = _int_env(self.environ, "PROXY_FAILURE_THRESHOLD", 3)
        self.cooldown_seconds = _int_env(self.environ, "PROXY_COOLDOWN_SECONDS", 60)
        self.retry_429 = _bool_env(self.environ, "EGRESS_RETRY_429", False)
        self.last_route = ""
        self.attempted_routes: list[str] = []

    def _selected_profiles(self) -> list[ProxyProfile]:
        profiles = list(self.profiles)
        if self.proxy_ids:
            wanted = set(self.proxy_ids)
            profiles = [profile for profile in profiles if profile.id in wanted]
        active = [profile for profile in profiles if not _cooldown_active(profile.id)]
        return active or profiles

    def _candidates(self) -> list[ProxyProfile | None]:
        proxies = self._selected_profiles()
        if self.mode == "direct":
            return [None]
        if self.mode in {"proxy", "pool"}:
            if not proxies:
                raise requests.ConnectionError("No configured proxy is available for the selected egress mode.")
            return proxies[: self.max_attempts]
        return ([None] + proxies)[: self.max_attempts]

    def request(self, method: str, url: str, **kwargs):
        if self.protect_url:
            validate_outbound_url(url, allow_http=self.allow_http_endpoint)
        kwargs.pop("proxies", None)
        kwargs["allow_redirects"] = False
        requested_timeout = kwargs.get("timeout")
        last_error = None
        last_response = None
        self.attempted_routes = []
        for candidate in self._candidates():
            route = "direct" if candidate is None else candidate.id
            self.attempted_routes.append(route)
            connect_limit = (
                self.auto_direct_connect_timeout
                if self.mode == "auto" and candidate is None
                else self.connect_timeout
            )
            if isinstance(requested_timeout, (int, float)) and requested_timeout > 0:
                kwargs["timeout"] = (min(float(requested_timeout), float(connect_limit)), float(requested_timeout))
            elif isinstance(requested_timeout, (tuple, list)) and len(requested_timeout) == 2:
                connect_timeout, read_timeout = requested_timeout
                try:
                    connect_timeout = min(float(connect_timeout), float(connect_limit))
                except (TypeError, ValueError):
                    connect_timeout = float(connect_limit)
                kwargs["timeout"] = (connect_timeout, read_timeout)
            if candidate is not None:
                kwargs["proxies"] = candidate.proxies
            else:
                # Explicit empty proxy entries prevent requests from inheriting
                # HTTP_PROXY/HTTPS_PROXY/ALL_PROXY for the Direct route.
                kwargs["proxies"] = {"http": "", "https": "", "all": ""}
            try:
                caller = getattr(requests, method.lower())
                call_kwargs = dict(kwargs)
                try:
                    signature = inspect.signature(caller)
                    accepts_kwargs = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in signature.parameters.values()
                    )
                    if not accepts_kwargs:
                        allowed = set(signature.parameters)
                        call_kwargs = {key: value for key, value in call_kwargs.items() if key in allowed}
                except (TypeError, ValueError):
                    pass
                response = caller(url, **call_kwargs)
                last_response = response
                retryable = response.status_code in {502, 503, 504} or (self.retry_429 and response.status_code == 429)
                if candidate is not None:
                    if retryable:
                        _record_failure(candidate.id, self.failure_threshold, self.cooldown_seconds)
                    else:
                        _record_success(candidate.id)
                if not retryable:
                    self.last_route = route
                    return response
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ProxyError) as exc:
                last_error = exc
                if candidate is not None:
                    _record_failure(candidate.id, self.failure_threshold, self.cooldown_seconds)
        if last_response is not None:
            self.last_route = self.attempted_routes[-1] if self.attempted_routes else ""
            return last_response
        if last_error is not None:
            raise last_error
        raise requests.ConnectionError("No egress route was available.")
