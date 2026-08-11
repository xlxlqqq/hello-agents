"""
DocGuard Agent - 统一日志系统
==============================

设计要点：
1. 基于标准库 logging，避免引入 loguru 等额外依赖
2. 同时输出到控制台和文件（按 Agent 名分文件），便于追踪
3. 提供 get_logger(name) 统一入口，自动配置 handler
4. 支持日志级别动态配置（通过 .env LOG_LEVEL）
5. 防止重复添加 handler（多次调用 get_logger 安全）
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from core.config import DocGuardConfig, get_config


# 标记是否已完成根 logger 初始化
_logging_initialized: bool = False


def setup_logging(config: Optional[DocGuardConfig] = None) -> None:
    """
    初始化全局日志系统。

    应在应用启动时调用一次，配置根 logger 的 handler 和格式。
    后续各模块通过 get_logger(__name__) 即可继承配置。

    Args:
        config: 配置实例，None 时使用全局单例
    """
    global _logging_initialized
    if _logging_initialized:
        return

    if config is None:
        config = get_config()

    log_cfg = config.log
    logs_dir = config.get_path("logs_dir")
    logs_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_cfg.level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt=log_cfg.format,
        datefmt=log_cfg.datefmt,
    )

    # 清理已有 handler，避免重复输出（如 uvicorn 预配置的 handler）
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # ----- 控制台 handler -----
    if log_cfg.console_log_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(root_logger.level)
        root_logger.addHandler(console_handler)

    # ----- 文件 handler（按大小轮转，单文件 10MB，保留 5 份） -----
    if log_cfg.file_log_enabled:
        main_log_file = logs_dir / "docguard.log"
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(main_log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(root_logger.level)
        root_logger.addHandler(file_handler)

    # 降低第三方库的日志噪音
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _logging_initialized = True
    root_logger.info(
        "DocGuard 日志系统初始化完成 | level=%s | logs_dir=%s",
        log_cfg.level,
        logs_dir,
    )


def get_logger(name: str) -> logging.Logger:
    """
    获取命名 logger。

    建议在每个模块顶部调用：logger = get_logger(__name__)
    会自动继承根 logger 的 handler 配置，无需重复添加。

    Args:
        name: 通常传 __name__，如 "agents.review_agent"

    Returns:
        配置好的 logging.Logger 实例
    """
    # 惰性初始化：首次调用时自动 setup
    if not _logging_initialized:
        try:
            setup_logging()
        except Exception:
            # 初始化失败也不应阻塞业务，回退到 basicConfig
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            )

    return logging.getLogger(name)


def get_agent_file_logger(agent_name: str, config: Optional[DocGuardConfig] = None) -> logging.Logger:
    """
    为单个 Agent 创建独立文件 logger（同时输出到主日志和独立文件）。

    用于 Agent 调试时单独查看某个 Agent 的完整执行日志。

    Args:
        agent_name: Agent 名称（如 "review_agent"）
        config: 配置实例

    Returns:
        配置了独立文件 handler 的 logger
    """
    if config is None:
        config = get_config()

    logger = get_logger(f"agents.{agent_name}")

    # 避免重复添加独立文件 handler
    handler_flag = f"_docguard_file_handler_{agent_name}"
    if getattr(logger, handler_flag, False):
        return logger

    logs_dir = config.get_path("logs_dir")
    agent_log_file = logs_dir / f"{agent_name}.log"

    formatter = logging.Formatter(
        fmt=config.log.format,
        datefmt=config.log.datefmt,
    )
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(agent_log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # 独立文件记录 DEBUG 级别，便于排查
    logger.addHandler(file_handler)
    logger.propagate = True  # 仍向上传播到 root logger，确保主日志也记录

    setattr(logger, handler_flag, True)
    return logger
