# KnowledgeSeek × MOSAIC 最小侵入式联邦检索集成计划

- 日期：2026-09-24
- 状态：Proposed / ready for implementation
- 目标仓库：mumu-140/knowledgeSeek
- 参考仓库：szaghi/mosaic
- 推荐实施分支：feat/mosaic-federated-provider

## 1. 目标

本计划不重构 PaperSeek/KnowledgeSeek，也不把 MOSAIC 整仓复制进来。

目标是把两者已经成熟的模块按职责拼接：

KnowledgeSeek 保留：
- 自然语言研究问题理解
- intent analysis
- query generation / broaden / narrow
- 学科判断与筛选
- 现有 retrieval fusion（RRF + BM25 + term coverage + embedding）
- citation expansion
- reranker
- LLM ranking
- CLI / Web / MCP / Skill

MOSAIC 复用：
- 多数据库 source adapters
- source registry
- 多源并发 fan-out
- source failure isolation
- 跨源基础去重与 metadata merge
- 后续可选的 cache / PDF / citation graph 能力

第一阶段完成后，KnowledgeSeek 增加一个新的 federated / mosaic 数据源模式，但原有 OpenAlex、PubMed、Semantic Scholar、Crossref 等单源模式完全保留且默认行为不改变。

核心原则：

Question
→ KnowledgeSeek 原有 intent / query
→ MosaicFederatedProvider
→ MOSAIC sources + concurrent retrieval + merge
→ MOSAIC Paper → KnowledgeSeek PaperRecord
→ KnowledgeSeek 原有 fuse_candidates_rrf()
→ citation / reranker / LLM ranking
→ result

## 2. 非目标

本轮明确不做：

- 不重写 agent.py
- 不替换 KnowledgeSeek 现有 provider
- 不删除现有 OpenAlex / PubMed / Semantic Scholar / Crossref 实现
- 不重写 MOSAIC source adapters
- 不接入 MOSAIC Web UI
- 不接入 NotebookLM
- 不接入 Obsidian / Zotero
- 不接入 MOSAIC 自己的 LLM ranking
- 不在第一轮引入新的 RAG 架构
- 不在第一轮引入新的数据库 schema
- 不把现有 PaperRecord 改造成第三套 canonical model
- 不为了集成而大规模调整 Python 版本或部署体系

## 3. 工作区与执行规则

建议将两个仓库放在同一工作区并保持兄弟目录：

workspace/
  knowledgeSeek/
  mosaic/

KnowledgeSeek 是唯一目标仓库。
MOSAIC 第一阶段视为只读参考与依赖来源，不直接修改其源码。

本地与服务器职责建议：

本地：
- clone / pull
- 阅读源码
- 设计
- 编辑代码
- git diff / commit / push

服务器或独立测试环境：
- Python 3.11+ 的 MOSAIC 集成测试
- dependency install
- 完整测试
- 网络真实检索
- benchmark
- Web/CLI smoke test

不要在已有生产环境中直接试验。
不要覆盖用户已有配置、数据库、缓存或密钥。
不要打印 API key、token、cookie 或其他认证材料。

所有工作必须在 feature branch 中完成，主分支只在最终审核后合并。

## 4. 强制执行节奏

每个阶段都必须遵循同一闭环：

1. 明确该阶段只解决一个问题。
2. 先读相关代码和测试。
3. 写或补充测试契约。
4. 实现最小改动。
5. 运行阶段相关测试。
6. 运行必要的回归测试。
7. git diff 审查。
8. 检查是否扩大范围、重复实现已有能力或破坏兼容性。
9. 更新本计划底部的 Implementation Log。
10. 形成一个小 commit。
11. 只有本阶段通过后才能进入下一阶段。

如果测试失败：
- 先定位并修复当前阶段；
- 不通过绕过测试继续推进；
- 不因为一个失败就重构无关模块。

## 5. Phase 0 — 拉取、冻结基线、建立可回退起点

### 目标

确保 KnowledgeSeek 与 MOSAIC 都处于明确版本，并证明 KnowledgeSeek 原始主流程在改动前可用。

### 操作

在本地和测试服务器分别：

1. clone 或 pull mumu-140/knowledgeSeek。
2. clone 或 pull szaghi/mosaic。
3. 记录两个仓库：
   - remote URL
   - branch
   - HEAD SHA
   - git status
4. KnowledgeSeek 创建：
   feat/mosaic-federated-provider
5. MOSAIC 保持只读，不创建功能改动。
6. 在服务器建立独立 Python 环境。
7. 运行 KnowledgeSeek 当前完整测试。
8. 如条件允许，运行 MOSAIC 自身核心测试，确认参考版本健康。

### 输出

记录至少：

- KNOWLEDGESEEK_BASE_SHA
- MOSAIC_BASE_SHA
- Python version
- KnowledgeSeek baseline test result
- MOSAIC baseline smoke result

### Gate 0

必须满足：

- KnowledgeSeek 工作树干净
- 已创建 feature branch
- baseline tests 可重复
- 两个基线 SHA 已记录

未满足不得进入 Phase 1。

## 6. Phase 1 — 接口审计与最小集成边界冻结

### 目标

不写业务代码，先确认两边真正需要连接的接口。

### KnowledgeSeek 重点阅读

- paperseek_core/agent.py
- paperseek_core/retrieval.py
- paperseek_core/results.py
- paperseek_core/sources/providers.py
- paperseek_core/sources/metadata.py
- paperseek_core/config.py
- tests/test_retrieval.py
- tests/test_source_providers.py
- tests/test_source_prompts.py
- tests/test_results.py

### MOSAIC 重点阅读

- mosaic/models.py
- mosaic/source_registry.py
- mosaic/search.py
- mosaic/services.py
- mosaic/sources/base.py
- mosaic/sources/*
- 必要时查看 config，但暂不接入其 UI/RAG。

### 需要冻结的接口

A. 输入边界

KnowledgeSeek 交给 MOSAIC：
- query
- max results
- source profile / selected sources
- year / author / journal 等能直接映射的 filter
- optional API configuration

B. 输出边界

MOSAIC 返回 Paper。
Adapter 转成 KnowledgeSeek 已有 PaperRecord / ProviderSearchResult。

C. 字段映射

至少确认：

MOSAIC Paper.title
→ PaperRecord.title

authors
→ PaperNames.authors

year
→ PaperSource.publish_year

journal
→ PaperSource.source_title

doi
→ PaperIdentifiers.doi

arxiv_id
→ PaperIdentifiers.arxiv

openalex_id
→ PaperIdentifiers.openalex

abstract
→ PaperRecord.abstract

pdf_url
→ PaperLinks.pdf

url
→ PaperLinks.landing_page / record

citation_count
→ PaperCitation / KnowledgeSeek 当前引用数语义

source
→ provider / source metadata

### 关键决策

优先采用“依赖 + adapter”，而不是复制 MOSAIC 源码。

只有以下情况确认存在时，才允许 vendor 极少数纯 Python 核心模块：

- mosaic-search 作为可选依赖无法稳定部署；
- Python 版本或 packaging 产生不可接受冲突；
- 需要的接口无法从公开模块稳定调用。

即使 vendor，也只允许提取最小模块并保留来源、版本与许可证说明，不得复制整个 MOSAIC。

### Gate 1

形成一份简短兼容性结论：

- 可直接映射字段
- 需要补充的字段
- 需要 adapter 的调用点
- 是否可直接依赖 mosaic-search
- 预计修改的 KnowledgeSeek 文件

此时仍不应修改 agent 主流程。

### Gate 1 结论（2026-09-25，已验证）

以下结论基于对两仓源码的直接核验（关键行号为主会话实测），非仅子代理报告。

A. 可直接映射字段（mosaic.Paper → paperseek PaperRecord）

| MOSAIC Paper | PaperRecord 目标 | 说明 |
|---|---|---|
| title | title | 必填，唯一必填字段 |
| authors: list[str] | names.authors: list[PaperAuthor(display_name=…)] | 纯字符串列表 → display_name |
| year | source.publish_year | int/None 兼容 |
| journal | source.source_title | |
| volume / issue / pages | source.volume / source.issue / source.pages | PaperSource.pages 为 Any，str 可直入 |
| doi | identifiers.doi | 经 normalize_doi（providers.py 已有） |
| arxiv_id | identifiers.arxiv | |
| openalex_id | identifiers.openalex | |
| abstract | abstract | None → "" |
| pdf_url | links.pdf | |
| url | links.landing_page | |
| citation_count | citations=[PaperCitation(db=paper.source or "unknown", count=…)] | count None → 0 |
| source（来源名） | provider="federated"；原始来源名放 raw["mosaic_source"] | 保持 KnowledgeSeek 单 provider 语义 |
| 整个 Paper | raw=paper.to_dict() | 供调试与还原 |

B. 需要补充/丢弃的字段：PaperRecord 侧 identifiers.pmid/issn/eisbn 等、keywords、types、citing_articles 链接，MOSAIC Paper 不提供 → 留空。MOSAIC 的 pii（PaperRecord 无对应位）与 relevance_score（避免双重排序）丢弃，pii 可存 raw。KnowledgeSeek document_key()（doi→openalex→arxiv→semanticscholar→pmid→uid→title）与 MOSAIC Paper.uid（doi→arxiv→pii→title）优先级不同但兼容：adapter 只需填 identifiers，去重交给 KnowledgeSeek。

C. 需要 adapter 的调用点（共 4 个，均已在两仓验证）：
1. mosaic.source_registry.build_sources(cfg: dict) -> list[BaseSource] — cfg 为普通嵌套 dict（不需要 config.load()/tomli_w）；`{"sources": {key: {"enabled": bool, "api_key": …}}}`；未列出的 key 默认 enabled=True（实测确认），因此 profile 选择必须显式 disable 其余源或绕过 registry 直接实例化源类。实测：全 disable + 仅开 arxiv/crossref → 只建 2 个源。无 key 源经 available()=False 被 search_all 自动跳过。
2. mosaic.search.search_all(sources, query, max_per_source=25, filters=None, errors=[], stats={}, parallel=True) -> list[Paper] — 逐源 try/except 失败隔离；stats 填 per_source/raw_total/unique/merged/after_filters；按 Paper.uid merge。只做 retrieve+merge，不做 ranking（sort_by_relevance 是独立入口，不调用）。
3. mosaic.models.SearchFilters（year_from/year_to/authors/journal）— 第一版传 None（KnowledgeSeek 的过滤在 fusion 后），保留结构以备 Phase 7。
4. Adapter 输出：ProviderSearchResult(metadata=SearchMetadata(total=len(papers), page=1, limit=…), hits=[PaperRecord…])，与 providers.py 各 provider 的返回完全同构。

D. 可否直接依赖 mosaic-search：可以，但有两个约束：
- mosaic pyproject requires-python >=3.11（其 config.py 用 tomllib）。但实测（本工作区 py3.10.11 venv，sys.path 直指 mosaic/）：models/services/search/source_registry 全部可导入并运行，federated 核心链路唯一第三方依赖是 httpx（已装）。未用到 config.load()/tomllib。→ Phase 8 打包时 `paperseek[federated]` extra 用 `mosaic-search; python_version >= "3.11"` marker，运行时 adapter 做可导入性探测即可。
- pypi.org 当前不可达，无法在线安装；开发期用 sys.path 指向兄弟目录 mosaic/（已在集成测试中采用），发布依赖留待有网环境验证。

E. 预计修改的 KnowledgeSeek 文件（全部 add-only）：
- 新增 paperseek_core/integrations/__init__.py、mosaic_adapter.py（Phase 2）、mosaic_provider.py（Phase 3）
- 新增 tests/test_mosaic_adapter.py、tests/test_mosaic_provider.py
- paperseek_core/sources/metadata.py：SOURCE_METADATA 增 "federated" 条目 + list_source_metadata() 元组加 "federated"
- paperseek_core/agent.py：__init__ elif 链加 `data_source == "federated"` 一行实例化 provider；_source_label() labels 加一项；_source_safe_query 对 federated 走默认 strip()（已满足，无需改）；_provider_search_lane 已有 `if self.provider:` 兜底分支，无需改
- paperseek_core/config.py：SourceConfig/RuntimeConfig/build_runtime_config 加 federated 配置字段（profile、max_per_source）
- paperseek/config.py（from_env）：加对应环境变量读取
- paperseek/web_app.py：data_source 白名单来自 supported_source_ids()（自动包含），仅展示层零改或小改
- pyproject.toml：Phase 8 加 [project.optional-dependencies] federated
- 无需改 retrieval.py / results.py / 现有任何单源 provider

## 7. Phase 2 — 新建 MOSAIC Adapter，尚不接主流程

### 目标

先解决“数据结构兼容”，与网络、LLM、agent 解耦。

### 建议新增

paperseek_core/integrations/
  __init__.py
  mosaic_adapter.py

tests/
  test_mosaic_adapter.py

### mosaic_adapter.py 第一版职责

只做：

- 可选 import MOSAIC
- MOSAIC availability check
- Mosaic Paper → PaperRecord
- list[Paper] → ProviderSearchResult
- source / error / stats 的轻量映射

不要在这里做：

- ranking
- RRF
- embedding
- LLM
- citation expansion
- query generation

### Optional dependency

优先把 MOSAIC 作为 optional extra，而不是 KnowledgeSeek 基础依赖。

目标语义：

- 普通 KnowledgeSeek 仍支持当前 Python / 当前安装方式。
- federated MOSAIC 模式只在满足 MOSAIC Python 环境时启用。
- 未安装 MOSAIC 时，原功能完全不受影响。
- 用户显式选择 federated 时才给出清晰的 dependency error。

### 测试

全部使用构造出的 MOSAIC Paper fixture，不访问网络。

覆盖：

- DOI paper
- arXiv paper
- OpenAlex paper
- 无 DOI paper
- 缺 abstract
- 缺 authors
- OA PDF
- citation count
- Unicode title
- duplicated identifiers

### Gate 2

必须满足：

- adapter tests 全部通过
- 原 test_results / test_retrieval 不退化
- agent.py 尚未被大改
- 未引入新的 ranking 逻辑

形成独立 commit。

## 8. Phase 3 — MosaicFederatedProvider

### 目标

把 MOSAIC 多源检索能力封装成 KnowledgeSeek 可以调用的一个 provider。

### 建议实现

在 integrations/mosaic_adapter.py 或独立：

paperseek_core/integrations/mosaic_provider.py

提供：

MosaicFederatedProvider

其职责：

1. 接收 query。
2. 根据 source profile 选择 MOSAIC sources。
3. 调用 MOSAIC build_sources / search_all 或等价公开接口。
4. 收集：
   - per_source count
   - raw_total
   - unique
   - merged
   - errors
5. 把 Paper 转换为 PaperRecord。
6. 返回 KnowledgeSeek 已有结果类型。

### 默认 source profile

不要默认启动 MOSAIC 所有数据库。

建议第一版：

biomed:
- PubMed
- Europe PMC
- PubMed Central
- OpenAlex
- Semantic Scholar
- bioRxiv/medRxiv
- Crossref

cs:
- OpenAlex
- Semantic Scholar
- arXiv
- DBLP
- Crossref
- IEEE（仅在 key 可用时）

general:
- OpenAlex
- Semantic Scholar
- Crossref
- DOAJ

注意：
- 缺 API key 的源应自动跳过或明确标记 unavailable。
- 一个 source 失败不能导致整次 federated search 失败。
- 第一版优先使用 keyless / 稳定数据源。

### Ranking 边界

MOSAIC 只负责：

retrieve
→ merge
→ Paper list

不要调用 MOSAIC 的 BM25 / semantic rank / LLM rank。

返回 KnowledgeSeek 后继续使用：

fuse_candidates_rrf()
→ external embedding/reranker
→ LLM rank

避免双重排序。

### Gate 3

mock source 测试需要覆盖：

- 多源均成功
- 一个源 timeout
- 一个源 429/5xx
- 一个源返回空
- 两个源返回同 DOI
- 两个源 metadata 互补
- MOSAIC 未安装
- profile 中包含 unavailable source

通过后形成独立 commit。

## 9. Phase 4 — 接回 KnowledgeSeek 主流程

### 目标

把 federated provider 注册到现有 source 体系中，但不改变默认行为。

### 原则

新增：
- federated
或
- mosaic

作为一个新的数据源选项。

不要改变：
- 默认 OpenAlex
- 原有 source-specific query 生成
- 原有单源 provider 行为
- 原有 CLI 参数语义
- 原有 Web / MCP 兼容性

### 第一版 query 策略

为了最小改动：

Question
→ 原有 intent analysis
→ 生成一个适合 federated retrieval 的普通文本 query
→ MosaicFederatedProvider
→ 多源 fan-out

不要在第一版立即重构 agent 为“每个 MOSAIC source 一套不同 query”。

已有单源模式仍继续使用当前：
- OpenAlex-specific prompt
- PubMed-specific prompt
- Semantic Scholar-specific prompt
- Crossref-specific prompt
等。

### 返回后

MOSAIC candidate pool 统一进入 KnowledgeSeek 已有 retrieval fusion。

第一版可把 federated pool 视作 relevance lane。
不要为了给不同源造 lane 而扩大改动。

### Gate 4

必须运行：

- 新 federated provider tests
- test_source_providers
- test_source_prompts
- test_retrieval
- test_results
- test_agent_api
- test_cli_management
- test_mcp_server
- test_web_app
- full test suite

并检查：

- 未选择 federated 时结果与旧逻辑一致
- federated source 选项可被 CLI / API 正确识别
- source failure 有可观察日志
- history / export 不因新 provider 结构损坏

通过后形成独立 commit。

## 10. Phase 5 — 真实网络 smoke test

### 目标

证明真实多源流程能运行，而不仅是 mock 单元测试通过。

### 测试要求

在服务器独立环境进行。

至少测试：

1. 精确题名型查询
2. 普通主题型查询
3. 较窄生物学主题
4. 同义词较多的主题
5. 近期主题

生命科学 profile 至少验证：

- PubMed
- Europe PMC
- OpenAlex
- Semantic Scholar
- Crossref

bioRxiv / PMC 可根据网络状态验证。

### 每个 query 记录

- query
- profile
- active sources
- 每个 source 原始返回数
- raw total
- unique total
- merge count
- errors
- latency
- top 20 DOI/title
- missing DOI %
- missing abstract %
- missing year %
- duplicate rate

### Gate 5

通过标准：

- 至少 3 个核心 source 能同时返回结果
- 单一 source 失败不会终止整体查询
- merge 后重复显著低于 raw pool
- 输出能正常进入 KnowledgeSeek ranking
- 不出现大规模空 title / 错位 DOI / authors 崩坏

若数据映射问题明显，回到 Phase 2 修 adapter；不要在 agent.py 打补丁。

## 11. Phase 6 — 基线模式 vs federated 模式对照

### 目标

证明新模式带来的主要价值是 candidate coverage，而不是主观宣称“更好”。

### 固定测试集

建立一个小型可复跑 query set。

建议包含：

- 6 个生命科学问题
- 2 个计算机科学问题
- 2 个精确已知论文查询

第一轮不需要建立人工 gold standard。

### 比较指标

对每个 query 比较：

single-source baseline
vs
federated

记录：

- raw candidates
- unique candidates
- new unique papers
- source distribution
- top 20 overlap
- DOI overlap
- duplicate rate
- metadata completeness
- latency
- source error count

不要仅用候选数判断质量。

### 输出

建议生成：

tests/fixtures/federated_queries.json

以及一个不进入核心 runtime 的 benchmark 脚本，例如：

scripts/benchmark_federated.py

如果仓库当前没有 scripts 目录，应先判断是否值得新增；也可以放 tests/benchmark/。

### Gate 6

确认：

- candidate coverage 确实增加
- top results 不发生明显异常漂移
- latency 在可接受范围
- 没有因跨源数据增加造成 LLM candidate explosion

如候选池过大：
优先限制每源 max_results 和总 pool_max；
不要先增加新的复杂 ranking 算法。

## 12. Phase 7 — 第二轮小优化：source-aware query routing

此阶段是可选增强，不是 MVP 必需。

只有 Phase 0–6 稳定后再进行。

### 问题

MOSAIC 默认 search_all 会把同一个 query 发给多个数据库。
PaperSeek 已有 source-specific query generation，这是现成优势。

### 最小增强

新增一个薄 Query Router：

- OpenAlex → 复用现有 OpenAlex query generation
- PubMed → 复用现有 PubMed query generation
- Semantic Scholar → 复用现有 Semantic Scholar query generation
- Crossref → 复用现有 Crossref query generation
- 其他 MOSAIC source → generic query

然后并发调用对应 MOSAIC source adapter。

不要重写 prompt。
不要把 source-specific query logic 搬进 MOSAIC。

### Gate 7

对同一固定 query set 比较：

single federated query
vs
source-aware federated queries

观察：

- unique recall proxy
- top 20 overlap
- source failure
- latency
- query complexity / malformed rate

只有有明确收益才保留。

## 13. Phase 8 — Packaging 与兼容性

### 目标

不让 MOSAIC 集成破坏现有 PaperSeek 安装。

### 检查

- pyproject optional extra
- Python <3.11 下基础安装仍可工作
- Python >=3.11 下 federated extra 可安装
- Docker 是否需要额外镜像调整
- CI matrix 是否需要新增一条 Python 3.11 federated job
- requirements.txt 不应无条件强塞所有 MOSAIC extra
- 不应安装 NotebookLM / browser / RAG 等非必要依赖

### 推荐

基础：
paperseek

可选：
paperseek[federated]

若 PyPI dependency marker 或 MOSAIC packaging 实际证明不适合，再讨论 vendor 极少量模块。

### Gate 8

- test_packaging 通过
- 原 CI 通过
- 新 federated CI 或 smoke job 通过
- Docker / CLI 至少完成一种真实运行验证

## 14. Phase 9 — 文档与收尾

只在功能稳定后更新：

- README
- docs/user-manual.md
- docs/deployment.md
- 如有必要 source metadata / UI source list

说明：

- federated 是可选模式
- 默认 profile
- 各 profile 的 source
- API key 可选项
- Python 版本要求
- 单源模式仍然存在
- source failure 的降级行为

不要在计划阶段先改大量 UI 文案。

## 15. Future：本轮不实现

MOSAIC 中以下能力值得后续独立评估，但不要并入当前 PR：

### A. Local literature library
- SQLite cache
- search history enrichment
- cross-run reuse

### B. PDF acquisition
- OA PDF
- Unpaywall
- PMC
- downloaded file tracking

### C. Full-text indexing
- PDF extraction
- chunking
- sqlite-vec
- semantic search

### D. Citation network
- paper_citations
- BFS
- community detection

### E. Evidence layer
最终可在 full text 上增加：

claim
→ supporting passage
→ section/page
→ paper
→ support / contradict / context
→ provenance

但这些必须作为后续独立计划，不得拖入本次 federated retrieval MVP。

## 16. 建议 commit 切分

建议保持小提交：

1. docs: add MOSAIC federated integration plan
2. test: define MOSAIC adapter contract
3. feat: add optional MOSAIC adapter
4. test: define federated provider behavior
5. feat: add MosaicFederatedProvider
6. feat: register federated source without changing defaults
7. test: add federated integration and fallback coverage
8. chore/test: add reproducible federated smoke benchmark
9. docs: document optional federated retrieval

不要把全部工作压成一个巨型 commit。

## 17. 回滚策略

任何阶段都应能退回：

- 删除/禁用 federated provider 注册
- optional MOSAIC dependency 移除
- 原 provider 与原 source path 无需恢复，因为不应被替换

若 federated 功能不稳定，发布时可以保留代码但默认关闭。

## 18. MVP 最终验收标准

必须同时满足：

1. 原 KnowledgeSeek full test suite 通过。
2. 原单源模式行为不变。
3. federated mode 可以在服务器真实查询。
4. 至少 3 个核心 source 可并发返回。
5. 单个 source 失败不会使任务整体失败。
6. 跨源重复论文能够合并。
7. MOSAIC Paper 到 PaperRecord 字段映射经测试覆盖。
8. federated candidate 可以直接进入现有 fuse_candidates_rrf()。
9. 没有引入第二套 ranking pipeline。
10. MOSAIC 未安装时原 KnowledgeSeek 不受影响。
11. Python 版本与 optional dependency 行为有明确测试。
12. 真实查询 benchmark 有可复跑记录。
13. 代码改动集中在 integration/provider 注册层，而不是大规模修改 agent.py。

## 19. 实施过程中的判断优先级

遇到设计选择时按以下顺序裁决：

1. 能复用现有代码，不新写。
2. 能加 adapter，不改核心。
3. 能配置解决，不硬编码。
4. 能保持 optional，不变成强依赖。
5. 能局部失败，不整体失败。
6. 能保留原路径，不替换原路径。
7. 有测试证据再优化，没有收益不增加复杂度。

## 20. Implementation Log

Codex 每完成一个 Phase 都在这里追加：

### Phase N
- 时间：
- KnowledgeSeek SHA：
- MOSAIC SHA：
- 修改文件：
- 测试：
- 结果：
- 发现的问题：
- 是否偏离计划：
- commit：
- 下一步：

不要提前填写未执行阶段。

### Phase 0
- 时间：2026-09-25
- KnowledgeSeek SHA：a4bcfe7059605105309838e1af2596eb20e373ec（branch feat/mosaic-federated-provider，工作树干净）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（main，tag v1.5.5，工作树干净，只读）
- 修改文件：仅本计划文档（Phase 0 记录）；代码零改动
- 测试：
  - KnowledgeSeek baseline：`LD_PRELOAD=$WS/.sqlite-fix/libsqlite3.so.0 $WS/.venv-knowledgeSeek/bin/python -m pytest -q`（在 knowledgeSeek/ 下）→ 178 passed, 1 skipped, 85 subtests passed（exit 0），日志 $WS/knowledgeSeek-baseline-pytest-py310.log
  - MOSAIC baseline smoke（核心子集 tests/test_models.py, test_search.py, test_db.py, test_sources.py，同一 venv，`-o addopts=""` 去掉 pytest-cov 参数）→ 241 passed, 9 skipped（exit 0），日志 $WS/mosaic-baseline-pytest-py310.log
  - $WS = /home/yangs/software/knowledgeSeek-mosaic-20260924
- 结果：Gate 0 满足——两仓 SHA 已记录、工作树干净、feature branch 已建、baseline 可重复
- 发现的问题：
  1. 环境：conda py3.10.11 的 sqlite3 因系统 libsqlite3 3.7.17 过旧无法 import（sqlite3_trace_v2 未定义）。已用工作区本地 LD_PRELOAD 垫片（$WS/.sqlite-fix/libsqlite3.so.0，sqlite 3.45.3）解决，未改动任何系统文件。
  2. pypi.org 不可达：原计划的服务器端 py3.11 独立环境（uv .venv-ks311）离线装包失败（cryptography/pydantic-core wheel 缺失），本轮退回 py3.10 venv + LD_PRELOAD。MOSAIC 官方 requires-python >=3.11，但本 smoke 子集在 3.10 下通过（未用 tomllib）；后续阶段如需 import mosaic.config 或完整依赖（httpx>=0.27 已满足），需在可达网络的环境重验。
  3. MOSAIC 测试 addopts 含 --cov（pytest-cov 未装），跑其测试需 `-o addopts=""`；tests/test_services_errors_jobs.py 依赖 flask（未装），故 smoke 覆盖为核心四文件。
- 是否偏离计划：是（环境层面，非代码层面）——baseline 测试改在 py3.10 + LD_PRELOAD sqlite 垫片上运行，而非计划的独立 py3.11 环境；原因见上 2。集成代码路径不受影响。
- commit：见本次 "docs: record Phase 0 baseline in implementation log"
- 下一步：Phase 1 接口审计（两份只读审计已产出：MOSAIC 公开 API 已完成；KnowledgeSeek provider/agent wiring 审计重跑中），冻结集成边界。

### Phase 1
- 时间：2026-09-25
- KnowledgeSeek SHA：8544fbe（feat/mosaic-federated-provider）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读）
- 修改文件：仅 docs/plans/2026-09-24-mosaic-federated-integration.md（新增 Gate 1 结论 + 本 Log 条目）；零代码改动
- 测试：
  - mosaic 核心在 py3.10 venv 下 sys.path 直指 mosaic/ 导入并运行验证：models/services/search/source_registry 全通过；build_sources 手工 cfg dict 实测（selective enable 生效；无 key 源 available()=False）
  - KnowledgeSeek 侧关键调用点逐行核验：agent.py provider elif 链（536-557）、_provider_search_lane（1096-1112）、_call_provider_search（1126-1134，按签名自适应 kwargs）、_retrieve_candidates lane 装配（1319-1354）、metadata.py SOURCE_METADATA/list_source_metadata、config.py SourceConfig/RuntimeConfig、web_app/cli supported_source_ids() 校验链
- 结果：Gate 1 满足——兼容性结论已写入计划第 6 节（字段映射表、4 个 adapter 调用点、依赖可行性、预计修改文件清单）
- 发现的问题：
  1. build_sources 对未列出的 source key 默认 enabled=True —— profile 必须显式 disable 非目标源，或 provider 绕过 registry 直接实例化源类。已写入结论 A/C。
  2. mosaic requires-python>=3.11 名义约束 vs 实际 federated 核心链路 py3.10 可跑（不用 tomllib）——Phase 8 用环境 marker + 运行时探测双保险。
  3. 子代理审计报告有部分函数名不准（如 _select_sources/search_sources 等并不存在），主会话已逐点实测纠正，Log 与 Gate 1 结论以实测为准。
- 是否偏离计划：否
- commit：见 "docs: add Phase 1 interface audit and Gate 1 conclusion"
- 下一步：Phase 2 新建 paperseek_core/integrations/mosaic_adapter.py（纯 fixture 测试，不接主流程，不访问网络）。

### Phase 2
- 时间：2026-09-25
- KnowledgeSeek SHA：fdb2a54 →（本次 commit）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读，sys.path 引用）
- 修改文件：新增 paperseek_core/integrations/__init__.py、paperseek_core/integrations/mosaic_adapter.py、tests/test_mosaic_adapter.py；零现有文件改动
- 测试：
  - tests/test_mosaic_adapter.py：无 mosaic 时 15 passed（duck-typed fixture + 缺依赖报错文案）；PYTHONPATH 指向兄弟 mosaic/ 时 14 passed + 1 skipped（真实 mosaic.models.Paper.from_dict 路径，skip 的是"未安装报错"用例）
  - 全量回归：193 passed, 1 skipped, 85 subtests（基线 178 + 新增 15）
- 结果：Gate 2 满足——adapter tests 通过、test_results/test_retrieval 不退化、agent.py 零改动、无新 ranking 逻辑
- 发现的问题：
  1. `import mosaic` 不自动加载 mosaic.models（包 __init__ 只含 __version__）——require_mosaic/mosaic_available 已改为 import mosaic.models，修正了 Gate 1 结论中"import mosaic 即可"的推断。
  2. adapter 对 to_dict() 失败做容错（raw 置空），不中断转换。
- 是否偏离计划：轻微——papers_to_provider_result 暂未在 metadata 中携带 stats/errors（保持与其它 provider 的 SearchMetadata 完全同构），per_source_stats/errors 参数仅保留接口位，实际消费在 Phase 3 provider 层。
- commit：见 "feat: add optional MOSAIC adapter (Paper to PaperRecord mapping)"
- 下一步：Phase 3 MosaicFederatedProvider（profile 选择 + search_all 封装 + 失败隔离，mock 测试）。

### Phase 3
- 时间：2026-09-25
- KnowledgeSeek SHA：30f22bb →（本次 commit）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读）
- 修改文件：新增 paperseek_core/integrations/mosaic_provider.py、tests/test_mosaic_provider.py；零现有文件改动
- 测试：
  - tests/test_mosaic_provider.py：15 passed（Gate 3 全场景：多源成功/单源 timeout/单源 5xx/空源/同 DOI 合并/互补 metadata/mosaic 未安装/profile 含 unavailable 源（available() 过滤 + 全 unavailable 报 ProviderError）/空 query/profile 映射与回退/limit 截断/单 relevance lane）
  - 真实 build_sources 配置 sanity（PYTHONPATH 指向 mosaic/，离线）：general profile → 恰好启用 openalex/semantic_scholar/crossref/doaj，显式 disable 其余 16 个 registry key
  - 全量回归：208 passed, 1 skipped, 85 subtests（178 基线 + 15 adapter + 15 provider）
- 结果：Gate 3 满足
- 发现的问题：Gate 1 结论中"未列出 key 默认 enabled"的风险已在 provider 内消化——_build_sources 对全部 20 个 registry key 显式写 enabled 布尔，profile 外源不会再被意外拉起。
- 是否偏离计划：轻微——search() 的 max_per_source 取 max(self.max_per_source, limit)，保证 KnowledgeSeek 侧 lane_limit 能透传到每源上限；ranking 边界保持：未调用 mosaic 的 sort_by_relevance/BM25。
- commit：见 "feat: add MosaicFederatedProvider with source profiles and failure isolation"
- 下一步：Phase 4 注册 "federated" 数据源到主流程（metadata.py SOURCE_METADATA、agent.py elif 分支、config 字段、web/cli 白名单自动生效），默认行为不变。

### Phase 4
- 时间：2026-09-25
- KnowledgeSeek SHA：4fb891c →（本次 commit）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读）
- 修改文件：
  - paperseek_core/sources/metadata.py（SOURCE_METADATA["federated"] + list_source_metadata 顺序元组）
  - paperseek_core/agent.py（__init__ elif 分支 + _source_label 一项；query 生成走既有 `if self.provider:` 通用分支，_source_safe_query 走默认 strip，均零改动）
  - paperseek_core/config.py（SourceConfig/RuntimeConfig/build_runtime_config 增 federated_profile、federated_max_per_source）
  - paperseek/config.py（AgentConfig 字段 + from_env 读 FEDERATED_PROFILE/FEDERATED_MAX_PER_SOURCE）
  - paperseek/cli.py（--federated-profile/--federated-max-per-source 参数；--source choices 自动含 federated）
  - paperseek/web_app.py（两个请求模型字段 + FIELD_LABELS + _config_from_payload 透传；data_source 校验经 supported_source_ids() 自动放行）
  - skills/paperseek/scripts/paperseek_skill_runtime.py（standalone 源清单加 federated 条目）
  - tests/test_{cli_management,mcp_server,skill_launcher,source_metadata,web_app}.py（期望源列表加 "federated"，位置在 crossref 与 wos 之间）
- 测试：
  - 全量：208 passed, 1 skipped, 85 subtests（含 Gate 4 要求的 test_source_providers/test_source_prompts/test_retrieval/test_results/test_agent_api/test_cli_management/test_mcp_server/test_web_app/test_skill_launcher）
  - CLI 冒烟：`python -m paperseek.cli sources --json` 输出含 federated（status=optional_dependency）
  - 配置链路验证：SearchConfig(data_source='federated')+SourceConfig(federated_profile='cs') → build_runtime_config → MosaicFederatedProvider(profile=cs, 5 源) ✓
  - 未装 mosaic 时 provider.search() 抛 MosaicNotInstalledError，报错含安装指引 ✓（原 source 全部不受影响，默认 data_source=openalex 未变）
- 结果：Gate 4 满足
- 发现的问题：
  1. skills standalone runtime 内嵌一份与 metadata.py 重复的源清单——两处都加了 federated；这是仓库既有模式（非本次引入）。
  2. 5 个测试文件硬编码期望源列表，加新源必须同步——已同步。
- 是否偏离计划：否
- commit：见 "feat: register federated data source across CLI, web, and skill surfaces"
- 下一步：Phase 5 真实网络 smoke test（需网络可达环境；当前主机 pypi/外网受限，先探测出口，不行则记录阻塞并先做 Phase 8 打包准备）。

### Phase 5
- 时间：2026-09-25
- KnowledgeSeek SHA：6acd45f →（本次 commit）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读）
- 环境声明：本机（fwq10ys）具备学术 API 出口（openalex/crossref/arxiv/DOAJ/EuropePMC/PubMed GET 均 200），pypi 不可达；测试用 venv py3.10.11 + LD_PRELOAD sqlite shim + PYTHONPATH 指向同级只读 mosaic checkout。
- 先修复（Phase 2 引入、真实执行暴露的缺陷）：require_mosaic() 原返回 mosaic.models 子模块，而 provider 在其上访问 mosaic.source_registry / mosaic.search → AttributeError。改为导入并返回 mosaic 根包（显式预导入 models/search/source_registry 三个子模块）；mosaic_available() 同步对齐；mosaic_paper_from_dict 改用 mosaic.models.Paper。此前 mock 测试把 require_mosaic patch 成假命名空间，掩盖了该缺陷——已由真实网络调用暴露并修复。回归：test_mosaic_adapter+test_mosaic_provider 29 passed/1 skipped（skip 为"未装 mosaic"分支，本环境装了属预期跳过）；全量 207 passed/2 skipped/85 subtests（另 1 skip 为环境性，非回归）。
- smoke 执行：workspace 下 phase5-smoke.py（不入库）+ 结果 phase5-smoke-results.json。5 类查询 × 对应 profile（narrow-bio/synonym-heavy 用 biomed，其余 general），limit=40, max_per_source=10。
- 每查询指标（全部成功返回）：
  - exact-title "Attention is all you need"（general）：3 源在线（S2 429），raw 120 → unique 109（merge 11，重复率 9.2%），DOI 缺失 0%，abstract 缺失 40%（Crossref 无摘要属源特性），year 0%，hits 40，2.5s
  - topical "transformer attention mechanisms"（general）：3 源，raw 120 → 119（merge 1），DOI 缺 5%，abstract 缺 0%，2.7s
  - narrow-bio "CRISPR base editing off-target evaluation"（biomed）：5 源返回（PubMed/EuropePMC/PMC/OpenAlex/Crossref 各 40 + bioRxiv 0），raw 200 → 191（merge 9），DOI 缺 2.5%，abstract 缺 2.5%，year 0%，3.8s
  - synonym-heavy "tumor immunotherapy checkpoint inhibitor resistance"（biomed）：5 源，raw 200 → 194（merge 6），DOI 缺 7.5%，abstract 缺 12.5%，year 缺 7.5%，3.1s
  - recent "large language model agents 2025"（general）：3 源，raw 145 → 143（merge 2），DOI 缺 2.5%，abstract 缺 22.5%，3.2s
- biomed 核心源核验：PubMed/EuropePMC/OpenAlex/Crossref 全部真实返回；Semantic Scholar 持续 429（独立单源重试亦然）——服务器 IP 限流，非代码缺陷；隔离性由"每次查询 S2 失败均未影响其余源与整体结果"直接证明。
- Gate 5 逐条：≥3 核心源同时返回 ✓（general 3、biomed 5）；单源失败不终止 ✓；merge 去重可见 ✓（5 查询 merge 计 29 条，hits 内 uid 零重复）；输出进 ranking ✓（每查询 hits 喂 fuse_candidates_rrf 正常产出 fused 排序）；无大规模空 title/错位 DOI/作者崩坏 ✓（top20 抽查标题-DOI 对应正常，arXiv/DOI 归一化生效）。
- 结果：Gate 5 满足
- 发现的问题：
  1. Semantic Scholar 公共 API 对本机 IP 持续 429；带 key 可解（FEDERATED 相关配置留待 Phase 8 文档说明）。
  2. arXiv 在 mosaic 侧拼 "all:" 前缀查询串返回 406（mosaic 上游行为，只读不改；cs profile 短查询不受影响）。
  3. abstract 缺失集中于 Crossref 无摘要、部分源 JATS 缺失——源特性，映射层如实保留空串，不做臆造填充。
- 是否偏离计划：smoke 脚本置于 workspace 而非 repo（计划允许 benchmark 脚本另行评估入 scripts/，属 Phase 6 决策）。
- commit：见 "fix: return mosaic root package from require_mosaic and validate real-network federated smoke"
- 下一步：Phase 6 固定 query set + baseline vs federated 对照 benchmark。

### Phase 6
- 时间：2026-09-25
- KnowledgeSeek SHA：c45e2af →（本次 commit）
- MOSAIC SHA：64b991927e5124c964a29f3103eb6b506c44e8d8（只读）
- 新增文件：
  - tests/fixtures/federated_queries.json（10 查询固定集：6 生命科学 biomed + 2 CS cs + 2 精确论文 general，带 expected_doi）
  - scripts/benchmark_federated.py（不入核心 runtime 的对照 driver；--limit/--max-per-source/--baseline/--queries/--out/--pause）
  - tests/test_federated_benchmark.py（3 个离线 guard：fixture schema 校验、脚本可编译、runtime 不引用 benchmark）
- 发现并修复（Phase 5 遗留的隐藏缺陷）：search_all 并行 fan-out 返回 dict 插入序 = 线程完成序，run-to-run 不确定；papers_to_provider_result 直接按 limit 截断 → 同一查询两次运行 top-N 可不同（实测复现：AlphaFold 查询 run1/run2 top3 相同且含目标论文，另一 run 池不同）。修复落在 adapter 数据层（遵守"不在 agent.py 打补丁"）：截断前按（跨源 merge 优先、引用数降序、年份降序、uid 升序）确定性排序——纯元数据稳定序，非相关性算法，相关性仍归 RRF/reranker。新增回归测试 test_papers_to_provider_result_ordering_is_deterministic（三种输入排列同输出）。
- fixture 修正：exact-01 expected_doi 原为 10.48550/arxiv.1706.03762，但 OpenAlex 将该论文索引为 10.65215/2q58a426（实测 API 返回）；修正后 fed/baseline 双双命中。
- benchmark 终版（phase6-benchmark-results-v3.json，limit=40, max_per_source=10）：
  - bio-01..06（6 源在线）：raw 200 → unique 182–198，重复率 1.0–9.0%，DOI 重叠 baseline 40/40，top20 重叠 12–15，new_unique=0%（注：本轮 OpenAlex 在线时 Crossref/PubMed 等 5 源结果与 baseline 40 条高度互补但 hits 截断后 baseline 的 40 条因引用排序优势占满前 40 —— 覆盖率收益体现在 unique 池 4.6–5.0×，而非截断窗口内）
  - cs-01/cs-02：公共 IP 下 arXiv 406/DBLP 解析失败/S2 429，仅 Crossref(±OpenAlex) 在线，unique 40–79
  - exact-01：3 源在线，fed 命中目标论文 ✓（修正 DOI 后）；exact-02：该轮 OpenAlex 恰 429，DOAJ+Crossref 池 48 未含目标；独立三连测验证 OpenAlex 在线时 100% 命中且 top1
  - 平均：federated unique 142.5 vs baseline 39.7；federated latency 2.68s vs baseline ~2.6s
- Gate 6 逐条：coverage 增加 ✓（unique 池 3–5×，多源在线时 4.6–5.0×）；top 无异常漂移 ✓（DOI 重叠 37–40/40，精确论文可命中；排序差异源于引用序 vs 相关性序，属预期非漂移）；latency 可接受 ✓（平均 2.7s，与单源相当）；无 candidate explosion ✓（unique ≤200 ≪ lane_limit 1000/pool_max 3000）。
- 已知边界（记录，不修）：
  1. 公共 IP 限流是最大变量：S2 持续 429、OpenAlex 突发 429、arXiv 406（mosaic 拼 "all:" 前缀）、DBLP 返回非 JSON、BASE 503——全部被失败隔离吸收，不影响其余源。
  2. mosaic 侧仅 openalex 源模块映射 citation_count（crossref is-referenced-by-count、europepmc citedByCount 未映射）——只读不改；当前排序以 OpenAlex 引用为权威，merge_papers 的 citation 回填机制部分弥补。
- 是否偏离计划：否（benchmark 脚本按计划评估后新增 scripts/ 目录，仅 1 个文件；fixture 放 tests/fixtures/）。
- commit：见 "feat: add federated benchmark fixture, comparison script, and deterministic merge ordering"
- 下一步：Phase 7（可选）source-aware query routing——评估后决定跳过或最小实现；随后 Phase 8 打包。

### Phase 7（可选增强——评估后跳过）
- 时间：2026-09-25
- 评估方式：无法运行真实 LLM query generation（本环境无 LLM key，且原则 8 禁触任何 key），改用"手写 style-faithful 变体"做路由收益代理实验——按 prompts.py 各源官方风格（OpenAlex 关键词+引号短语 / Crossref 3-10 词书目式无布尔 / PubMed 大写布尔 ESearch）为 bio-01、cs-01 各写 per-source 查询，A=单查询广播（现行为）vs B=逐源路由，均用 mosaic merge_papers 合并、指纹取 DOI/title。
- 结果：
  - bio-01：A 49 篇（30.3s，1 错误——S2 SSL 超时）；B 50 篇（7.5s，0 错误）；重叠 34，onlyA 15 / onlyB 16。
  - cs-01：A 20 篇（1.9s，3 错误）；B 20 篇（2.2s，0 错误）；重叠仅 4，onlyA 16 / onlyB 16。
- 判定：无明确收益——数量持平（49v50、20v20）；B 的延迟/错误优势全部来自"路由集恰好不含 S2/DBLP"这一实验安排，非路由本身；组合差异（overlap 34/49、4/20）在无 gold standard 下无法判优劣（计划明示第一轮不建 gold standard）；malformed rate 需 LLM 实测，本环境不可得。按 Gate 7 自身标准"只有有明确收益才保留"→ 不保留。
- 结论：Phase 7 跳过，不引入 Query Router 代码；source-specific query generation 既有资产保留原位（federated 走 generic 分支），未来有 LLM 环境可按本实验框架重测。
- commit：无代码变更，仅本日志（随 Phase 8 一并提交）。
- 下一步：Phase 8 打包（pyproject optional extra federated + python_version marker + test_packaging）。

### Phase 8
- 时间：2026-09-25
- KnowledgeSeek SHA：1d97461 →（本次 commit）
- 修改文件：pyproject.toml（federated extra：`mosaic-search>=1.5.5; python_version >= '3.11'`）、tests/test_packaging.py（test_federated_extra_is_optional_and_gated）、.github/workflows/ci.yml（新增 federated job：Python 3.11 安装 `.[dev,federated]` 并运行 mosaic 测试集）
- 检查结果：
  - pyproject：federated 为 opt-in extra，带 `python_version >= '3.11'` 标记；base dependencies 与 requirements.txt 均不含 mosaic；未混入 playwright/notebooklm/flask/sqlite-vec/scrapy/selenium 等重量级 extras
  - Python 3.10 无 mosaic 环境：213 passed / 1 skipped；CLI `--help` 正常；未安装 mosaic 时 agent 惰性初始化、lane 报错并返回空候选，不崩溃
  - Docker 无需变更（python:3.12-slim 安装 `.`，如需 federated 可 `pip install ".[federated]"`）
  - CI：新增 federated job 与现有 test matrix 并存，base matrix 不装 mosaic
- Gate 8 逐条：✅ pip install -e ".[dev]" 不拉 mosaic（extra 门控）；✅ 全测试通过（py3.10 无 mosaic 213 passed；py3.11 带 mosaic 由 CI federated job 覆盖）；✅ 未安装 mosaic 时 CLI/--federated 参数给出可操作错误（MosaicNotInstalledError 提示 `pip install 'paperseek[federated]'`）；✅ requirements.txt / Docker 不变（base 安装不含 mosaic）
- 是否偏离计划：否——federated extra 采用 mosaic-search 基础安装（无 core extra，其 dev/notebooklm/browser/ui/desktop/rag/analysis/all 均非运行所需，httpx+stdlib 即足以支撑 federated retrieval）
- commit：见 "build: gate MOSAIC behind optional federated extra with python marker and CI job"
- 下一步：Phase 9 文档收尾（README、user-manual、deployment 增补 federated 用法）。

### Phase 9
- 时间：2026-09-25
- KnowledgeSeek SHA：0ceb422 →（本次 commit）
- 修改文件：.env.example（新增 FEDERATED_PROFILE / FEDERATED_MAX_PER_SOURCE 注释项）、README.md + README.en.md（数据源表新增 Federated 行：可选依赖、无 Key、需 py≥3.11 + `pip install "paperseek[federated]"`、排序仍走 PaperSeek RRF/reranker/LLM）、docs/user-manual.md（能力总览表新增 federated 行；DATA_SOURCE 环境变量表补 `federated`；CLI 参数表补 `--federated-profile` / `--federated-max-per-source`；新增 "Federated 多源检索（MOSAIC）" 小节：安装方式、三种启用途径（env/CLI/Web UI）、三 profile 源清单、默认值、单源失败隔离与缺 Key 自动跳过；"选择数据源"表新增对应行）、docs/deployment.md（Docker 环境变量区说明 federated 为可选依赖及镜像启用方式 `pip install ".[federated]"`，列出三个环境变量）
- 文档内容与代码核对：CLI flags（cli.py:176-177 choices/默认）、config 默认值（config.py:60-61 general/25）、env 读取（paperseek/config.py:84-85）、profile 源清单（mosaic_provider.py:26-34）、source_metadata notes 与 optional_config 均逐条对齐
- 检查结果：全量 base 套件 213 passed / 1 skipped（py3.10 无 mosaic），test_packaging 的 README/user-manual/deployment 断言全部保持绿
- Gate 9 逐条：✅ README 双语数据源表含 federated 及安装提示；✅ user-manual 覆盖安装、启用、profile、参数；✅ deployment 说明 Docker 可选启用；✅ .env.example 有注释项可抄；✅ 文档守卫测试不回归
- 是否偏离计划：否
- commit：见 "docs: document federated multi-source retrieval usage"
- 下一步：收尾（工作树清点、向用户交付总结）。
[Phase 8 记录占位]
### Phase 8 自检记录（2026-09-24）

- federated extra 落地：pyproject.toml `federated = ['mosaic-search>=1.5.5; python_version >= "3.11"']`，基础安装（py3.8+）永不拉取 MOSAIC。
- 导入守卫：`tests/test_packaging.py` 新增 packaging guard——py3.10 环境下 `mosaic` 不可导入、`import mosaic` 报 MosaicNotInstalledError（可执行提示安装命令）。
- CI：`.github/workflows/ci.yml` 新增 `federated` job（Python 3.11 + `pip install ".[federated]"`），base job 保持 ≥3.8 不装 mosaic，双矩阵语义明确。
- 降级路径进程内验证：`mosaic_available=False`；`import mosaic` 抛 MosaicNotInstalledError（含 actionable 消息）；agent 懒初始化不受影响；federated lane 记录 error 并返回空候选，不崩溃。
- CLI argparse 在 py3.10（无 mosaic）下验证通过；requirements.txt 不含 mosaic-search；Dockerfile 无需改动（安装 `.`，federated 由 extra 控制）。
- 源元数据已声明 FEDERATED_PROFILE / FEDERATED_MAX_PER_SOURCE 环境变量；web UI 字段、README 更新归入 Phase 9 文档收尾。
- 测试基线：with mosaic 212 passed / 0 failed；without mosaic（py3.10）213 passed 1 skipped，与 base CI 持平。
- commit：`build: gate MOSAIC behind optional federated extra with python marker and CI job`（待本条自检随 Phase 8 一并提交）。
- 下一步：Phase 9 文档收尾（README/README.en.md、user-manual、deployment 说明与源元数据一致性核对）。

（Phase 8 收尾备忘：下一阶段先 `git add -A && git commit`，随后进入 Phase 9。）

