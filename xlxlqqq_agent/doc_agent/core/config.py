"""
DocGuard Agent - 全局配置管理
==============================

设计要点：
1. 使用 dataclass 而非散落的 os.getenv 调用，集中管理所有配置
2. __post_init__ 中从 .env 加载覆盖默认值，支持环境变量优先级
3. 提供 get_config() 单例访问，全局唯一配置实例
4. 配置项分为：LLM / ChromaDB / 路径 / Agent 参数 / 日志
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ============================================================
# 子配置 dataclass
# ============================================================


@dataclass
class LLMConfig:
    """LLM 配置（OpenAI 兼容协议）"""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: int = 120
    # Embedding 批量大小（OpenAI 单次最多 2048 输入）
    embedding_batch_size: int = 64


@dataclass
class ChromaConfig:
    """ChromaDB 向量数据库配置"""

    persist_directory: str = "output/chroma_db"
    collection_name: str = "docguard_knowledge"
    top_k: int = 5
    similarity_threshold: float = 0.7


@dataclass
class PathsConfig:
    """路径配置（全部相对于项目根目录）"""

    output_dir: str = "output"
    logs_dir: str = "output/logs"
    reports_dir: str = "output/reports"
    repaired_dir: str = "output/repaired"
    knowledge_dir: str = "knowledge_docs"
    rules_dir: str = "rules"


@dataclass
class HitlConfig:
    """Human-in-the-loop 配置"""

    enabled: bool = False                 # 是否启用 HITL（默认关闭，开发模式自动批准确认）
    auto_approve_all: bool = True         # 自动批准所有修复（开发模式默认 True）
    require_confirm_severity: list[str] = field(
        default_factory=lambda: ["critical"]
    )                                        # 需要人工确认的严重级别
    require_confirm_categories: list[str] = field(
        default_factory=list
    )                                        # 需要人工确认的问题类别（空列表=全部）
    confirm_timeout_seconds: int = 300      # CLI 交互超时（秒）


@dataclass
class ParserConfig:
    """多格式解析器配置"""

    supported_formats: list[str] = field(
        default_factory=lambda: ["docx", "pdf", "ppt"]
    )                                        # 支持的格式列表
    pdf_engine: str = "pdfplumber"          # PDF 解析引擎（pdfplumber / pypdfium2 / pdfminer）
    ppt_engine: str = "python-pptx"         # PPTX 解析引擎
    pdf_extract_images: bool = False        # PDF 是否提取图片（避免重依赖）
    pdf_tables_flavor: str = "lattice"      # PDF 表格识别模式（lattice/stream）
    fallback_to_text_when_no_parser: bool = True  # 解析器缺失时降级为 UTF-8 纯文本读取


@dataclass
class ValidationConfig:
    """验证阶段配置（修复后复检）"""

    max_iterations: int = 3                  # 修复-验证最大迭代次数
    enabled: bool = True                     # 是否启用验证阶段
    enable_track_changes: bool = False       # 修复时是否开启 Word 修订模式
    stop_on_new_issues: bool = True          # 发现新引入问题时停止迭代（防止副作用）
    detect_remaining_issues: bool = True     # 是否检测残留问题
    detect_new_issues: bool = True           # 是否检测新引入问题


@dataclass
class AgentConfig:
    """Agent 行为参数"""

    max_review_iterations: int = 3      # 保留：兼容旧命名，等同 validation.max_iterations
    auto_repair_enabled: bool = True    # 是否启用自动修复
    validation_enabled: bool = True     # 保留：兼容旧命名，等同 validation.enabled
    enable_track_changes: bool = False  # 保留：兼容旧命名
    stop_validation_on_new_issues: bool = True  # 保留：兼容旧命名


@dataclass
class LogConfig:
    """日志配置"""

    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    # 文件日志：按 Agent 名分文件
    file_log_enabled: bool = True
    # 控制台日志
    console_log_enabled: bool = True


# ============================================================
# 主配置类
# ============================================================


@dataclass
class DocGuardConfig:
    """DocGuard Agent 主配置"""

    # 应用元信息
    app_name: str = "DocGuard Agent"
    version: str = "1.1.0"                 # Phase 6: Validation + HITL + 多格式
    debug: bool = False

    # 子配置
    llm: LLMConfig = field(default_factory=LLMConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    hitl: HitlConfig = field(default_factory=HitlConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    log: LogConfig = field(default_factory=LogConfig)

    # 项目根目录（运行时自动推导，不应在 .env 中设置）
    project_root: str = ""

    def __post_init__(self) -> None:
        """从环境变量加载覆盖默认值"""
        load_dotenv()

        # ----- 应用 -----
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # ----- LLM -----
        self.llm.api_key = os.getenv("LLM_API_KEY", self.llm.api_key)
        self.llm.base_url = os.getenv("LLM_BASE_URL", self.llm.base_url)
        self.llm.chat_model = os.getenv("LLM_CHAT_MODEL", self.llm.chat_model)
        self.llm.embedding_model = os.getenv(
            "LLM_EMBEDDING_MODEL", self.llm.embedding_model
        )
        self.llm.temperature = float(
            os.getenv("LLM_TEMPERATURE", str(self.llm.temperature))
        )
        self.llm.max_tokens = int(
            os.getenv("LLM_MAX_TOKENS", str(self.llm.max_tokens))
        )
        self.llm.timeout = int(os.getenv("LLM_TIMEOUT", str(self.llm.timeout)))

        # ----- ChromaDB -----
        self.chroma.persist_directory = os.getenv(
            "CHROMA_PERSIST_DIRECTORY", self.chroma.persist_directory
        )
        self.chroma.collection_name = os.getenv(
            "CHROMA_COLLECTION_NAME", self.chroma.collection_name
        )
        self.chroma.top_k = int(os.getenv("CHROMA_TOP_K", str(self.chroma.top_k)))
        self.chroma.similarity_threshold = float(
            os.getenv(
                "CHROMA_SIMILARITY_THRESHOLD",
                str(self.chroma.similarity_threshold),
            )
        )

        # ----- 路径 -----
        self.paths.output_dir = os.getenv("OUTPUT_DIR", self.paths.output_dir)
        self.paths.logs_dir = os.getenv("LOGS_DIR", self.paths.logs_dir)
        self.paths.reports_dir = os.getenv("REPORTS_DIR", self.paths.reports_dir)
        self.paths.repaired_dir = os.getenv("REPAIRED_DIR", self.paths.repaired_dir)
        self.paths.knowledge_dir = os.getenv("KNOWLEDGE_DIR", self.paths.knowledge_dir)
        self.paths.rules_dir = os.getenv("RULES_DIR", self.paths.rules_dir)

        # ----- HITL -----
        self.hitl.enabled = (
            os.getenv("HITL_ENABLED", str(self.hitl.enabled)).lower() == "true"
        )
        self.hitl.auto_approve_all = (
            os.getenv(
                "HITL_AUTO_APPROVE_ALL",
                str(self.hitl.auto_approve_all),
            ).lower()
            == "true"
        )
        hitl_sev = os.getenv("HITL_REQUIRE_CONFIRM_SEVERITY")
        if hitl_sev:
            self.hitl.require_confirm_severity = [
                s.strip() for s in hitl_sev.split(",") if s.strip()
            ]
        self.hitl.confirm_timeout_seconds = int(
            os.getenv(
                "HITL_CONFIRM_TIMEOUT_SECONDS",
                str(self.hitl.confirm_timeout_seconds),
            )
        )

        # ----- Parser（多格式）-----
        pf = os.getenv("PARSER_SUPPORTED_FORMATS")
        if pf:
            self.parser.supported_formats = [
                f.strip() for f in pf.split(",") if f.strip()
            ]
        self.parser.pdf_engine = os.getenv("PARSER_PDF_ENGINE", self.parser.pdf_engine)
        self.parser.ppt_engine = os.getenv("PARSER_PPT_ENGINE", self.parser.ppt_engine)
        self.parser.fallback_to_text_when_no_parser = (
            os.getenv(
                "PARSER_FALLBACK_TO_TEXT",
                str(self.parser.fallback_to_text_when_no_parser),
            ).lower()
            == "true"
        )

        # ----- Agent -----
        self.agent.max_review_iterations = int(
            os.getenv(
                "MAX_REVIEW_ITERATIONS",
                str(self.agent.max_review_iterations),
            )
        )
        self.agent.auto_repair_enabled = os.getenv(
            "AUTO_REPAIR_ENABLED", "true"
        ).lower() == "true"
        self.agent.validation_enabled = os.getenv(
            "VALIDATION_ENABLED", "true"
        ).lower() == "true"
        self.agent.stop_validation_on_new_issues = (
            os.getenv(
                "STOP_ON_NEW_ISSUES",
                str(self.agent.stop_validation_on_new_issues),
            ).lower()
            == "true"
        )

        # ----- Validation（同步 agent 兼容字段 → validation 子配置） -----
        self.validation.max_iterations = int(
            os.getenv(
                "VALIDATION_MAX_ITERATIONS",
                str(self.agent.max_review_iterations),
            )
        )
        self.validation.enabled = self.agent.validation_enabled
        self.validation.stop_on_new_issues = self.agent.stop_validation_on_new_issues
        self.validation.enable_track_changes = self.agent.enable_track_changes

        # ----- 日志 -----
        self.log.level = os.getenv("LOG_LEVEL", self.log.level)

        # ----- 推导项目根目录 -----
        # 本文件位于: <project_root>/core/config.py
        self.project_root = str(Path(__file__).resolve().parent.parent)

        # ----- 确保关键目录存在 -----
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """确保运行时所需的输出目录存在"""
        root = Path(self.project_root)
        for dir_path in [
            self.paths.output_dir,
            self.paths.logs_dir,
            self.paths.reports_dir,
            self.paths.repaired_dir,
            self.paths.knowledge_dir,
            self.paths.rules_dir,
            self.chroma.persist_directory,
        ]:
            abs_path = root / dir_path if not Path(dir_path).is_absolute() else Path(dir_path)
            abs_path.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 便捷访问方法
    # ============================================================
    def get_path(self, name: str) -> Path:
        """
        获取配置中 paths 段某个路径的绝对 Path 对象。

        Args:
            name: 路径字段名（如 "output_dir", "logs_dir"）

        Returns:
            绝对路径 Path 对象
        """
        rel = getattr(self.paths, name)
        p = Path(rel)
        if not p.is_absolute():
            p = Path(self.project_root) / p
        return p

    def validate_required(self) -> list[str]:
        """
        校验必填配置项，返回缺失项列表（空列表表示全部通过）。

        Returns:
            缺失或无效的配置项描述列表
        """
        errors: list[str] = []
        if not self.llm.api_key:
            errors.append("LLM_API_KEY 未配置")
        if not self.llm.base_url:
            errors.append("LLM_BASE_URL 未配置")
        if not self.llm.chat_model:
            errors.append("LLM_CHAT_MODEL 未配置")
        return errors


# ============================================================
# 全局单例
# ============================================================

# 全局配置实例（模块加载时创建一次）
_config: Optional[DocGuardConfig] = None


def get_config() -> DocGuardConfig:
    """
    获取全局配置实例（单例模式）。

    首次调用时创建并初始化配置，后续调用返回同一实例。
    在测试或需要覆盖配置的场景下，可调用 reset_config() 重置。
    """
    global _config
    if _config is None:
        _config = DocGuardConfig()
    return _config


def reset_config() -> None:
    """重置全局配置实例（主要用于测试场景）"""
    global _config
    _config = None


def set_config(config: DocGuardConfig) -> None:
    """直接设置全局配置实例（用于依赖注入或测试）"""
    global _config
    _config = config
