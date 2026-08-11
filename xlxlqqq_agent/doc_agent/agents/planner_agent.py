"""
DocGuard Agent - Planner Agent
==============================

职责：
1. 任务初始化：生成 task_id（若未提供）、记录原始文件名
2. 参数校验：检查输入文件存在性、后缀合法性
3. 上下文准备：解析 user_requirements，传递给后续 Agent
4. 不调用 LLM（轻量规划阶段，避免不必要的 token 消耗）

输出 state 字段：
- task_id: 确保有值
- original_filename: 从 input_file_path 推导
- current_step: "planner"
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from agents.base import BaseAgent
from core.config import DocGuardConfig
from core.exceptions import AgentError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import DocGuardState


class PlannerAgent(BaseAgent):
    """
    Planner Agent：任务规划与参数初始化。

    此 Agent 不调用 LLM，仅做轻量的参数校验与上下文准备。
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient],
        config: DocGuardConfig,
        logger=None,
    ) -> None:
        super().__init__(llm_client, config, logger or get_logger("agents.planner_agent"))

    def agent_name(self) -> str:
        return "planner_agent"

    async def execute(self, state: DocGuardState) -> DocGuardState:
        """
        执行规划逻辑：
        1. 确保 task_id 存在
        2. 校验输入文件
        3. 推导 original_filename
        """
        self.logger.info("[Planner] 开始任务规划")

        # 1. 确保 task_id
        if not state.get("task_id"):
            state["task_id"] = f"task_{uuid.uuid4().hex[:12]}"
            self.logger.info("[Planner] 生成 task_id: %s", state["task_id"])
        else:
            self.logger.info("[Planner] 复用 task_id: %s", state["task_id"])

        # 2. 校验输入文件
        input_path_str = state.get("input_file_path", "")
        if not input_path_str:
            raise AgentError(
                "input_file_path 未设置",
                context={"task_id": state["task_id"]},
            )

        input_path = Path(input_path_str)
        if not input_path.exists():
            raise AgentError(
                f"输入文件不存在: {input_path_str}",
                context={"input_file_path": input_path_str},
            )
        if not input_path.is_file():
            raise AgentError(
                f"输入路径不是文件: {input_path_str}",
                context={"input_file_path": input_path_str},
            )
        # Phase 6 多格式支持：允许 .docx/.pdf/.ppt/.pptx/.txt，其余后缀由 Parser
        # 统一按 fallback 解析（ParserConfig.fallback_to_text_when_no_parser）。
        allowed_raw = {".docx", ".pdf", ".ppt", ".pptx", ".txt"}
        suffix = input_path.suffix.lower()
        # 如果 state 中显式带 input_format，则 Parser 会按格式路由，放宽限制
        explicit_format = bool(state.get("input_format"))
        if suffix not in allowed_raw and not explicit_format:
            raise AgentError(
                f"输入格式不在支持列表 {sorted(allowed_raw)} 中，"
                f"当前后缀: {suffix}；若要强制解析可通过 input_format 参数",
                context={
                    "input_file_path": input_path_str,
                    "suffix": suffix,
                    "supported_formats": sorted(allowed_raw),
                },
            )

        # 3. 推导 original_filename
        if not state.get("original_filename"):
            state["original_filename"] = input_path.name

        self.logger.info(
            "[Planner] 规划完成 | task_id=%s | file=%s | has_requirements=%s",
            state["task_id"],
            state["original_filename"],
            bool(state.get("user_requirements")),
        )

        return state

    def _build_summary(self, state: DocGuardState) -> str:
        return (
            f"task_id={state.get('task_id')}, "
            f"file={state.get('original_filename', 'unknown')}"
        )
