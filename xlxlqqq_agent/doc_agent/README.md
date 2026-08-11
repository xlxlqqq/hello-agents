# DocGuard Agent

> 企业技术文档智能审查 Agent —— 基于 LLM + RAG + Multi-Agent 架构

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/framework-LangGraph-green.svg)](https://github.com/langchain-ai/langgraph)
[![Phase](https://img.shields.io/badge/phase-5%20Repair%20Agent-blue.svg)](#开发阶段)

DocGuard Agent 是一个企业级文档智能审查与规范化系统，自动检查 DOCX 技术文档中的内容、格式、结构和规范问题，并结合历史优秀文档知识库实现企业级文档规范学习，最终输出修复后的 DOCX 文件。

## 当前阶段：Phase 5 — Multi-Agent 工作流 + Repair Agent

已完成 **Planner + Parser + Retrieval + Review + Repair** 五阶段 Multi-Agent 工作流，支持：

- ✅ DOCX 文档结构化解析（段落/Run/字体/标题/表格/图片）
- ✅ ChromaDB 向量知识库（持久化 + CRUD + 批量摄取）
- ✅ RAG 检索（多段落查询 + 相似度过滤 + 去重排序）
- ✅ 文档风格画像（统计模式 + 可选 LLM 增强）
- ✅ 企业术语库自动提取
- ✅ Review Agent（格式 / 结构 / 内容三类规则检查引擎）
- ✅ 质量评分（0-100）+ 问题清单 + 改进建议报告
- ✅ Repair Agent（自动修复 + 批注标记，输出修复后 DOCX）
- ✅ 六类修复器（文本替换 / 字体 / 字号 / 行距 / 缩进 / 仅批注）
- ✅ LangGraph 工作流编排（双层条件路由 + 异常降级）
- ✅ 无 API Key 环境下的 Mock Embedding 模式

## 目录结构

```
doc_agent/
├── agents/                     # Multi-Agent 工作流
│   ├── base.py                 # Agent 抽象基类（_safe_execute 包装）
│   ├── planner_agent.py        # 任务规划 Agent
│   ├── parser_agent.py         # DOCX 解析 Agent
│   ├── retrieval_agent.py      # 知识库检索 + 风格画像 Agent
│   ├── review_agent.py         # 审查 Agent（格式/结构/内容三引擎）
│   ├── repair_agent.py         # 修复 Agent（六类修复器 + 批注标记）
│   └── workflow.py             # LangGraph 工作流编排（双层条件路由）
│
├── core/                       # 核心基础设施
│   ├── config.py               # 全局配置管理
│   ├── state.py                # DocGuardState 状态定义（TypedDict）
│   ├── llm_client.py           # OpenAI 兼容 LLM 客户端
│   ├── embedding_client.py     # OpenAI 兼容 Embedding 客户端
│   ├── mock_embedding.py       # Mock Embedding（开发/测试用）
│   ├── exceptions.py           # 自定义异常体系
│   └── logging_config.py       # 日志系统
│
├── document/                   # 文档处理
│   ├── models.py               # 结构化文档数据模型
│   ├── parser.py               # DOCX → StructuredDocument 解析器
│   ├── writer.py               # StructuredDocument → DOCX 写回器
│   └── annotator.py            # 文档标注器（高亮 + 批注 + 修订）
│
├── knowledge/                  # 知识库与 RAG
│   ├── vector_store.py         # ChromaDB 向量存储封装
│   ├── ingestor.py             # 知识库摄取器（解析→切分→向量化→入库）
│   ├── retriever.py            # 知识库检索器
│   └── style_profile.py        # 文档风格画像生成器
│
├── tests/                      # 测试套件
│   ├── test_document/          # Phase 2 解析器/标注器/写回器测试
│   ├── test_knowledge/         # Phase 3 知识库单元测试（53 个）
│   ├── test_agents/            # Review/Repair Agent 单元测试
│   ├── test_integration/       # 集成测试（Phase 2/3/4/5）
│   └── ...
│
├── knowledge_docs/             # 知识库源文档（历史优秀文档）
├── output/                     # 输出目录（日志/报告/向量库）
├── rules/                      # 规则配置
│
├── run.py                      # 一键启动脚本（CLI 入口）
├── requirements.txt            # Python 依赖
├── pytest.ini                  # pytest 配置
├── .env.example                # 环境变量示例
└── DocGuard-Agent-PRD.md       # 产品需求文档
```

## 快速开始

### 1. 环境准备

```bash
# 创建 conda 环境（Python 3.10+）
conda create -n doc_agent python=3.10
conda activate doc_agent

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 LLM API Key（可选）

复制 `.env.example` 为 `.env`，填入 OpenAI 兼容 API 配置：

```bash
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
```

> **提示**：未配置 API Key 时，可使用 `--mock-embedding` / `--skip-llm` 选项进行功能验证。

### 3. 知识库摄取

将企业历史优秀 DOCX 文档放入 `knowledge_docs/` 目录，然后摄取：

```bash
# 使用真实 Embedding（需 API Key）
python run.py ingest knowledge_docs/

# 重建知识库（清空后重新摄取）
python run.py ingest knowledge_docs/ --rebuild

# 无 API Key 时使用 Mock Embedding（开发/测试模式）
python run.py ingest knowledge_docs/ --mock-embedding --rebuild
```

### 4. 文档审查（解析 + 检索 + 审查）

```bash
# 完整模式（需 API Key：解析 + 检索 + LLM 增强风格画像 + 三类规则审查）
python run.py review input.docx

# 跳过 LLM（无 API Key：解析 + 统计模式风格画像 + 规则审查）
python run.py review input.docx --skip-llm

# Mock Embedding 模式（无 API Key：解析 + 完整检索 + 统计模式风格画像 + 规则审查）
python run.py review input.docx --mock-embedding --skip-llm

# 仅输出问题清单（不显示解析/检索细节，便于 CI 集成）
python run.py review input.docx --mock-embedding --skip-llm --only-issues

# 输出 JSON 报告（含问题清单 + 质量评分 + 改进建议）
python run.py review input.docx --mock-embedding --skip-llm --json
```

**返回码约定**（便于 CI/CD 流水线使用）：
- `0`：解析成功且无 critical/major 问题
- `1`：输入文件不存在
- `2`：工作流执行异常
- `3`：解析失败
- `4`：存在 critical/major 级问题（建议修复）

### 5. 文档修复（解析 + 检索 + 审查 + 自动修复）

repair 子命令在 review 基础上接入 Repair Agent，对可自动修复的问题执行修复，
对不可修复问题添加批注，最终输出修复后的 DOCX 文件。

```bash
# 完整模式（需 API Key）
python run.py repair input.docx

# 跳过 LLM（无 API Key：解析 + 统计模式风格画像 + 规则审查 + 规则修复）
python run.py repair input.docx --skip-llm

# Mock Embedding 模式（无 API Key：完整流程跑通）
python run.py repair input.docx --mock-embedding --skip-llm

# 仅输出修复动作清单（不显示解析/检索/审查细节）
python run.py repair input.docx --mock-embedding --skip-llm --only-actions

# 输出 JSON 报告（含修复动作清单 + 修复前后值）
python run.py repair input.docx --mock-embedding --skip-llm --json

# 指定输出目录（默认 output/repaired）
python run.py repair input.docx --output-dir output/repaired
```

**修复能力映射**（基于 `IssueCategory` + `auto_repairable` 自动路由）：

| IssueCategory | 修复器 | 行为 |
|---------------|--------|------|
| CONTENT_TYPO / CONTENT_TERMINOLOGY / CONTENT_WRONG_WORD | TextReplaceRepairer | 替换错别字/术语（保留 Run 格式） |
| FORMAT_FONT | FontChangeRepairer | 修改字体（含东亚字体） |
| FORMAT_SIZE | SizeChangeRepairer | 修改字号 |
| FORMAT_SPACING | LineSpacingRepairer | 调整行距 |
| FORMAT_INDENT | IndentChangeRepairer | 调整首行缩进 |
| STRUCTURE_MISSING_SECTION / 其他 | CommentOnlyRepairer | 仅添加批注（不自动修改） |

每个修复动作会在修改位置添加 `[DocGuard]` 黄色高亮 + 红色批注文本，
记录"原文/原值 → 新值"，便于人工复核。

**repair 返回码约定：**
- `0`：修复成功
- `1`：输入文件不存在
- `2`：工作流执行异常
- `3`：解析失败
- `5`：修复失败（repair_success=False）

### 6. 知识库统计

```bash
python run.py stats
```

输出示例：

```
📊 知识库统计信息
============================================================
健康状态:       ✅ 健康
Collection:     docguard_knowledge
持久化目录:     output/chroma_db
总记录数:       4
============================================================
```

### 7. 仅解析（Phase 2 兼容）

```bash
python run.py parse input.docx
python run.py parse input.docx --json
```

## 工作流架构

Phase 5 工作流（双层条件路由）：

```
START → planner → parser → [parse_success?]
                              ├─ yes → retrieval → review → [review_issues?]
                              │                                    ├─ >0 → repair → END
                              │                                    └─ =0 → END
                              └─ no  → END
```

**Retrieval Agent 内部流程：**

1. 校验 `parsed_document`（解析失败则跳过检索）
2. 提取代表性段落（标题 + 前若干正文）作为 query
3. 向量化 query → 查询 ChromaDB
4. 相似度阈值过滤 + 去重 + 排序
5. 加载 `knowledge_docs/` 样本文档生成风格画像
6. 写入 `state.retrieved_documents` / `style_profile` / `terminology_list`

**Review Agent 内部流程：**

1. 校验 `parsed_document`（缺失则记录失败步骤，不中断工作流）
2. 读取 `state.style_profile` / `state.terminology_list`（来自 Retrieval Agent）
3. 执行三类规则检查引擎：
   - **FormatChecker**：对比 `style_profile` 检查正文字体/字号/行距/首行缩进，标题字体/字号
   - **StructureChecker**：对比 `expected_sections` 检查章节完整性，标题层级连续性
   - **ContentChecker**：基于错别字词表扫描，对比 `terminology_list` 检查术语一致性
4. 汇总 `ReviewIssue` 列表 → `calculate_quality_score` 计算 0-100 质量分
5. `summarize_issues` 生成 `ReviewReport`（含问题统计 + 改进建议）
6. 写入 `state.review_issues` / `state.review_report`

**Repair Agent 内部流程：**

1. 校验 `parsed_document` 与 `review_issues`，校验 `_docx_reference`（缺失则抛 AgentError）
2. 遍历每个 `ReviewIssue`，按 `category` + `auto_repairable` 路由到对应修复器
3. 修复器直接操作 python-docx Document（`_docx_reference`）修改内容/格式
4. 同步更新 `StructuredDocument` 的 `runs` / `paragraphs`，保持数据模型一致
5. 每个修复动作调用 `DocxAnnotator` 在修改位置添加黄色高亮 + `[DocGuard]` 红色批注
6. 构造 `RepairAction`（含原值/新值/执行结果/批注状态）追加到列表
7. 写入 `state.repaired_document` / `repair_actions` / `repair_success` / `repair_error`
8. 若 `output_dir` 提供，由 `DocxWriter` 保存修复后 DOCX 并写入 `state.output_docx_path`

**降级策略：**

- 解析失败 → 跳过 retrieval / review / repair，直接 END
- review_issues 为空 → 跳过 repair，直接 END
- EmbeddingClient 初始化失败 → 跳过检索，仍生成统计模式风格画像
- 风格画像生成失败 → 返回 None，不中断流程
- style_profile / terminology_list 缺失 → Review 降级为通用规则检查（仍执行错别字扫描）
- 任一检查引擎抛异常 → 跳过该引擎，继续执行其他引擎
- 单个修复器抛异常 → 记录失败动作，继续执行其他修复
- `_docx_reference` 缺失（如从字典反序列化） → Repair Agent 抛 AgentError

## 测试

### 运行全部测试

```bash
# Phase 3 知识库单元测试（53 个）
pytest tests/test_knowledge/ -v

# Phase 4 Review Agent 单元测试（16 个）
pytest tests/test_agents/test_review_agent.py -v

# Phase 5 Repair Agent 单元测试（36 个）
pytest tests/test_agents/test_repair_agent.py -v

# Phase 3 集成测试（10 个）
pytest tests/test_integration/test_retrieval_workflow.py -v

# Phase 4 集成测试（12 个）
pytest tests/test_integration/test_review_workflow.py -v

# Phase 5 集成测试（18 个）
pytest tests/test_integration/test_repair_workflow.py -v

# 全部测试（219 个）
pytest -v
```

### 测试覆盖

| 模块 | 测试文件 | 测试数 | 说明 |
|------|----------|--------|------|
| VectorStore | test_vector_store.py | 17 | CRUD/生命周期/统计/工厂 |
| Ingestor | test_ingestor.py | 14 | 切分器/摄取器/幂等性 |
| Retriever | test_retriever.py | 9 | 文档检索/文本检索/过滤 |
| StyleProfile | test_style_profile.py | 13 | 统计模式/序列化/工厂 |
| ReviewAgent | test_review_agent.py | 16 | FormatChecker/StructureChecker/ContentChecker/质量评分/降级 |
| RepairAgent | test_repair_agent.py | 36 | 六类修复器/路由/状态同步/降级 |
| 集成测试 | test_retrieval_workflow.py | 10 | Phase 3 完整工作流端到端 |
| 集成测试 | test_review_workflow.py | 12 | Phase 4 完整工作流端到端 |
| 集成测试 | test_repair_workflow.py | 18 | Phase 5 完整工作流端到端 + 输出 DOCX |

**测试策略：**
- 基于词袋模型的 `MockEmbeddingClient`，无需真实 API Key
- 临时 ChromaDB 持久化目录，测试间隔离
- 异步测试（pytest-asyncio，mode=auto）
- Mock 注入（Retriever / StyleGenerator 可替换）

## 开发模式

### Mock Embedding

无 API Key 环境下，使用基于词袋模型的 Mock Embedding 验证完整流程：

```python
from core.mock_embedding import create_mock_embedding_client

client = create_mock_embedding_client(dim=384)
embeddings = await client.embed_texts(["hello world", "你好世界"])
```

**局限性：**
- 不捕获词序与语义（仅用于功能验证）
- 检索质量远不如真实 Embedding 模型
- 生产环境请使用 `EmbeddingClient`（OpenAI 兼容协议）

### 配置自定义知识库目录

通过 `config.get_path("knowledge_dir")` 配置知识库源文档目录，RetrievalAgent 会从该目录加载样本文档生成风格画像。

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 项目初始化和架构设计 | ✅ 完成 |
| Phase 2 | DOCX Parser（结构化解析） | ✅ 完成 |
| Phase 3 | 知识库与 RAG | ✅ 完成 |
| Phase 4 | Multi-Agent 完整工作流 + Review Agent | ✅ 完成 |
| **Phase 5** | **Repair Agent（自动修复 + 批注标记）** | ✅ **完成** |
| Phase 6 | 优化与扩展（PDF/PPT 支持 / Human-in-the-loop） | ⏳ 待开发 |

## 技术栈

- **语言**：Python 3.10+
- **Agent 框架**：LangGraph（StateGraph + 条件路由）
- **文档解析**：python-docx
- **向量数据库**：ChromaDB（PersistentClient）
- **LLM**：OpenAI 兼容 API（AsyncOpenAI）
- **Embedding**：OpenAI 兼容 Embedding API
- **测试**：pytest + pytest-asyncio
- **日志**：Python logging（结构化输出）

## 详细文档

- [产品需求文档（PRD）](DocGuard-Agent-PRD.md)

## 后续规划

- **Phase 6**：优化与扩展
  - Validation Agent（修复后复检，统计残留问题与新引入问题）
  - PDF / PPT 支持（扩展 Parser 适配多格式）
  - Human-in-the-loop（关键修复人工确认）
  - LLM 增强内容检查（错别字 LLM 校对、术语近邻检测、语义连贯性）
  - 章节顺序学习、表格格式规范化
  - 修复-验证迭代闭环（基于 `_repair_iterations` 状态字段）
