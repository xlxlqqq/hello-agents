"""
DocGuard Agent - Agent 抽象基类
================================

设计要点：
1. 所有 Agent 继承 BaseAgent，统一 execute 接口
2. _safe_execute 包装器统一处理异常，避免单个 Agent 失败中断整个工作流
3. 每个 Agent 通过依赖注入获得 LLMClient / Config / Logger
4. execute 方法返回更新后的 DocGuardState（LangGraph 节点协议）
5. 自动记录步骤日志（StepLog）到 state.step_logs

Agent 实现规范：
- agent_name() 返回唯一标识，用于日志与步骤追踪
- execute() 实现核心逻辑，读取上游字段、写入自身负责的字段
- 异常应在 execute 内捕获并写入 state（如 parse_error），
  只有不可恢复的异常才向上抛出（由 _safe_execute 兜底）
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from core.config import DocGuardConfig
from core.exceptions import AgentError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import DocGuardState, StepLog


class BaseAgent(ABC):
    """
    Agent 抽象基类。

    所有具体 Agent（PlannerAgent / ParserAgent / ...）必须继承此类
    并实现 agent_name() 和 execute() 方法。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        config: DocGuardConfig,
        logger=None,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端实例（可能为 None，对于不需要 LLM 的 Agent）
            config: 全局配置
            logger: 可选的 logger（None 时按 agent_name 创建）
        """
        # 兼容两套命名：历史代码使用 self.llm，部分 Agent 依赖 self.llm_client
        self.llm: LLMClient = llm_client
        self.llm_client: LLMClient = llm_client
        self.config: DocGuardConfig = config
        self.logger = logger or get_logger(f"agents.{self.agent_name()}")

    # ============================================================
    # 抽象方法
    # ============================================================
    @abstractmethod
    def agent_name(self) -> str:
        """返回 Agent 唯一名称（如 "parser_agent"）"""
        ...

    @abstractmethod
    async def execute(self, state: DocGuardState) -> DocGuardState:
        """
        执行 Agent 核心逻辑。

        Args:
            state: 当前工作流状态（只读语义，实际返回新 state）

        Returns:
            更新后的 DocGuardState

        注意：
        - 读取上游字段，写入自身负责的字段
        - 可恢复异常在方法内捕获并写入 state（如 parse_error）
        - 不可恢复异常可向上抛出，由 _safe_execute 兜底
        """
        ...

    # ============================================================
    # 公共方法
    # ============================================================
    async def _safe_execute(self, state: DocGuardState) -> DocGuardState:
        """
        带异常处理的执行包装器（LangGraph 节点入口）。

        - 自动记录步骤开始/结束时间与耗时
        - 异常被捕获后记录到 state.step_logs，不向上抛出
          （保证工作流不中断）
        - 更新 state.current_step

        Args:
            state: 当前工作流状态

        Returns:
            更新后的 DocGuardState
        """
        agent_name = self.agent_name()
        started_at = datetime.now().isoformat()
        step_start = time.time()
        state["current_step"] = agent_name

        self.logger.info("[%s] 开始执行", agent_name)

        try:
            result_state = await self.execute(state)
            elapsed = round(time.time() - step_start, 3)
            self.logger.info("[%s] 执行完成，耗时 %.3fs", agent_name, elapsed)

            # 追加步骤日志
            result_state["step_logs"].append(StepLog(
                step=agent_name,
                success=True,
                started_at=started_at,
                elapsed_seconds=elapsed,
                error=None,
                summary=self._build_summary(result_state),
            ))
            result_state["total_elapsed_seconds"] = round(
                result_state.get("total_elapsed_seconds", 0.0) + elapsed, 3
            )
            return result_state

        except Exception as e:
            elapsed = round(time.time() - step_start, 3)
            self.logger.error(
                "[%s] 执行失败: %s", agent_name, e, exc_info=True
            )

            # 写入失败日志
            state["step_logs"].append(StepLog(
                step=agent_name,
                success=False,
                started_at=started_at,
                elapsed_seconds=elapsed,
                error=str(e),
                summary=None,
            ))
            state["total_elapsed_seconds"] = round(
                state.get("total_elapsed_seconds", 0.0) + elapsed, 3
            )
            return state

    # ============================================================
    # 可重写方法
    # ============================================================
    def _build_summary(self, state: DocGuardState) -> str:
        """
        构建步骤摘要（写入 step_logs）。

        子类可重写以提供更有意义的摘要。
        默认返回空字符串。

        Args:
            state: 执行完成后的状态

        Returns:
            摘要字符串
        """
        return ""

    # ============================================================
    # 辅助方法
    # ============================================================
    def _validate_state_field(
        self,
        state: DocGuardState,
        field_name: str,
        required: bool = True,
    ) -> Any:
        """
        校验 state 字段是否存在。

        Args:
            state: 工作流状态
            field_name: 字段名
            required: 是否必填

        Returns:
            字段值

        Raises:
            AgentError: 必填字段缺失
        """
        value = state.get(field_name)
        if required and (value is None or value == "" or value == []):
            raise AgentError(
                f"状态字段 '{field_name}' 缺失或为空，{self.agent_name()} 无法执行",
                context={"agent": self.agent_name(), "field": field_name},
            )
        return value
