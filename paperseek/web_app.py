from __future__ import annotations

from pathlib import Path
import os
import json
import logging
from queue import Empty, Queue
import re
import time
from threading import Thread
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from paperseek.client import ApiException
from paperseek.config import (
    AgentConfig,
    SUPPORTED_EGRESS_MODES,
    default_api_type,
    default_base_url,
    default_model,
    preset_models,
)
from paperseek.diagnostics import run_doctor, smoke_source
from paperseek.disciplines import (
    list_discipline_fields,
    list_source_filter_options,
    normalize_source_filter_values,
    source_filter_label,
    source_filter_mode,
)
from paperseek.env_loader import load_env_file
from paperseek.config_store import load_user_config_into_env
from paperseek.history import HistoryStore, result_payload_from_search_result, safe_search_params_from_config
from paperseek.llm_client import LLMError, create_llm_client, fetch_remote_models
from paperseek.providers import ProviderError
from paperseek.search_agent import PaperSeekAgent
from paperseek.source_metadata import list_source_metadata, supported_source_ids
from paperseek_core.network import EgressRouter, EndpointPolicyError, proxy_metadata, validate_outbound_url


STATIC_DIR = Path(__file__).parent / "static"
STREAM_HEARTBEAT_SECONDS = 10.0
STREAM_HEARTBEAT_PADDING = " " * 2048

load_env_file()
load_user_config_into_env()

app = FastAPI(title="PaperSeek", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
logger = logging.getLogger("paperseek.web_app")


FIELD_LABELS = {
    "question": "Research Question",
    "data_source": "Data Source",
    "wos_api_key": "WoS API Key",
    "openalex_api_key": "OpenAlex API Key",
    "openalex_email": "OpenAlex Email",
    "crossref_email": "Crossref Email",
    "semantic_scholar_api_key": "Semantic Scholar API Key",
    "pubmed_api_key": "PubMed API Key",
    "pubmed_email": "PubMed Email",
    "pubmed_tool": "PubMed Tool",
    "serper_api_key": "Serper API Key",
    "federated_profile": "Federated Profile",
    "federated_max_per_source": "Federated Max Per Source",
    "llm_api_key": "LLM API Key",
    "llm_api_type": "LLM API Type",
    "discipline_fields": "Discipline Fields",
    "expand_citations": "Expand Citations",
    "target_min": "Min Results",
    "target_max": "Max Results",
    "max_iterations": "Iterations",
}


class SearchRequest(BaseModel):
    question: str = Field(min_length=1)
    data_source: str = "openalex"
    wos_api_key: Optional[str] = ""
    openalex_api_key: Optional[str] = ""
    openalex_email: Optional[str] = ""
    crossref_email: Optional[str] = ""
    semantic_scholar_api_key: Optional[str] = ""
    pubmed_api_key: Optional[str] = ""
    pubmed_email: Optional[str] = ""
    pubmed_tool: Optional[str] = ""
    serper_api_key: Optional[str] = ""
    federated_profile: Optional[str] = ""
    federated_max_per_source: Optional[int] = None
    llm_api_key: Optional[str] = ""
    llm_provider: str = ""
    llm_api_type: str = ""
    llm_model: Optional[str] = None
    # Known providers ignore client base URLs. Custom endpoints are accepted only after
    # server-side URL policy validation and are never persisted by the browser.
    llm_base_url: Optional[str] = None
    llm_max_tokens: Optional[int] = Field(default=None, ge=0, le=8192)
    egress_mode: Optional[str] = ""
    egress_proxy_ids: List[str] = Field(default_factory=list)
    wos_db: str = "WOS"
    search_field: Optional[str] = ""
    discipline_fields: List[str] = Field(default_factory=list)
    client_timezone: Optional[str] = ""
    client_utc_offset_minutes: Optional[int] = None
    fetch_abstracts: bool = False
    expand_citations: bool = True
    target_min: int = Field(default=5, ge=0, le=50)
    target_max: int = Field(default=50, ge=1, le=50)
    max_iterations: int = Field(default=5, ge=1, le=10)
    retrieval_pool_max: int = Field(default=3000, ge=1, le=3000)
    retrieval_pool_min: int = Field(default=5, ge=0, le=50)
    retrieval_lane_limit: int = Field(default=1000, ge=1, le=1000)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_embedding_provider: Optional[str] = ""
    retrieval_embedding_model: Optional[str] = ""
    retrieval_embedding_base_url: Optional[str] = ""  # accepted-but-ignored (SSRF hardening)
    retrieval_embedding_api_key: Optional[str] = ""
    retrieval_reranker_provider: Optional[str] = ""
    retrieval_reranker_model: Optional[str] = ""
    retrieval_reranker_base_url: Optional[str] = ""  # accepted-but-ignored (SSRF hardening)
    retrieval_reranker_api_key: Optional[str] = ""
    retrieval_crossref_enrichment: bool = False

    @field_validator("question")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("is required")
        return value

    @field_validator("llm_provider")
    @classmethod
    def clean_provider(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("llm_api_type")
    @classmethod
    def clean_api_type(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("egress_mode")
    @classmethod
    def clean_egress_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode and mode not in SUPPORTED_EGRESS_MODES:
            raise ValueError(f"must be one of {', '.join(SUPPORTED_EGRESS_MODES)}")
        return mode

    @field_validator("egress_proxy_ids", mode="before")
    @classmethod
    def clean_egress_proxy_ids(cls, value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return [item.strip().lower() for item in str(value).split(",") if item.strip()]

    @field_validator("data_source")
    @classmethod
    def supported_source(cls, value: str) -> str:
        value = (value or "openalex").strip().lower()
        if value not in supported_source_ids():
            raise ValueError(f"must be one of {', '.join(supported_source_ids())}")
        return value

    @field_validator("discipline_fields", mode="before")
    @classmethod
    def clean_discipline_fields(cls, value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @model_validator(mode="after")
    def normalize_filters_for_source(self):
        self.discipline_fields = list(normalize_source_filter_values(self.data_source, self.discipline_fields))
        return self


class DiagnosticRequest(BaseModel):
    question: Optional[str] = "machine learning"
    data_source: str = "openalex"
    wos_api_key: Optional[str] = ""
    openalex_api_key: Optional[str] = ""
    openalex_email: Optional[str] = ""
    crossref_email: Optional[str] = ""
    semantic_scholar_api_key: Optional[str] = ""
    pubmed_api_key: Optional[str] = ""
    pubmed_email: Optional[str] = ""
    pubmed_tool: Optional[str] = ""
    serper_api_key: Optional[str] = ""
    federated_profile: Optional[str] = ""
    federated_max_per_source: Optional[int] = None
    llm_api_key: Optional[str] = ""
    llm_provider: str = ""
    llm_api_type: str = ""
    llm_model: Optional[str] = None
    # Known providers ignore client base URLs. Custom endpoints are accepted only after
    # server-side URL policy validation and are never persisted by the browser.
    llm_base_url: Optional[str] = None
    llm_max_tokens: Optional[int] = Field(default=None, ge=0, le=8192)
    egress_mode: Optional[str] = ""
    egress_proxy_ids: List[str] = Field(default_factory=list)
    wos_db: str = "WOS"
    search_field: Optional[str] = ""
    discipline_fields: List[str] = Field(default_factory=list)
    client_timezone: Optional[str] = ""
    client_utc_offset_minutes: Optional[int] = None
    fetch_abstracts: bool = False
    expand_citations: bool = True
    target_min: int = Field(default=5, ge=0, le=50)
    target_max: int = Field(default=50, ge=1, le=50)
    max_iterations: int = Field(default=5, ge=1, le=10)
    retrieval_pool_max: int = Field(default=3000, ge=1, le=3000)
    retrieval_pool_min: int = Field(default=5, ge=0, le=50)
    retrieval_lane_limit: int = Field(default=1000, ge=1, le=1000)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_embedding_provider: Optional[str] = ""
    retrieval_embedding_model: Optional[str] = ""
    retrieval_embedding_base_url: Optional[str] = ""  # accepted-but-ignored (SSRF hardening)
    retrieval_embedding_api_key: Optional[str] = ""
    retrieval_reranker_provider: Optional[str] = ""
    retrieval_reranker_model: Optional[str] = ""
    retrieval_reranker_base_url: Optional[str] = ""  # accepted-but-ignored (SSRF hardening)
    retrieval_reranker_api_key: Optional[str] = ""
    retrieval_crossref_enrichment: bool = False

    @field_validator("llm_provider")
    @classmethod
    def clean_provider(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("llm_api_type")
    @classmethod
    def clean_api_type(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("egress_mode")
    @classmethod
    def clean_egress_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode and mode not in SUPPORTED_EGRESS_MODES:
            raise ValueError(f"must be one of {', '.join(SUPPORTED_EGRESS_MODES)}")
        return mode

    @field_validator("egress_proxy_ids", mode="before")
    @classmethod
    def clean_egress_proxy_ids(cls, value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return [item.strip().lower() for item in str(value).split(",") if item.strip()]

    @field_validator("data_source")
    @classmethod
    def supported_source(cls, value: str) -> str:
        value = (value or "openalex").strip().lower()
        if value not in supported_source_ids():
            raise ValueError(f"must be one of {', '.join(supported_source_ids())}")
        return value

    @field_validator("discipline_fields", mode="before")
    @classmethod
    def clean_discipline_fields(cls, value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @model_validator(mode="after")
    def normalize_filters_for_source(self):
        self.discipline_fields = list(normalize_source_filter_values(self.data_source, self.discipline_fields))
        return self


class ModelDiscoveryRequest(BaseModel):
    provider: str = "custom"
    base_url: str = Field(min_length=1)
    api_key: Optional[str] = ""
    egress_mode: Optional[str] = ""
    egress_proxy_ids: List[str] = Field(default_factory=list)

    @field_validator("provider", "egress_mode")
    @classmethod
    def clean_lower(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("egress_proxy_ids", mode="before")
    @classmethod
    def clean_proxy_ids(cls, value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return [item.strip().lower() for item in str(value).split(",") if item.strip()]


def _allow_insecure_custom_endpoints() -> bool:
    return os.environ.get("ALLOW_INSECURE_CUSTOM_ENDPOINTS", "").strip().lower() in {"1", "true", "yes", "on"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        loc = error.get("loc", [])
        field = loc[-1] if loc else ""
        label = FIELD_LABELS.get(field, str(field))
        error_type = error.get("type", "")
        msg = str(error.get("msg", "invalid value")).replace("Value error, ", "")
        if error_type == "value_error":
            if "is required" in msg:
                messages.append(f"{label} is required.")
            else:
                messages.append(f"{label}: {msg}.")
        elif error_type == "string_too_short":
            messages.append(f"{label} is required.")
        elif "greater_than_equal" in error_type or "less_than_equal" in error_type:
            messages.append(f"{label} is outside the allowed range.")
        else:
            messages.append(f"{label}: {msg}.")
    return JSONResponse(status_code=422, content={"detail": " ".join(messages) or "Invalid request."})


def _compact_text(value, max_chars: int = 700) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?i)(x-apikey|api[_-]?key|authorization)\s*[:=]\s*[^,\s}]+", r"\1: [redacted]", text)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def _friendly_api_error(exc: ApiException) -> str:
    body = _compact_text(exc.body or exc.data)
    query = _compact_text(getattr(exc, "query", ""), max_chars=450)
    iteration = getattr(exc, "iteration", None)
    context = f" Iteration: {iteration}." if iteration else ""
    query_context = f" Query: {query}" if query else ""

    if "please use https" in body.lower():
        return "WoS Starter API now requires HTTPS. The client has been updated; restart the web server and try again."
    if exc.status == 401:
        return f"WoS API authorization failed. Check that the WoS API Key is valid and has Starter API access.{context}{query_context}"
    if exc.status == 403:
        return f"WoS API access was forbidden. Check whether this key is allowed to use Web of Science Starter API.{context}{query_context}"
    if exc.status == 400:
        suffix = f" Response: {body}" if body else ""
        return f"WoS rejected the generated query. Try a simpler question or increase the iteration count.{context}{query_context}{suffix}"
    if exc.status == 512:
        suffix = f" Response: {body}" if body else " No response body was returned."
        return (
            "Clarivate returned HTTP 512, a non-standard upstream/server error from the WoS API. "
            "This is usually not caused by the local web UI. Retry once; if it repeats, simplify the query or reduce special characters."
            f"{context}{query_context}{suffix}"
        )
    suffix = f" Response: {body}" if body else ""
    return f"WoS API request failed with status {exc.status or 'unknown'}.{context}{query_context}{suffix}"


def _friendly_provider_error(exc: ProviderError) -> str:
    body = _compact_text(exc.body)
    message = _compact_text(str(exc), max_chars=700)
    query = _compact_text(exc.query, max_chars=450)
    query_context = f" Query: {query}" if query else ""
    suffix = f" Response: {body}" if body else (f" Details: {message}" if message else "")
    status = exc.status or "unknown"
    return f"{exc.source.title()} API request failed with status {status}.{query_context}{suffix}"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/sources")
def sources():
    return {"sources": list_source_metadata()}


@app.get("/api/disciplines")
def disciplines():
    sources = {}
    for source in supported_source_ids():
        sources[source] = {
            "mode": source_filter_mode(source),
            "label": source_filter_label(source),
            "options": list_source_filter_options(source),
        }
    return {"disciplines": list_discipline_fields(), "sources": sources}


@app.get("/api/config/defaults")
def config_defaults():
    config = AgentConfig.from_env()
    return {
        "data_source": config.data_source,
        "federated_profile": getattr(config, "federated_profile", "general"),
        "federated_max_per_source": getattr(config, "federated_max_per_source", 25),
        "llm_provider": config.llm_provider,
        "llm_api_type": config.llm_api_type,
        "llm_model": config.llm_model,
        # A server-managed Custom endpoint may be internal/sensitive. Never echo it.
        # Known-provider defaults are public vendor endpoints and remain useful to the UI.
        "llm_base_url": "" if config.llm_provider == "custom" else config.llm_base_url,
        "has_llm_base_url": bool(config.llm_base_url),
        "llm_max_tokens": config.llm_max_tokens,
        "target_min": config.target_min,
        "target_max": config.target_max,
        "max_iterations": config.max_iterations,
        "discipline_fields": list(getattr(config, "discipline_fields", ()) or []),
        "expand_citations": config.expand_citations,
        "fetch_abstracts": config.fetch_abstracts,
        "has_wos_api_key": bool(config.wos_api_key),
        "has_openalex_api_key": bool(config.openalex_api_key),
        "has_openalex_email": bool(config.openalex_email),
        "has_crossref_email": bool(config.crossref_email),
        "has_semantic_scholar_api_key": bool(getattr(config, "semantic_scholar_api_key", "")),
        "has_pubmed_api_key": bool(getattr(config, "pubmed_api_key", "")),
        "has_pubmed_email": bool(getattr(config, "pubmed_email", "")),
        "has_serper_api_key": bool(getattr(config, "serper_api_key", "")),
        "has_llm_api_key": bool(config.llm_api_key),
    }


def _validate_payload(payload: SearchRequest):
    if payload.target_min > payload.target_max:
        raise HTTPException(status_code=400, detail="Target minimum cannot exceed target maximum.")
    env_config = AgentConfig.from_env()
    if payload.data_source == "wos" and not ((payload.wos_api_key or "").strip() or env_config.wos_api_key):
        raise HTTPException(status_code=400, detail="WoS API Key is required for WoS searches.")


def _config_from_payload(payload: SearchRequest) -> AgentConfig:
    config = AgentConfig.from_env()
    env_provider = config.llm_provider
    payload_provider = payload.llm_provider or config.llm_provider
    provider_changed = bool(payload.llm_provider and payload_provider != env_provider)
    config.data_source = payload.data_source
    config.wos_api_key = payload.wos_api_key or config.wos_api_key
    config.openalex_api_key = payload.openalex_api_key or config.openalex_api_key
    config.openalex_email = payload.openalex_email or config.openalex_email
    config.crossref_email = payload.crossref_email or config.crossref_email
    config.semantic_scholar_api_key = payload.semantic_scholar_api_key or getattr(config, "semantic_scholar_api_key", "")
    config.pubmed_api_key = payload.pubmed_api_key or getattr(config, "pubmed_api_key", "")
    config.pubmed_email = payload.pubmed_email or getattr(config, "pubmed_email", "")
    config.pubmed_tool = payload.pubmed_tool or getattr(config, "pubmed_tool", "paperseek") or "paperseek"
    config.serper_api_key = payload.serper_api_key or getattr(config, "serper_api_key", "")
    config.federated_profile = (payload.federated_profile or "").strip() or getattr(config, "federated_profile", "general")
    config.federated_max_per_source = payload.federated_max_per_source or getattr(config, "federated_max_per_source", 25) or 25
    config.llm_api_key = payload.llm_api_key or config.llm_api_key
    config.llm_provider = payload_provider
    if payload.llm_api_type:
        config.llm_api_type = payload.llm_api_type
    elif provider_changed or not config.llm_api_type:
        config.llm_api_type = default_api_type(config.llm_provider)
    if payload.llm_model:
        config.llm_model = payload.llm_model
    elif provider_changed or not config.llm_model:
        config.llm_model = default_model(config.llm_provider)
    # Known providers remain pinned to trusted server/default endpoints. Custom
    # endpoints are session-scoped and must pass the outbound URL policy first.
    if config.llm_provider == "custom":
        if payload.llm_base_url:
            try:
                endpoint = validate_outbound_url(
                    payload.llm_base_url,
                    allow_http=_allow_insecure_custom_endpoints(),
                )
            except EndpointPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            config.llm_base_url = endpoint.url
        elif provider_changed:
            config.llm_base_url = ""
    elif provider_changed or not config.llm_base_url:
        config.llm_base_url = default_base_url(config.llm_provider, config.llm_api_type)
    if payload.llm_max_tokens is not None:
        config.llm_max_tokens = payload.llm_max_tokens
    config.wos_db = payload.wos_db or config.wos_db or "WOS"
    config.search_field = payload.search_field or config.search_field or ""
    config.discipline_fields = normalize_source_filter_values(config.data_source, payload.discipline_fields)
    config.fetch_abstracts = payload.fetch_abstracts
    config.expand_citations = payload.expand_citations
    config.target_min = payload.target_min
    config.target_max = payload.target_max
    config.max_iterations = payload.max_iterations
    config.retrieval_pool_max = payload.retrieval_pool_max
    config.retrieval_pool_min = payload.retrieval_pool_min
    config.retrieval_lane_limit = payload.retrieval_lane_limit
    config.retrieval_rrf_k = payload.retrieval_rrf_k
    config.retrieval_embedding_provider = (payload.retrieval_embedding_provider or config.retrieval_embedding_provider or "local").strip().lower()
    config.retrieval_embedding_model = payload.retrieval_embedding_model or config.retrieval_embedding_model
    if config.retrieval_embedding_provider == "custom" and payload.retrieval_embedding_base_url:
        try:
            endpoint = validate_outbound_url(
                payload.retrieval_embedding_base_url,
                allow_http=_allow_insecure_custom_endpoints(),
            )
        except EndpointPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config.retrieval_embedding_base_url = endpoint.url
    # Known retrieval providers remain server/default sourced; client URLs are ignored.
    config.retrieval_embedding_api_key = payload.retrieval_embedding_api_key or config.retrieval_embedding_api_key
    config.retrieval_reranker_provider = (payload.retrieval_reranker_provider or config.retrieval_reranker_provider or "").strip().lower()
    config.retrieval_reranker_model = payload.retrieval_reranker_model or config.retrieval_reranker_model
    if config.retrieval_reranker_provider == "custom" and payload.retrieval_reranker_base_url:
        try:
            endpoint = validate_outbound_url(
                payload.retrieval_reranker_base_url,
                allow_http=_allow_insecure_custom_endpoints(),
            )
        except EndpointPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config.retrieval_reranker_base_url = endpoint.url
    # Known retrieval providers remain server/default sourced; client URLs are ignored.
    config.retrieval_reranker_api_key = payload.retrieval_reranker_api_key or config.retrieval_reranker_api_key
    config.retrieval_crossref_enrichment = payload.retrieval_crossref_enrichment
    config.egress_mode = (payload.egress_mode or config.egress_mode or "auto").strip().lower()
    config.egress_proxy_ids = tuple(payload.egress_proxy_ids or config.egress_proxy_ids or ())
    return config


def _history_store_from_payload(payload) -> HistoryStore:
    return HistoryStore(
        timezone_name=getattr(payload, "client_timezone", "") or None,
        utc_offset_minutes=getattr(payload, "client_utc_offset_minutes", None),
    )


@app.post("/api/diagnostics")
def diagnostics(payload: DiagnosticRequest):
    config = _config_from_payload(payload)
    return run_doctor(config)


@app.post("/api/smoke")
def smoke(payload: DiagnosticRequest):
    config = _config_from_payload(payload)
    query = (payload.question or "machine learning").strip() or "machine learning"
    return smoke_source(config, query=query, limit=1)


def _response_payload(result: dict, source: str) -> dict:
    return result_payload_from_search_result(result, source)


def _record_failure(store: HistoryStore, run_id: str, message: str) -> None:
    store.fail_run(run_id, message)


@app.get("/api/history/status")
def history_status():
    return HistoryStore().status()


@app.get("/api/history")
def history_list(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    store = HistoryStore()
    return {
        **store.status(),
        "history": store.list_runs(limit=limit, offset=offset),
    }


@app.get("/api/history/{run_id}")
def history_detail(run_id: str):
    store = HistoryStore()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="History run not found.")
    return run


@app.delete("/api/history/{run_id}")
def history_delete(run_id: str):
    store = HistoryStore()
    if not store.delete_run(run_id):
        raise HTTPException(status_code=404, detail="History run not found.")
    return {"deleted": run_id}


@app.delete("/api/history")
def history_clear(confirm: bool = Query(default=False)):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to clear all local history.")
    store = HistoryStore()
    return {"deleted": store.clear()}



@app.get("/api/llm/models")
def list_llm_models(provider: str = Query(default="openai")):
    prov = (provider or "openai").lower()
    presets = preset_models(prov)
    return {
        "provider": prov,
        "models": presets or ([default_model(prov)] if default_model(prov) else []),
        "default": default_model(prov),
        "source": "preset",
        "count": len(presets),
    }


@app.post("/api/llm/models")
def discover_llm_models(payload: ModelDiscoveryRequest):
    if payload.provider != "custom":
        raise HTTPException(status_code=400, detail="Remote model discovery is available only for Custom endpoints.")
    if payload.egress_mode and payload.egress_mode not in SUPPORTED_EGRESS_MODES:
        raise HTTPException(status_code=400, detail="Unsupported egress mode.")
    try:
        endpoint = validate_outbound_url(payload.base_url, allow_http=_allow_insecure_custom_endpoints())
        router = EgressRouter(
            mode=payload.egress_mode or None,
            proxy_ids=payload.egress_proxy_ids,
            protect_url=True,
            allow_http_endpoint=_allow_insecure_custom_endpoints(),
        )
        models, error = fetch_remote_models(
            "custom",
            base_url=endpoint.url,
            api_key=payload.api_key or "",
            egress_router=router,
        )
    except (EndpointPolicyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not models:
        raise HTTPException(status_code=502, detail=error or "Model discovery returned no models.")
    return {
        "provider": "custom",
        "models": models,
        "default": models[0],
        "source": "remote",
        "count": len(models),
        "egress": router.last_route or "unknown",
        "attempted_egress": list(router.attempted_routes),
    }


@app.get("/api/network/egress")
def network_egress():
    config = AgentConfig.from_env()
    proxies = proxy_metadata()
    default_proxy_ids = list(config.egress_proxy_ids)
    if not default_proxy_ids and config.egress_mode != "direct":
        default_proxy_ids = [str(item.get("id") or "") for item in proxies if item.get("id")]
    return {
        "modes": list(SUPPORTED_EGRESS_MODES),
        "default_mode": config.egress_mode,
        "default_proxy_ids": default_proxy_ids,
        "proxies": proxies,
    }



@app.post("/api/search")
def search(payload: SearchRequest):
    _validate_payload(payload)
    config = _config_from_payload(payload)
    store = _history_store_from_payload(payload)
    run_id = store.create_run(payload.question, safe_search_params_from_config(config))

    try:
        config.validate()
        llm = create_llm_client(config)
        agent = PaperSeekAgent(config, llm)
        result = agent.search(payload.question, verbose=False)
        response = _response_payload(result, payload.data_source)
        if run_id:
            response["run_id"] = run_id
        store.complete_run(run_id, response)
    except ValueError as exc:
        _record_failure(store, run_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        _record_failure(store, run_id, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ApiException as exc:
        message = _friendly_api_error(exc)
        _record_failure(store, run_id, message)
        raise HTTPException(status_code=502, detail=message) from exc
    except ProviderError as exc:
        message = _friendly_provider_error(exc)
        _record_failure(store, run_id, message)
        raise HTTPException(status_code=502, detail=message) from exc
    except Exception as exc:
        message = "Search failed. Check the local server logs for details."
        logger.exception("Unhandled search failure.")
        _record_failure(store, run_id, message)
        raise HTTPException(status_code=500, detail=message) from exc

    return response


@app.post("/api/search/stream")
def search_stream(payload: SearchRequest):
    _validate_payload(payload)

    def line(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    def generator():
        events: Queue = Queue()
        store = _history_store_from_payload(payload)
        run_id = ""

        def send(event: dict):
            if event.get("type") != "result":
                store.record_event(run_id, event)
            events.put(event)

        def worker():
            nonlocal run_id
            try:
                config = _config_from_payload(payload)
                run_id = store.create_run(payload.question, safe_search_params_from_config(config))
                if run_id:
                    send({"type": "run", "run_id": run_id})
                send({"type": "log", "message": "Backend request accepted: POST /api/search/stream -> HTTP 200 OK."})
                send({
                    "type": "log",
                    "message": (
                        f"Runtime config: source={config.data_source}; llm_provider={config.llm_provider}; "
                        f"api_type={config.llm_api_type}; model={config.llm_model}; target={config.target_min}-{config.target_max}; "
                        f"iterations={config.max_iterations}."
                    ),
                })
                config.validate()
                send({"type": "log", "message": "Initializing LLM client."})
                llm = create_llm_client(config)
                send({"type": "log", "message": "Initializing source adapter."})
                agent = PaperSeekAgent(config, llm)
                result = agent.search(payload.question, verbose=False, event_handler=send)
                response = _response_payload(result, payload.data_source)
                if run_id:
                    response["run_id"] = run_id
                store.complete_run(run_id, response)
                send({"type": "result", "data": response})
                send({"type": "log", "message": "Run completed successfully."})
            except ValueError as exc:
                message = str(exc)
                _record_failure(store, run_id, message)
                send({"type": "error", "message": message})
            except LLMError as exc:
                message = str(exc)
                _record_failure(store, run_id, message)
                send({"type": "error", "message": message})
            except ApiException as exc:
                message = _friendly_api_error(exc)
                _record_failure(store, run_id, message)
                send({"type": "error", "message": message})
            except ProviderError as exc:
                message = _friendly_provider_error(exc)
                _record_failure(store, run_id, message)
                send({"type": "error", "message": message})
            except Exception:
                message = "Search failed. Check the local server logs for details."
                logger.exception("Unhandled streaming search failure.")
                _record_failure(store, run_id, message)
                send({"type": "error", "message": message})
            finally:
                events.put(None)

        Thread(target=worker, daemon=True).start()

        while True:
            try:
                item = events.get(timeout=STREAM_HEARTBEAT_SECONDS)
            except Empty:
                yield line({"type": "heartbeat", "ts": time.time(), "pad": STREAM_HEARTBEAT_PADDING})
                continue
            if item is None:
                break
            yield line(item)

    return StreamingResponse(
        generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def main():
    uvicorn.run("paperseek.web_app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
