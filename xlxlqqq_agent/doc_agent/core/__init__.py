"""
DocGuard Agent - 核心基础层
============================

提供全局共享的基础设施：
- config: 配置管理（dataclass + .env）
- logging_config: 统一日志系统
- llm_client: LLM 客户端（OpenAI 兼容封装）
- embedding_client: Embedding 客户端
- exceptions: 自定义异常类
- state: Agent 共享状态定义
"""

__version__ = "1.0.0"
