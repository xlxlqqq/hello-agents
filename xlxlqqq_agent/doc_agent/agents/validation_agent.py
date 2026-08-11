"""
DocGuard Agent - Validation Agent
====================================

职责（修复后复检）：
1. 读取修复后的 repaired_document 与原始 review_issues
2. 复用 ReviewAgent 的三检查引擎（FormatChecker / StructureChecker / ContentChecker）
   对 repaired_document 重新审查，产出"修复后新问题集" recheck_issues
3. 对比分析：
   - total_repaired: 成功修复动作数（对应 success RepairAction 的 issue 不再出现）
   - total_remaining: original 问题中仍在 recheck_issues 中出现（按位置/类别/文本匹配）
   - newly_introduced: recheck_issues 中不属于 original 问题集合的新项目
4. 判断 passed: remaining == 0 AND newly_introduced == 0
5. 配合 max_review_iterations 与 _repair_iterations 控制迭代闭环：
   - 若 still remaining 且 迭代未达上限 → 把 remaining_issues 作为下一轮 review_issues
     （由工作流条件路由实现）
   - 若 new_introduced_issues 非空且 stop_validation_on_new_issues=True → 打标记终止迭代
6. 写入 state: validation_result / remaining_issues / new_introduced_issues

关键设计：
- 直接实例化 ReviewAgent 的检查引擎（无需再绕 Agent 接口），
  避免重复走 _safe_execute 日志循环
- issue 匹配算法：优先 issue_id（若修复后仍保留） → 再按 location.paragraph_index
  + category + original_text 模糊匹配
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agents.base import BaseAgent
from agents.review_agent import (
    COMMON_TYPO_RULES,
    ContentChecker,
    FormatChecker,
    StructureChecker,
)
from core.config import DocGuardConfig
from core.exceptions import AgentError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import (
    DocGuardState,
    ReviewIssue,
    ValidationResult,
)
from document.models import IssueCategory, IssueSeverity, StructuredDocument
from knowledge.style_profile import StyleProfile


logger = get_logger("agents.validation_agent")


# ============================================================
# Issue 匹配工具
# ============================================================

def _issue_fingerprint(issue: ReviewIssue) -> tuple:
    """构建 Issue 的匹配指纹（用于跨轮次对比）"""
    loc = issue.get("location") or {}
    return (
        issue.get("category"),
        issue.get("severity"),
        loc.get("paragraph_index"),
        loc.get("run_index"),
        loc.get("char_start"),
        (issue.get("original_text") or "")[:20],
        issue.get("title", "")[:40],
    )


def _issues_match(orig: ReviewIssue, recheck: ReviewIssue) -> bool:
    """判断 recheck 问题是否对应 original 问题（即该问题未被成功修复，残留）。"""
    # 1. 文本指纹完全相等 → 肯定是残留
    if _issue_fingerprint(orig) == _issue_fingerprint(recheck):
        return True

    # 2. 同段落 + 同类别 + 相同 original_text 前缀 → 认定为残留
    loc_o = orig.get("location") or {}
    loc_r = recheck.get("location") or {}
    if (
        orig.get("category") == recheck.get("category")
        and loc_o.get("paragraph_index") is not None
        and loc_o.get("paragraph_index") == loc_r.get("paragraph_index")
        and (orig.get("original_text") or "").strip()
        == (recheck.get("original_text") or "").strip()
    ):
        return True

    # 3. 对于 TYPO 类：original_text 相同且 recheck.text_snippet 仍含错误写法 → 残留
    if orig.get("category") == IssueCategory.CONTENT_TYPO:
        for wrong, _, _ in COMMON_TYPO_RULES:
            if orig.get("original_text") and wrong in orig.get("original_text", ""):
                snippet = (loc_r.get("text_snippet") or "") + (
                    recheck.get("original_text") or ""
                )
                if wrong in snippet:
                    if orig.get("category") == recheck.get("category"):
                        return True
    return False


# ============================================================
# Validation Agent 主体
# ============================================================

@dataclass
class _ValidationSummary:
    """复检结果摘要"""

    total_in_original: int
    total_recheck_issues: int
    total_repaired: int
    total_remaining: int
    newly_introduced: int
    passed: bool


class ValidationAgent(BaseAgent):
    """
    修复后复检 Agent。

    复用 ReviewAgent 的三个检查引擎对 repaired_document 重新审查，
    与原始 review_issues 对比，统计修复情况。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        config: DocGuardConfig,
        format_checker_cls: Any = FormatChecker,
        structure_checker_cls: Any = StructureChecker,
        content_checker_cls: Any = ContentChecker,
    ) -> None:
        super().__init__(llm_client, config)
        self._fmt_cls = format_checker_cls
        self._str_cls = structure_checker_cls
        self._cnt_cls = content_checker_cls

    def agent_name(self) -> str:
        return "validation_agent"

    # ============================================================
    # 核心逻辑
    # ============================================================
    async def execute(self, state: DocGuardState) -> DocGuardState:
        if not self.config.agent.validation_enabled:
            self.logger.info("validation 阶段被配置禁用，跳过")
            repaired_count = len(state.get("repair_actions") or [])
            state["validation_result"] = ValidationResult(
                # 标准字段
                pass_flag=True,
                fixed_issue_count=repaired_count,
                remaining_issue_count=0,
                new_issue_count=0,
                fixed_issues=list(state.get("review_issues") or []),
                remaining_issues=[],
                new_issues=[],
                max_iterations=int(
                    getattr(
                        getattr(self.config, "validation", None),
                        "max_iterations",
                        3,
                    )
                ),
                current_iteration=int(state.get("validation_iterations") or 1),
                improvement_suggestions=[
                    "Validation 被配置禁用 (agent.validation_enabled=false)"
                ],
                # 兼容旧字段
                total_repaired=repaired_count,
                total_remaining=0,
                newly_introduced=0,
                passed=True,
                remaining_issue_ids=[],
                new_issue_ids=[],
                notes="validation 被配置禁用 (agent.validation_enabled=false)",
            )
            state["remaining_issues"] = []
            state["new_introduced_issues"] = []
            if "validation_iterations" not in state or state.get("validation_iterations") in (None, 0):
                state["validation_iterations"] = 1
            return state

        doc: Any = self._validate_state_field(state, "repaired_document")
        if not isinstance(doc, StructuredDocument):
            raise AgentError(
                "repaired_document 不是 StructuredDocument 实例",
                context={"type": type(doc).__name__},
            )

        original_issues: list[ReviewIssue] = state.get("review_issues") or []
        style_profile: Optional[dict] = state.get("style_profile")

        # ---- 重新审查 repaired_document ----
        recheck_issues = self._recheck_document(doc, style_profile)
        logger.info(
            "[Validation] 复检完成 | original=%d | recheck=%d",
            len(original_issues), len(recheck_issues),
        )

        # ---- 对比分析 ----
        summary, remaining, newly_intro = self._analyze(
            original_issues, recheck_issues, state.get("repair_actions") or [],
        )

        # ---- 停止策略：发现新引入问题时终止迭代 ----
        notes_parts = []
        if summary.newly_introduced > 0 and self.config.agent.stop_validation_on_new_issues:
            notes_parts.append(
                f"检测到 {summary.newly_introduced} 个新引入问题，"
                "按 stop_validation_on_new_issues 策略停止继续迭代"
            )

        passed = summary.total_remaining == 0 and summary.newly_introduced == 0

        # 计算已修复 issue 列表：original_issues 中被成功修复的（指纹未出现在 remaining）
        remain_fp = {_issue_fingerprint(i) for i in remaining}
        fixed_issues: list[ReviewIssue] = []
        for oi in original_issues:
            if _issue_fingerprint(oi) not in remain_fp:
                fixed_issues.append(oi)

        max_it = getattr(self.config, "validation", None)
        max_iterations: int = (
            int(max_it.max_iterations)
            if (max_it is not None and hasattr(max_it, "max_iterations"))
            else int(getattr(self.config.agent, "max_review_iterations", 3))
        )
        current_iteration: int = int(state.get("validation_iterations") or 1)

        # 建议：根据残留/新引入问题给提示
        suggestions: list[str] = []
        if summary.total_remaining > 0:
            suggestions.append(
                f"仍有 {summary.total_remaining} 个问题未解决，建议人工复核或继续迭代修复"
            )
        if summary.newly_introduced > 0:
            suggestions.append(
                f"发现 {summary.newly_introduced} 个新引入问题，建议撤销最近的修复动作"
            )
        if not suggestions and passed:
            suggestions.append("文档通过复检，可以交付。")

        validation_result = ValidationResult(
            # 标准字段
            pass_flag=passed,
            fixed_issue_count=summary.total_repaired,
            remaining_issue_count=summary.total_remaining,
            new_issue_count=summary.newly_introduced,
            fixed_issues=fixed_issues,
            remaining_issues=remaining,
            new_issues=newly_intro,
            max_iterations=max_iterations,
            current_iteration=current_iteration,
            improvement_suggestions=suggestions,
            # 兼容旧字段
            total_repaired=summary.total_repaired,
            total_remaining=summary.total_remaining,
            newly_introduced=summary.newly_introduced,
            passed=passed,
            remaining_issue_ids=[i["issue_id"] for i in remaining],
            new_issue_ids=[i["issue_id"] for i in newly_intro],
            notes="; ".join(notes_parts) if notes_parts else None,
        )

        # 写入状态
        state["validation_result"] = validation_result
        state["remaining_issues"] = remaining
        state["new_introduced_issues"] = newly_intro
        if "validation_iterations" not in state or state.get("validation_iterations") in (None, 0):
            state["validation_iterations"] = current_iteration
        else:
            state["validation_iterations"] = int(state["validation_iterations"])

        logger.info(
            "[Validation] 结果: repaired=%d / remaining=%d / new=%d / passed=%s",
            summary.total_repaired, summary.total_remaining,
            summary.newly_introduced, passed,
        )
        return state

    # ============================================================
    # 辅助方法
    # ============================================================
    def _recheck_document(
        self,
        doc: StructuredDocument,
        style_profile: Optional[dict],
    ) -> list[ReviewIssue]:
        """对 repaired_document 重新执行三类检查"""
        issues: list[ReviewIssue] = []
        try:
            fmt = self._fmt_cls(style_profile)
            issues.extend(fmt.check(doc))
        except Exception as e:
            logger.warning("FormatChecker 复检失败: %s", e)

        try:
            stc = self._str_cls(style_profile)
            issues.extend(stc.check(doc))
        except Exception as e:
            logger.warning("StructureChecker 复检失败: %s", e)

        try:
            ctn = self._cnt_cls(None, [])
            # ContentChecker 不需要 terminology 也能跑错别字检查
            issues.extend(ctn.check(doc))
        except Exception as e:
            logger.warning("ContentChecker 复检失败: %s", e)
        return issues

    def _analyze(
        self,
        original_issues: list[ReviewIssue],
        recheck_issues: list[ReviewIssue],
        repair_actions: list[dict],
    ) -> tuple[_ValidationSummary, list[ReviewIssue], list[ReviewIssue]]:
        """
        对比 original_issues 与 recheck_issues，计算修复结果。

        Returns:
            (summary, remaining_issues, newly_introduced_issues)
        """
        # Step 1: 计算已成功修复的动作数
        success_actions = [a for a in repair_actions if a.get("success")]
        total_repaired = len(success_actions)

        # Step 2: 标记 recheck 中哪些是"残留的 original issue"
        remaining: list[ReviewIssue] = []
        matched_original = set()

        for r in recheck_issues:
            is_remaining = False
            for idx, orig in enumerate(original_issues):
                if idx in matched_original:
                    continue
                if _issues_match(orig, r):
                    matched_original.add(idx)
                    remaining.append(r)
                    is_remaining = True
                    break

        # Step 3: 未被匹配到的 recheck issue 视为新引入
        remaining_ids = {id(i) for i in remaining}
        newly_introduced: list[ReviewIssue] = [
            i for i in recheck_issues if id(i) not in remaining_ids
        ]

        # Step 4: 对于 original 中 auto_repairable=False 或成功批注过（不修改）的
        # STRUCTURE 类问题，不应计入 remaining。我们基于 issue 自身的 auto_repairable
        # 与严重程度判断是否计入 "需要修复但未成功"。
        effectively_remaining: list[ReviewIssue] = []
        for r in remaining:
            # 通过 category 确定对应 original issue 的 auto_repairable 预期
            orig_match = None
            for orig in original_issues:
                if _issues_match(orig, r):
                    orig_match = orig
                    break
            if orig_match is None or orig_match.get("auto_repairable", True):
                effectively_remaining.append(r)
            # auto_repairable=False 的 issue（如 STRUCTURE_MISSING_SECTION）
            # 走 CommentOnlyRepairer，属于"按设计保留"，不视为残留

        summary = _ValidationSummary(
            total_in_original=len(original_issues),
            total_recheck_issues=len(recheck_issues),
            total_repaired=total_repaired,
            total_remaining=len(effectively_remaining),
            newly_introduced=len(newly_introduced),
            passed=(len(effectively_remaining) == 0 and len(newly_introduced) == 0),
        )
        return summary, effectively_remaining, newly_introduced

    def _build_summary(self, state: DocGuardState) -> str:
        vr = state.get("validation_result") or {}
        return (
            f"修复={vr.get('total_repaired', 0)} "
            f"残留={vr.get('total_remaining', 0)} "
            f"新引入={vr.get('newly_introduced', 0)} "
            f"通过={'✅' if vr.get('passed') else '❌'}"
        )
