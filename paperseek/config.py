from __future__ import annotations

import os
from dataclasses import dataclass, field

from paperseek.disciplines import normalize_source_filter_values
from paperseek.source_metadata import supported_source_ids

SUPPORTED_EGRESS_MODES = ("direct", "proxy", "pool", "auto")


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, value)


@dataclass
class AgentConfig:
    data_source: str = "openalex"
    wos_api_key: str = ""
    openalex_api_key: str = ""
    openalex_email: str = ""
    crossref_email: str = ""
    semantic_scholar_api_key: str = ""
    pubmed_api_key: str = ""
    pubmed_email: str = ""
    pubmed_tool: str = "paperseek"
    serper_api_key: str = ""
    federated_profile: str = "general"
    federated_max_per_source: int = 25
    llm_api_key: str = ""
    llm_provider: str = "openai"
    llm_api_type: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_max_tokens: int = 2048
    ranking_llm_timeout_seconds: int = 60
    wos_db: str = "WOS"
    search_field: str = ""
    discipline_fields: tuple[str, ...] = field(default_factory=tuple)
    fetch_abstracts: bool = False
    expand_citations: bool = True
    citation_seed_count: int = 30
    citation_per_seed: int = 4
    citation_max_records: int = 160
    citation_depth: int = 2
    ranking_batch_size: int = 8
    ranking_concurrency: int = 16
    ranking_candidate_limit: int = 256
    target_min: int = 5
    target_max: int = 50
    max_iterations: int = 5
    retrieval_pool_max: int = 3000
    retrieval_pool_min: int = 5
    retrieval_lane_limit: int = 1000
    retrieval_rrf_k: int = 60
    retrieval_embedding_provider: str = "local"
    retrieval_embedding_model: str = "qwen3-embedding:8b,bge-large-zh:latest"
    retrieval_embedding_base_url: str = ""
    retrieval_embedding_api_key: str = ""
    retrieval_reranker_provider: str = ""
    retrieval_reranker_model: str = "qwen3-reranker:8b"
    retrieval_reranker_base_url: str = ""
    retrieval_reranker_api_key: str = ""
    retrieval_crossref_enrichment: bool = False
    egress_mode: str = "auto"
    egress_proxy_ids: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        api_type = os.environ.get("LLM_API_TYPE", "") or default_api_type(provider)
        return cls(
            wos_api_key=os.environ.get("WOS_API_KEY", ""),
            data_source=os.environ.get("DATA_SOURCE", "openalex").lower(),
            openalex_api_key=os.environ.get("OPENALEX_API_KEY", ""),
            openalex_email=os.environ.get("OPENALEX_EMAIL", ""),
            crossref_email=os.environ.get("CROSSREF_EMAIL", ""),
            semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""),
            pubmed_api_key=os.environ.get("PUBMED_API_KEY", ""),
            pubmed_email=os.environ.get("PUBMED_EMAIL", ""),
            pubmed_tool=os.environ.get("PUBMED_TOOL", "paperseek"),
            serper_api_key=os.environ.get("SERPER_API_KEYS", "") or os.environ.get("SERPER_API_KEY", ""),
            federated_profile=os.environ.get("FEDERATED_PROFILE", "general"),
            federated_max_per_source=_int_env("FEDERATED_MAX_PER_SOURCE", 25, minimum=1),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_provider=provider,
            llm_api_type=api_type,
            llm_model=os.environ.get("LLM_MODEL") or default_model(provider),
            llm_base_url=os.environ.get("LLM_BASE_URL") or default_base_url(provider, api_type),
            llm_max_tokens=_int_env("LLM_MAX_TOKENS", 2048),
            ranking_llm_timeout_seconds=_int_env("RANKING_LLM_TIMEOUT_SECONDS", 60, minimum=10),
            wos_db=os.environ.get("WOS_DB", "WOS"),
            search_field=os.environ.get("SEARCH_FIELD", ""),
            discipline_fields=os.environ.get("DISCIPLINE_FIELDS", ""),
            fetch_abstracts=os.environ.get("FETCH_ABSTRACTS", "").lower() in ("1", "true", "yes"),
            expand_citations=os.environ.get("EXPAND_CITATIONS", "true").lower() not in ("0", "false", "no"),
            citation_seed_count=int(os.environ.get("CITATION_SEED_COUNT", "30")),
            citation_per_seed=int(os.environ.get("CITATION_PER_SEED", "4")),
            citation_max_records=int(os.environ.get("CITATION_MAX_RECORDS", "160")),
            citation_depth=int(os.environ.get("CITATION_DEPTH", "2")),
            ranking_batch_size=int(os.environ.get("RANKING_BATCH_SIZE", "8")),
            ranking_concurrency=int(os.environ.get("RANKING_CONCURRENCY", "16")),
            ranking_candidate_limit=int(os.environ.get("RANKING_CANDIDATE_LIMIT") or "256"),
            target_min=int(os.environ.get("TARGET_MIN", "5")),
            target_max=int(os.environ.get("TARGET_MAX", "50")),
            max_iterations=int(os.environ.get("MAX_ITERATIONS", "5")),
            retrieval_pool_max=int(os.environ.get("RETRIEVAL_POOL_MAX", "3000")),
            retrieval_pool_min=int(os.environ.get("RETRIEVAL_POOL_MIN", "5")),
            retrieval_lane_limit=int(os.environ.get("RETRIEVAL_LANE_LIMIT", "1000")),
            retrieval_rrf_k=int(os.environ.get("RETRIEVAL_RRF_K", "60")),
            retrieval_embedding_provider=os.environ.get("RETRIEVAL_EMBEDDING_PROVIDER", "local").strip().lower() or "local",
            retrieval_embedding_model=os.environ.get("RETRIEVAL_EMBEDDING_MODEL", "qwen3-embedding:8b,bge-large-zh:latest"),
            retrieval_embedding_base_url=os.environ.get("RETRIEVAL_EMBEDDING_BASE_URL", ""),
            retrieval_embedding_api_key=os.environ.get("RETRIEVAL_EMBEDDING_API_KEY", ""),
            retrieval_reranker_provider=os.environ.get("RETRIEVAL_RERANKER_PROVIDER", "").strip().lower(),
            retrieval_reranker_model=os.environ.get("RETRIEVAL_RERANKER_MODEL", "qwen3-reranker:8b"),
            retrieval_reranker_base_url=os.environ.get("RETRIEVAL_RERANKER_BASE_URL", ""),
            retrieval_reranker_api_key=os.environ.get("RETRIEVAL_RERANKER_API_KEY", ""),
            retrieval_crossref_enrichment=os.environ.get("RETRIEVAL_CROSSREF_ENRICHMENT", "").lower() in ("1", "true", "yes"),
            egress_mode=(os.environ.get("EGRESS_MODE", "auto") or "auto").strip().lower(),
            egress_proxy_ids=tuple(item.strip().lower() for item in os.environ.get("EGRESS_PROXY_IDS", "").split(",") if item.strip()),
        )

    def validate(self):
        missing = []
        self.data_source = (self.data_source or "openalex").lower()
        if self.data_source not in supported_source_ids():
            raise ValueError(f"DATA_SOURCE must be one of {', '.join(supported_source_ids())}, got '{self.data_source}'")
        self.discipline_fields = normalize_source_filter_values(self.data_source, self.discipline_fields)
        if self.data_source == "wos" and not self.wos_api_key:
            missing.append("WOS_API_KEY")
        if self.data_source == "googlescholar" and not self.serper_api_key:
            missing.append("SERPER_API_KEY")
        self.llm_provider = (self.llm_provider or "openai").lower()
        self.llm_api_type = (self.llm_api_type or default_api_type(self.llm_provider)).lower()
        if not self.llm_api_key and self.llm_provider != "ollama":
            missing.append("LLM_API_KEY")
        if self.llm_provider == "custom" and not (self.llm_base_url or "").strip():
            missing.append("LLM_BASE_URL")
        self.egress_mode = (self.egress_mode or "auto").strip().lower()
        if self.egress_mode not in SUPPORTED_EGRESS_MODES:
            raise ValueError(f"EGRESS_MODE must be one of {', '.join(SUPPORTED_EGRESS_MODES)}, got '{self.egress_mode}'")
        if self.llm_provider not in SUPPORTED_LLM_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {', '.join(SUPPORTED_LLM_PROVIDERS)}, got '{self.llm_provider}'")
        if self.llm_api_type not in SUPPORTED_LLM_API_TYPES:
            raise ValueError(f"LLM_API_TYPE must be one of {', '.join(SUPPORTED_LLM_API_TYPES)}, got '{self.llm_api_type}'")
        if missing:
            raise ValueError(
                f"Missing required configuration: {', '.join(missing)}. "
                f"Set them via environment variables or CLI flags."
            )


SUPPORTED_LLM_PROVIDERS = (
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "cstcloud",
    "dashscope",
    "moonshot",
    "zhipu",
    "siliconflow",
    "openrouter",
    "nvidia",
    "volcengine",
    "hunyuan",
    "qianfan",
    "modelscope",
    "ollama",
    "custom",
)

SUPPORTED_LLM_API_TYPES = ("openai_chat", "openai_responses", "anthropic_messages")


def default_api_type(provider: str) -> str:
    provider = (provider or "openai").lower()
    if provider == "anthropic":
        return "anthropic_messages"
    if provider == "openai":
        return "openai_responses"
    return "openai_chat"


def default_model(provider: str) -> str:
    provider = (provider or "openai").lower()
    models = {
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-3.5-flash",
        "deepseek": "deepseek-v4-flash",
        "cstcloud": "deepseek-v4-flash",
        "dashscope": "qwen3.6-plus",
        "moonshot": "kimi-k2.6",
        "zhipu": "glm-5.1",
        "siliconflow": "deepseek-ai/DeepSeek-V4-Flash",
        "openrouter": "openai/gpt-5.4-mini",
        "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "volcengine": "doubao-seed-2-0-mini-260428",
        "hunyuan": "hunyuan-turbos-latest",
        "qianfan": "ernie-5.0",
        "modelscope": "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "ollama": "qwen3:8b",
        "custom": "",
    }
    return models.get(provider, "")


def default_base_url(provider: str, api_type: str = "") -> str:
    provider = (provider or "openai").lower()
    api_type = (api_type or default_api_type(provider)).lower()
    urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "google": "https://generativelanguage.googleapis.com/v1beta/openai",
        "deepseek": "https://api.deepseek.com",
        "cstcloud": "https://uni-api.cstcloud.cn/v1",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "moonshot": "https://api.moonshot.ai/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
        "hunyuan": "https://tokenhub.tencentmaas.com/v1",
        "qianfan": "https://qianfan.baidubce.com/v2",
        "modelscope": "https://api-inference.modelscope.cn/v1",
        "ollama": "http://127.0.0.1:11434/v1",
        "custom": "",
    }
    if api_type == "anthropic_messages" and provider == "anthropic":
        return "https://api.anthropic.com"
    return urls.get(provider, "")

PROVIDER_PRESET_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5.4-mini", "gpt-4o", "gpt-4o-mini", "o1", "o1-mini", "o3-mini", "gpt-4-turbo"],
    "anthropic": ["claude-sonnet-4-6", "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    "google": ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    "deepseek": ["deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
    "siliconflow": [
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "THUDM/glm-4-9b-chat",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
    ],
    "openrouter": [
        "openai/gpt-5.4-mini",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-r1",
        "anthropic/claude-3.5-sonnet",
        "google/gemini-2.5-flash",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "dashscope": ["qwen3.6-plus", "qwen-max", "qwen-plus", "qwen-turbo", "qwen2.5-72b-instruct", "deepseek-v3", "deepseek-r1"],
    "zhipu": ["glm-5.1", "glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
    "moonshot": ["kimi-k2.6", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "ollama": [
        "qwen3:8b",
        "qwen2.5:7b",
        "qwen2.5:14b",
        "qwen2.5:32b",
        "llama3.1:8b",
        "llama3.3:70b",
        "deepseek-r1:8b",
        "deepseek-r1:14b",
        "mistral:latest",
    ],
    "modelscope": [
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "Qwen/Qwen2.5-72B-Instruct",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
    ],
    "cstcloud": ["deepseek-v4-flash", "deepseek-v3", "deepseek-r1", "qwen2.5-72b-instruct"],
    "volcengine": ["doubao-seed-2-0-mini-260428", "doubao-pro-32k", "doubao-lite-32k", "deepseek-v3", "deepseek-r1"],
    "hunyuan": ["hunyuan-turbos-latest", "hunyuan-pro", "hunyuan-standard", "hunyuan-lite"],
    "qianfan": ["ernie-5.0", "ernie-4.0-turbo-8k", "ernie-3.5-8k", "deepseek-v3", "deepseek-r1"],
    "nvidia": ["nvidia/llama-3.3-nemotron-super-49b-v1.5", "meta/llama-3.3-70b-instruct", "deepseek-ai/deepseek-r1"],
}


def preset_models(provider: str) -> list[str]:
    provider = (provider or "openai").lower()
    return list(PROVIDER_PRESET_MODELS.get(provider, []))
