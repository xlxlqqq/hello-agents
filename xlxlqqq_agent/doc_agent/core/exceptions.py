"""
DocGuard Agent - 自定义异常体系
================================

设计原则：
1. 所有业务异常继承 DocGuardError，便于全局捕获
2. 区分"可恢复异常"（不影响后续流程）与"致命异常"（终止流程）
3. 每个异常携带上下文信息，便于日志追踪
"""

from typing import Any, Optional


class DocGuardError(Exception):
    """DocGuard 所有异常的基类"""

    def __init__(
        self,
        message: str,
        *,
        context: Optional[dict] = None,
        cause: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict = context or {}
        self.cause: Optional[Exception] = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            parts.append(f"context=[{ctx_str}]")
        if self.cause:
            parts.append(f"caused_by={type(self.cause).__name__}: {self.cause}")
        return " | ".join(parts)


# ============================================================
# 配置异常
# ============================================================
class ConfigError(DocGuardError):
    """配置错误（如缺少必填项）"""


# ============================================================
# LLM 异常
# ============================================================
class LLMError(DocGuardError):
    """LLM 调用通用异常"""


class LLMRateLimitError(LLMError):
    """LLM 触发限流（429）"""

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    """LLM 调用超时"""


class LLMResponseParseError(LLMError):
    """LLM 响应解析失败（如 JSON 结构化输出格式不符）"""


# ============================================================
# 文档处理异常
# ============================================================
class DocumentError(DocGuardError):
    """文档处理通用异常"""


class DocumentParseError(DocumentError):
    """文档解析失败（如文件损坏、格式不支持）"""


class DocumentWriteError(DocumentError):
    """文档写入失败"""


class AnnotationError(DocumentError):
    """文档标注失败（如批注/高亮写入异常）"""


# ============================================================
# 知识库 / RAG 异常
# ============================================================
class KnowledgeBaseError(DocGuardError):
    """知识库通用异常"""


class EmbeddingError(KnowledgeBaseError):
    """Embedding 向量化失败"""


class VectorStoreError(KnowledgeBaseError):
    """向量数据库操作失败"""


class RetrievalError(KnowledgeBaseError):
    """检索失败"""


# ============================================================
# Agent 执行异常
# ============================================================
class AgentError(DocGuardError):
    """Agent 执行通用异常"""


class AgentStateError(AgentError):
    """Agent 状态异常（如缺少必填字段）"""


class WorkflowError(DocGuardError):
    """LangGraph 工作流编排异常"""


# ============================================================
# 修复异常
# ============================================================
class RepairError(DocGuardError):
    """自动修复失败"""


class ValidationError(DocGuardError):
    """修复验证失败"""


# ============================================================
# 便捷工具函数
# ============================================================
def wrap_exception(
    original: Exception,
    new_cls: type,
    message: str,
    **context: Any,
) -> DocGuardError:
    """
    将底层异常包装为 DocGuard 自定义异常，保留原始 traceback 上下文。

    Args:
        original: 原始异常
        new_cls: 目标异常类（DocGuardError 子类）
        message: 新异常消息
        **context: 上下文信息

    Returns:
        包装后的 DocGuardError 实例
    """
    return new_cls(message, context=context, cause=original)
