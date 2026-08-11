"""
DocGuard Agent - HITL Agent（Human-in-the-loop）
================================================

职责：
1. 决定哪些 ReviewIssue 需要人工确认：
   - 条件：hitl.enabled=True
     AND NOT hitl.auto_approve_all
     AND issue.auto_repairable=True
     AND (
         issue.severity in hitl.require_confirm_severity
         OR (hitl.require_confirm_categories 非空
             AND issue.category in hitl.require_confirm_categories)
     )
2. 不需要确认时：
   - 自动为 ALL 问题生成 decision=approve 的 RepairConfirmation（confirmed_by="auto"）
   - 标记 hitl_completed=True, hitl_required=False
3. 需要确认时：
   - 标记 hitl_required=True
   - 若提供了 confirm_callback 可执行回调（CLI 交互 / Web UI），
     回调返回 list[RepairConfirmation]
   - 没有提供回调 → 标记 hitl_completed=False，等待外部输入
     （工作流会根据 hitl_completed 判断是否进入 repair）
   - 默认情况下 hitl.auto_approve_all=True，全部自动批准
     （开发/测试模式不阻塞）

对于生产级 HITL，需要在 workflow.py 中实现"等待外部输入"的逻辑
（LangGraph 的 interrupt 机制或持久化状态后等待），
Phase 6 这里提供框架 + 默认自动批准 + CLI 交互回调（供 run.py 使用）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from agents.base import BaseAgent
from core.config import DocGuardConfig
from core.exceptions import AgentError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import (
    DocGuardState,
    RepairConfirmation,
    ReviewIssue,
)
from document.models import IssueSeverity


logger = get_logger("agents.hitl_agent")


# ============================================================
# 辅助：Issue 是否需要人工确认
# ============================================================

def issue_requires_confirmation(
    issue: ReviewIssue,
    *,
    enabled: bool,
    auto_approve_all: bool,
    require_severity: list[str],
    require_categories: list[str],
) -> bool:
    """判断单个 issue 是否需要人工确认。"""
    if not enabled:
        return False
    if auto_approve_all:
        return False
    if not issue.get("auto_repairable"):
        # 不可自动修复的 issue 走 CommentOnlyRepairer，不涉及修改，无需人工确认
        return False
    severity_hit = issue.get("severity") in (require_severity or [])
    if require_categories:
        category_hit = issue.get("category") in require_categories
    else:
        category_hit = True  # 空列表 = 全部类别都匹配
    return severity_hit or category_hit


# ============================================================
# 自动生成批准（默认开发模式）
# ============================================================

def _auto_approve(issues: list[ReviewIssue]) -> list[RepairConfirmation]:
    """自动批准所有 issue（开发模式）。"""
    now = datetime.now().isoformat()
    return [
        RepairConfirmation(
            issue_id=issue["issue_id"],
            decision="approve",
            override_fix=None,
            confirmed_by="auto",
            confirmed_at=now,
            notes=None,
        )
        for issue in issues
    ]


# ============================================================
# CLI 交互回调（供 run.py 注入）
# ============================================================

def cli_interactive_confirm(
    issues: list[ReviewIssue],
    *,
    timeout_seconds: int = 300,
) -> list[RepairConfirmation]:
    """
    命令行交互人工确认（用于 hitl.enabled=True, auto_approve_all=False 时）。

    若当前运行环境非 TTY 或用户超时，自动降级为 "system" 批准（不阻塞）。
    """
    import sys
    now = datetime.now().isoformat()
    results: list[RepairConfirmation] = []

    # 非交互环境：降级为 system 批准
    if not sys.stdin.isatty():
        for issue in issues:
            results.append(RepairConfirmation(
                issue_id=issue["issue_id"],
                decision="approve",
                override_fix=None,
                confirmed_by="system",
                confirmed_at=now,
                notes="non-tty 环境，降级为自动批准",
            ))
        return results

    print("\n" + "=" * 60)
    print("🤖 HITL: Human-in-the-loop 修复确认")
    print("=" * 60)
    print(f"共 {len(issues)} 个问题需要确认：")
    for idx, issue in enumerate(issues, start=1):
        print(
            f"\n[{idx}] [{issue['severity']}] [{issue['category']}] "
            f"{issue['title']}"
        )
        if issue.get("description"):
            print(f"    说明: {issue['description']}")
        if issue.get("suggested_fix"):
            print(f"    建议修复: {issue['suggested_fix']}")
        while True:
            try:
                raw = input(
                    "    决策 ([a]pprove 批准 / [r]eject 跳过 / "
                    "[o]verride 覆写建议值，默认 a): "
                ).strip().lower()
            except EOFError:
                raw = "a"
            if raw == "" or raw in ("a", "approve"):
                results.append(RepairConfirmation(
                    issue_id=issue["issue_id"], decision="approve",
                    override_fix=None, confirmed_by="user",
                    confirmed_at=datetime.now().isoformat(), notes=None,
                ))
                break
            if raw in ("r", "reject"):
                results.append(RepairConfirmation(
                    issue_id=issue["issue_id"], decision="reject",
                    override_fix=None, confirmed_by="user",
                    confirmed_at=datetime.now().isoformat(),
                    notes="用户拒绝该修复",
                ))
                break
            if raw in ("o", "override"):
                override = input("    请输入覆写的建议修复值: ").strip()
                results.append(RepairConfirmation(
                    issue_id=issue["issue_id"], decision="approve_with_override",
                    override_fix=override or None, confirmed_by="user",
                    confirmed_at=datetime.now().isoformat(),
                    notes=None if override else "空值，使用原建议",
                ))
                break
            print("    无效输入，请输入 a / r / o。")
    return results


# ============================================================
# HITL Agent 主体
# ============================================================

class HitlAgent(BaseAgent):
    """Human-in-the-loop Agent。

    位于 review → repair 之间，负责生成修复确认清单 repair_confirmations。
    RepairAgent 读取 repair_confirmations 并对 decision="reject" 的 issue
    跳过修复，对 "approve_with_override" 的 issue 覆写 suggested_fix 后修复。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        config: DocGuardConfig,
        confirm_callback: Optional[Callable[[list[ReviewIssue]], list[RepairConfirmation]]] = None,
    ) -> None:
        super().__init__(llm_client, config)
        self._confirm_callback = confirm_callback

    def agent_name(self) -> str:
        return "hitl_agent"

    async def execute(self, state: DocGuardState) -> DocGuardState:
        issues: list[ReviewIssue] = self._validate_state_field(
            state, "review_issues", required=False,
        ) or []
        hitl = self.config.hitl

        if not issues:
            state["hitl_required"] = False
            state["hitl_completed"] = True
            state["repair_confirmations"] = []
            return state

        # 筛选需要确认的 issue
        need_confirm = [
            i for i in issues
            if issue_requires_confirmation(
                i,
                enabled=hitl.enabled,
                auto_approve_all=hitl.auto_approve_all,
                require_severity=hitl.require_confirm_severity,
                require_categories=hitl.require_confirm_categories,
            )
        ]

        hitl_required = len(need_confirm) > 0
        state["hitl_required"] = hitl_required

        if not hitl_required:
            # 全部自动批准（含 auto_repairable=False 的也写入，方便日志追踪）
            state["repair_confirmations"] = _auto_approve(issues)
            state["hitl_completed"] = True
            return state

        # ===== 需要人工确认 =====
        logger.info(
            "[HITL] 有 %d 个问题需要人工确认（共 %d 问题）",
            len(need_confirm), len(issues),
        )

        confirmations: list[RepairConfirmation]
        if self._confirm_callback is not None:
            try:
                user_results = self._confirm_callback(need_confirm)
                confirmations = list(user_results) if user_results else []
            except Exception as e:
                logger.warning("[HITL] 回调执行失败，降级为自动批准: %s", e)
                confirmations = _auto_approve(need_confirm)
        else:
            # 无回调 → 不自动完成，留给外部系统填充（LangGraph interrupt / Web UI）
            logger.info(
                "[HITL] 无 confirm_callback，hitl_completed=False，"
                "等待外部系统写入 repair_confirmations"
            )
            state["hitl_completed"] = False
            state["repair_confirmations"] = []
            return state

        # 处理不需要确认的 issue，补齐 auto_approve
        confirmed_ids = {c["issue_id"] for c in confirmations}
        confirmations.extend(_auto_approve(
            [i for i in issues if i["issue_id"] not in confirmed_ids]
        ))
        state["repair_confirmations"] = confirmations
        state["hitl_completed"] = True
        logger.info("[HITL] 完成：%d 个确认", len(confirmations))
        return state

    def _build_summary(self, state: DocGuardState) -> str:
        confs = state.get("repair_confirmations") or []
        approved = sum(1 for c in confs if c.get("decision") == "approve")
        rejected = sum(1 for c in confs if c.get("decision") == "reject")
        overridden = sum(
            1 for c in confs if c.get("decision") == "approve_with_override"
        )
        hitl_req = "是" if state.get("hitl_required") else "否"
        return (
            f"需确认={hitl_req} 确认={len(confs)} "
            f"批准={approved} 拒绝={rejected} 覆写={overridden}"
        )
