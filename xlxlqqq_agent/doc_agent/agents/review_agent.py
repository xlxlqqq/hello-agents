"""
DocGuard Agent - Review Agent
===============================

职责：
1. 读取 Parser 的 StructuredDocument
2. 读取 Retrieval 的 style_profile + retrieved_documents + terminology_list
3. 执行三类检查（格式 / 结构 / 内容），产出 ReviewIssue 列表
4. 汇总生成 ReviewReport（问题统计 + 质量评分）
5. 写入 state.review_issues / review_report

检查引擎分层：
  1. 格式检查：对比 style_profile（正文字体/字号、标题字体/字号、行距、首行缩进）
  2. 结构检查：对比 expected_sections（章节完整性、标题层级连续性）
  3. 内容检查：
     - 错别字规则库（内置常见错词）
     - 术语一致性（对比 terminology_list 常见错写变体）

设计要点：
- 所有检查为纯规则引擎，不依赖 LLM（Phase 5 可扩展 LLM 增强）
- 支持 style_profile=None：跳过规范对比，仅执行通用规则（如错别字）
- 支持 terminology_list=[]：跳过术语一致性检查
- 生成的 Issue 标注 auto_repairable，供 Phase 6 Repair Agent 决策
- 质量评分 0-100：基础 100，按严重程度扣减

输入 state 字段：
- parsed_document: 必填
- style_profile: 可选（None 时仅执行通用规则）
- terminology_list: 可选（空列表时跳过术语检查）
- retrieved_documents: 可选（仅用于置信度参考，Phase 5 扩展）

输出 state 字段：
- review_issues: list[ReviewIssue]
- review_report: ReviewReport
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agents.base import BaseAgent
from core.config import DocGuardConfig
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import DocGuardState, Location, ReviewIssue, ReviewReport, StyleProfile
from document.models import IssueCategory, IssueSeverity, Paragraph, StructuredDocument


logger = get_logger("agents.review_agent")


# ============================================================
# 辅助函数与常量
# ============================================================

# 常见错别字规则库：(错误写法, 正确写法, 严重程度)
COMMON_TYPO_RULES: list[tuple[str, str, str]] = [
    # 格式类（DocGuard Demo 文档故意包含的错误）
    ("格试", "格式", IssueSeverity.MAJOR),
    # 常见中文错误
    ("帐户", "账户", IssueSeverity.MINOR),
    ("帐号", "账号", IssueSeverity.MINOR),
    ("既然", "既然", IssueSeverity.MINOR),
    ("部署", "部署", IssueSeverity.MINOR),
    ("按装", "安装", IssueSeverity.MAJOR),
    ("必竞", "毕竟", IssueSeverity.MINOR),
    ("既使", "即使", IssueSeverity.MINOR),
    ("做用", "作用", IssueSeverity.MINOR),
    ("幅射", "辐射", IssueSeverity.MAJOR),
    ("迫不急待", "迫不及待", IssueSeverity.MINOR),
    ("一愁莫展", "一筹莫展", IssueSeverity.MINOR),
]

# 严重程度扣分
SEVERITY_SCORE_DEDUCTION = {
    IssueSeverity.CRITICAL: 10.0,
    IssueSeverity.MAJOR: 5.0,
    IssueSeverity.MINOR: 2.0,
    IssueSeverity.INFO: 0.5,
}


def _issue_id() -> str:
    """生成 issue 唯一 ID"""
    return f"issue_{uuid.uuid4().hex[:12]}"


def _make_location(
    *,
    paragraph_id: Optional[str] = None,
    paragraph_index: Optional[int] = None,
    run_index: Optional[int] = None,
    table_id: Optional[str] = None,
    row: Optional[int] = None,
    col: Optional[int] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
    text_snippet: Optional[str] = None,
) -> Location:
    """构造 Location 对象（所有字段可选）"""
    return Location(
        paragraph_id=paragraph_id,
        paragraph_index=paragraph_index,
        run_index=run_index,
        table_id=table_id,
        row=row,
        col=col,
        char_start=char_start,
        char_end=char_end,
        text_snippet=text_snippet,
    )


# ============================================================
# 规则引擎：格式检查
# ============================================================

@dataclass
class FormatCheckResult:
    issues: list[ReviewIssue] = field(default_factory=list)
    paragraphs_checked: int = 0
    format_issues_found: int = 0


class FormatChecker:
    """
    格式规则引擎：对比 style_profile 规范检查段落格式。

    - 正文段落：对比 body_font / body_size_pt / line_spacing / first_line_indent_pt
    - 标题段落：对比 heading_font / heading_size_pt
    - style_profile 某字段为 None 时跳过对应项对比
    """

    def __init__(
        self,
        style_profile: Optional[StyleProfile],
        tolerance_pt: float = 0.5,
    ) -> None:
        self.profile = style_profile
        self.tolerance_pt = tolerance_pt  # 字号容忍误差（Pt）
        self.logger = get_logger("review.format")

    def check(self, doc: StructuredDocument) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if doc is None:
            return issues

        for para in doc.paragraphs:
            # 标题 vs 正文 分别检查
            if para.heading_level is not None:
                issues.extend(self._check_heading(para))
            else:
                # 正文：跳过表格内段落（表格有独立检查）
                if not para.in_table:
                    issues.extend(self._check_body(para))

        # 检查表格格式（Phase 5 扩展，INFO 级占位）
        self.logger.info(
            "格式检查完成 | 段落=%d | 问题=%d",
            len(doc.paragraphs), len(issues),
        )
        return issues

    # --- 正文段落检查 ---
    def _check_body(self, para: Paragraph) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if self.profile is None:
            return issues

        snippet = para.text[:40]
        # ---- 正文字体检查 ----
        expected_font = self.profile.get("body_font")
        if expected_font:
            for r in para.runs:
                run_font = r.style.name or r.style.name_east_asian
                if run_font and run_font != expected_font:
                    issues.append(ReviewIssue(
                        issue_id=_issue_id(),
                        category=IssueCategory.FORMAT_FONT,
                        severity=IssueSeverity.MAJOR,
                        title=f"正文字体不统一",
                        description=(
                            f"正文字体应为『{expected_font}』，"
                            f"实际为『{run_font}』"
                        ),
                        location=_make_location(
                            paragraph_id=para.paragraph_id,
                            paragraph_index=para.paragraph_index,
                            run_index=r.run_index,
                            text_snippet=snippet,
                        ),
                        original_text=(r.text[:30] if r.text else None),
                        suggested_fix=f"将字体修改为 {expected_font}",
                        source="rule",
                        confidence=0.9,
                        auto_repairable=True,
                    ))

        # ---- 正文字号检查 ----
        expected_size = self.profile.get("body_size_pt")
        if expected_size is not None:
            for r in para.runs:
                run_size = r.style.size_pt
                if run_size is not None:
                    diff = abs(run_size - expected_size)
                    if diff > self.tolerance_pt:
                        issues.append(ReviewIssue(
                            issue_id=_issue_id(),
                            category=IssueCategory.FORMAT_SIZE,
                            severity=IssueSeverity.MAJOR,
                            title=f"正文字号不统一",
                            description=(
                                f"正文字号应为 {expected_size}pt，"
                                f"实际为 {run_size}pt（偏差 {diff:.1f}pt）"
                            ),
                            location=_make_location(
                                paragraph_id=para.paragraph_id,
                                paragraph_index=para.paragraph_index,
                                run_index=r.run_index,
                                text_snippet=snippet,
                            ),
                            original_text=(r.text[:30] if r.text else None),
                            suggested_fix=f"将字号修改为 {expected_size}pt",
                            source="rule",
                            confidence=0.9,
                            auto_repairable=True,
                        ))

        # ---- 行距检查 ----
        expected_line = self.profile.get("line_spacing")
        if expected_line is not None and para.style.line_spacing is not None:
            actual = para.style.line_spacing
            # "multiple" 规则通常是 1.5，对比允许 0.1 偏差
            if abs(actual - expected_line) > 0.15:
                issues.append(ReviewIssue(
                    issue_id=_issue_id(),
                    category=IssueCategory.FORMAT_SPACING,
                    severity=IssueSeverity.MINOR,
                    title=f"正文行距不符合规范",
                    description=(
                        f"正文行距应为 {expected_line}，实际为 {actual}"
                    ),
                    location=_make_location(
                        paragraph_id=para.paragraph_id,
                        paragraph_index=para.paragraph_index,
                        text_snippet=snippet,
                    ),
                    original_text=(para.text[:30] if para.text else None),
                    suggested_fix=f"调整行距为 {expected_line}",
                    source="rule",
                    confidence=0.75,
                    auto_repairable=True,
                ))

        # ---- 首行缩进检查 ----
        expected_indent = self.profile.get("first_line_indent_pt")
        if (
            expected_indent is not None
            and para.style.first_line_indent_pt is not None
        ):
            diff = abs(para.style.first_line_indent_pt - expected_indent)
            if diff > self.tolerance_pt:
                issues.append(ReviewIssue(
                    issue_id=_issue_id(),
                    category=IssueCategory.FORMAT_INDENT,
                    severity=IssueSeverity.MINOR,
                    title=f"首行缩进不符合规范",
                    description=(
                        f"首行缩进应为 {expected_indent}pt，"
                        f"实际为 {para.style.first_line_indent_pt}pt"
                    ),
                    location=_make_location(
                        paragraph_id=para.paragraph_id,
                        paragraph_index=para.paragraph_index,
                        text_snippet=snippet,
                    ),
                    original_text=(para.text[:30] if para.text else None),
                    suggested_fix=f"调整首行缩进为 {expected_indent}pt",
                    source="rule",
                    confidence=0.7,
                    auto_repairable=True,
                ))

        return issues

    # --- 标题检查 ---
    def _check_heading(self, para: Paragraph) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if self.profile is None:
            return issues

        snippet = para.text[:40]
        # 标题字体
        expected_font = self.profile.get("heading_font")
        if expected_font:
            for r in para.runs:
                run_font = r.style.name or r.style.name_east_asian
                if run_font and run_font != expected_font:
                    issues.append(ReviewIssue(
                        issue_id=_issue_id(),
                        category=IssueCategory.FORMAT_FONT,
                        severity=IssueSeverity.MAJOR,
                        title=f"H{para.heading_level} 标题字体不规范",
                        description=(
                            f"标题字体应为『{expected_font}』，"
                            f"实际为『{run_font}』"
                        ),
                        location=_make_location(
                            paragraph_id=para.paragraph_id,
                            paragraph_index=para.paragraph_index,
                            run_index=r.run_index,
                            text_snippet=snippet,
                        ),
                        original_text=(r.text[:30] if r.text else None),
                        suggested_fix=f"将标题字体修改为 {expected_font}",
                        source="rule",
                        confidence=0.85,
                        auto_repairable=True,
                    ))

        # 标题字号
        expected_size = self.profile.get("heading_size_pt")
        if expected_size is not None:
            for r in para.runs:
                run_size = r.style.size_pt
                if run_size is not None:
                    diff = abs(run_size - expected_size)
                    if diff > self.tolerance_pt:
                        issues.append(ReviewIssue(
                            issue_id=_issue_id(),
                            category=IssueCategory.FORMAT_SIZE,
                            severity=IssueSeverity.MAJOR,
                            title=f"H{para.heading_level} 标题字号不规范",
                            description=(
                                f"标题字号应为 {expected_size}pt，"
                                f"实际为 {run_size}pt（偏差 {diff:.1f}pt）"
                            ),
                            location=_make_location(
                                paragraph_id=para.paragraph_id,
                                paragraph_index=para.paragraph_index,
                                run_index=r.run_index,
                                text_snippet=snippet,
                            ),
                            original_text=(r.text[:30] if r.text else None),
                            suggested_fix=f"将标题字号修改为 {expected_size}pt",
                            source="rule",
                            confidence=0.85,
                            auto_repairable=True,
                        ))

        return issues


# ============================================================
# 规则引擎：结构检查
# ============================================================

class StructureChecker:
    """
    结构规则引擎：检查章节完整性 + 标题层级连续性。

    - 章节完整性：对比 expected_sections（包含匹配，容错同义词）
    - 标题层级连续性：不允许 H1 → H3 / H2 → H4 等跳跃
    """

    def __init__(self, style_profile: Optional[StyleProfile]) -> None:
        self.profile = style_profile
        self.logger = get_logger("review.structure")

    def check(self, doc: StructuredDocument) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if doc is None:
            return issues

        issues.extend(self._check_sections(doc))
        issues.extend(self._check_heading_continuity(doc))

        self.logger.info(
            "结构检查完成 | 章节=%d | 问题=%d",
            len(doc.get_heading_outline()), len(issues),
        )
        return issues

    # ---- 章节完整性 ----
    def _check_sections(self, doc: StructuredDocument) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if self.profile is None:
            return issues

        expected = self.profile.get("expected_sections") or []
        if not expected:
            return issues

        outline = doc.get_heading_outline()
        current_heads = [h["text"].strip() for h in outline]
        current_head_lower = [h.lower() for h in current_heads]

        for sec in expected:
            sec_stripped = sec.strip()
            if not sec_stripped:
                continue
            # 子串匹配（容错："1.1 架构概述" ⊂ "1.1 系统架构概述" 即可）
            matched = False
            sec_lower = sec_stripped.lower()
            for h in current_head_lower:
                if sec_lower in h or h in sec_lower:
                    matched = True
                    break
            if not matched:
                issues.append(ReviewIssue(
                    issue_id=_issue_id(),
                    category=IssueCategory.STRUCTURE_MISSING_SECTION,
                    severity=IssueSeverity.MAJOR,
                    title=f"缺失建议章节：{sec_stripped}",
                    description=(
                        f"参考历史文档，建议补充章节『{sec_stripped}』，"
                        f"当前文档共 {len(current_heads)} 个标题"
                    ),
                    location=_make_location(
                        paragraph_index=None,
                        text_snippet=(" | ".join(current_heads[:5]) + ("..." if len(current_heads) > 5 else "")),
                    ),
                    original_text=None,
                    suggested_fix=f"按企业规范补充章节：{sec_stripped}",
                    source="rag",
                    confidence=0.7,
                    auto_repairable=False,  # 结构缺失需要人工补充
                ))
        return issues

    # ---- 标题层级连续性 ----
    def _check_heading_continuity(self, doc: StructuredDocument) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        outline = doc.get_heading_outline()
        if len(outline) < 2:
            return issues

        prev_level = outline[0]["level"]
        for h in outline[1:]:
            cur_level = h["level"]
            jump = cur_level - prev_level
            if jump > 1:
                issues.append(ReviewIssue(
                    issue_id=_issue_id(),
                    category=IssueCategory.FORMAT_HEADING_LEVEL,
                    severity=IssueSeverity.MAJOR,
                    title=f"标题层级跳跃：H{prev_level} → H{cur_level}",
                    description=(
                        f"标题层级不连续，从 H{prev_level} 直接跳到 H{cur_level}，"
                        f"建议先出现 H{prev_level + 1}"
                    ),
                    location=_make_location(
                        paragraph_id=h["paragraph_id"],
                        paragraph_index=h["paragraph_index"],
                        text_snippet=h["text"][:40],
                    ),
                    original_text=h["text"],
                    suggested_fix=(
                        f"在『{h['text']}』之前补充 H{prev_level + 1} 级标题，"
                        f"或调整当前标题层级"
                    ),
                    source="rule",
                    confidence=0.95,
                    auto_repairable=False,  # 语义相关，不自动修复
                ))
            prev_level = cur_level
        return issues


# ============================================================
# 规则引擎：内容检查
# ============================================================

class ContentChecker:
    """
    内容规则引擎：错别字 + 术语一致性。

    - 错别字：COMMON_TYPO_RULES 内置错别字词表扫描
    - 术语一致性：检查 terminology_list 术语是否使用了企业规范写法
      （非规范写法检测：将术语规范化为"无空格/全半角"后，对比段落中的近邻写法）
    """

    def __init__(
        self,
        terminology_list: Optional[list[str]] = None,
        extra_typo_rules: Optional[list[tuple[str, str, str]]] = None,
    ) -> None:
        self.terminology = terminology_list or []
        self.typo_rules: list[tuple[str, str, str]] = list(COMMON_TYPO_RULES)
        if extra_typo_rules:
            self.typo_rules.extend(extra_typo_rules)
        self.logger = get_logger("review.content")

    def check(self, doc: StructuredDocument) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if doc is None:
            return issues

        for para in doc.paragraphs:
            if not para.text.strip():
                continue
            if para.in_table:
                # 表格内文本由 Phase 5 扩展，此处跳过减少误报
                continue
            issues.extend(self._check_typos(para))
            issues.extend(self._check_terminology(para))

        self.logger.info(
            "内容检查完成 | 段落=%d | 问题=%d",
            len(doc.paragraphs), len(issues),
        )
        return issues

    # ---- 错别字扫描 ----
    def _check_typos(self, para: Paragraph) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        text = para.text
        for wrong, right, severity in self.typo_rules:
            if wrong not in text:
                continue
            # 找到所有出现位置
            start = 0
            while True:
                idx = text.find(wrong, start)
                if idx == -1:
                    break
                snippet_start = max(0, idx - 10)
                snippet_end = min(len(text), idx + len(wrong) + 10)
                snippet = text[snippet_start:snippet_end]
                issues.append(ReviewIssue(
                    issue_id=_issue_id(),
                    category=IssueCategory.CONTENT_TYPO,
                    severity=severity,
                    title=f"疑似错别字：『{wrong}』应为『{right}』",
                    description=(
                        f"检测到疑似错别字『{wrong}』，"
                        f"根据企业通用规范应为『{right}』"
                    ),
                    location=_make_location(
                        paragraph_id=para.paragraph_id,
                        paragraph_index=para.paragraph_index,
                        char_start=idx,
                        char_end=idx + len(wrong),
                        text_snippet=snippet,
                    ),
                    original_text=wrong,
                    suggested_fix=f"替换为『{right}』",
                    source="rule",
                    confidence=0.88,
                    auto_repairable=True,
                ))
                start = idx + 1
        return issues

    # ---- 术语一致性检查 ----
    def _check_terminology(self, para: Paragraph) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if not self.terminology:
            return issues
        text = para.text
        for term in self.terminology:
            term = term.strip()
            if not term or len(term) < 2:
                continue
            # 简单的"术语错误用法"检测：
            #   - 英文术语大小写不一致（如 docguard vs DocGuard）
            #   - 全角半角变体
            # 更复杂的语义近邻将在 Phase 5 通过 LLM 增强
            lower_text = text.lower()
            lower_term = term.lower()
            if lower_term not in lower_text:
                continue
            # 找出所有出现位置，对比大小写/字符
            pos = 0
            while True:
                idx = lower_text.find(lower_term, pos)
                if idx == -1:
                    break
                actual = text[idx:idx + len(term)]
                if actual != term:
                    snippet_start = max(0, idx - 10)
                    snippet_end = min(len(text), idx + len(term) + 10)
                    issues.append(ReviewIssue(
                        issue_id=_issue_id(),
                        category=IssueCategory.CONTENT_TERMINOLOGY,
                        severity=IssueSeverity.MINOR,
                        title=f"术语写法不一致：『{actual}』应为『{term}』",
                        description=(
                            f"企业术语『{term}』在文档中写法为『{actual}』，"
                            f"请确认大小写/字符与规范一致"
                        ),
                        location=_make_location(
                            paragraph_id=para.paragraph_id,
                            paragraph_index=para.paragraph_index,
                            char_start=idx,
                            char_end=idx + len(term),
                            text_snippet=text[snippet_start:snippet_end],
                        ),
                        original_text=actual,
                        suggested_fix=f"替换为企业规范写法『{term}』",
                        source="rag",
                        confidence=0.8,
                        auto_repairable=True,
                    ))
                pos = idx + 1
        return issues


# ============================================================
# 质量评分
# ============================================================

def calculate_quality_score(issues: list[ReviewIssue]) -> float:
    """
    根据问题严重性计算文档质量分（0-100）。

    规则：基础 100 分，按严重程度扣减，最低 0 分。
    """
    score = 100.0
    for issue in issues:
        sev = issue.get("severity", IssueSeverity.INFO)
        deduction = SEVERITY_SCORE_DEDUCTION.get(sev, 0.5)
        score -= deduction
    return round(max(0.0, min(100.0, score)), 1)


def summarize_issues(issues: list[ReviewIssue]) -> ReviewReport:
    """汇总生成 ReviewReport"""
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for issue in issues:
        cat = issue.get("category", "unknown")
        sev = issue.get("severity", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1

    quality = calculate_quality_score(issues)

    # 总体改进建议（Top 3 类别）
    suggestions: list[str] = []
    sorted_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    for cat, cnt in sorted_cats[:3]:
        msg_map: dict[str, str] = {
            IssueCategory.FORMAT_FONT: "统一文档字体（优先检查正文 Run 的 font 属性）",
            IssueCategory.FORMAT_SIZE: "统一文档字号（正文/标题分别设置）",
            IssueCategory.FORMAT_SPACING: "统一行距设置（检查 ParagraphFormat.line_spacing）",
            IssueCategory.FORMAT_INDENT: "统一首行缩进（通常中文 24pt = 2 字符）",
            IssueCategory.FORMAT_HEADING_LEVEL: "调整标题层级，避免越级（H1→H3 等）",
            IssueCategory.STRUCTURE_MISSING_SECTION: "按企业章节规范补充缺失章节",
            IssueCategory.CONTENT_TYPO: "全文校对错别字，重点检查 COMMON_TYPO_RULES 词库",
            IssueCategory.CONTENT_TERMINOLOGY: "统一术语大小写与写法，使用企业术语库",
            IssueCategory.CONTENT_WRONG_WORD: "检查搭配/用词错误，必要时请人工复核",
            IssueCategory.CONTENT_GRAMMAR: "检查语法，必要时请人工复核",
        }
        suggestion = msg_map.get(cat, f"重点关注『{cat}』类别问题")
        suggestions.append(f"{suggestion}（{cnt} 处）")

    summary = (
        f"共发现 {len(issues)} 个问题，"
        f"其中 Critical {by_severity.get('critical', 0)} 个、"
        f"Major {by_severity.get('major', 0)} 个、"
        f"Minor {by_severity.get('minor', 0)} 个。"
        f"质量评分 {quality:.1f}/100。"
    )

    return ReviewReport(
        task_id="",
        total_issues=len(issues),
        by_category=by_category,
        by_severity=by_severity,
        issues=issues,
        quality_score=quality,
        summary=summary,
        suggestions=suggestions,
    )


# ============================================================
# Review Agent
# ============================================================

class ReviewAgent(BaseAgent):
    """
    Review Agent：文档审查。

    执行三类规则检查（格式 / 结构 / 内容），产出 ReviewIssue 列表
    并汇总生成 ReviewReport。

    依赖注入三个 Checker，便于测试 mock。
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient],
        config: DocGuardConfig,
        logger=None,
        *,
        format_checker: Optional[FormatChecker] = None,
        structure_checker: Optional[StructureChecker] = None,
        content_checker: Optional[ContentChecker] = None,
    ) -> None:
        super().__init__(llm_client, config, logger or get_logger("agents.review_agent"))
        self._format_checker = format_checker
        self._structure_checker = structure_checker
        self._content_checker = content_checker

    def agent_name(self) -> str:
        return "review_agent"

    async def execute(self, state: DocGuardState) -> DocGuardState:
        """
        执行文档审查。

        上游解析/检索失败时：降级为"通用规则检查"（跳过 style_profile 对比），
        不中断工作流（仍执行错别字检查）。
        """
        parsed_doc = self._validate_state_field(state, "parsed_document")

        # 上游状态判断（parse_success 为 False 也允许审查，降低误报）
        parse_ok = state.get("parse_success", False)
        if not parse_ok:
            self.logger.warning("[Review] 上游解析失败，降级为通用规则检查")

        style_profile = state.get("style_profile")
        terminology = state.get("terminology_list") or []

        self.logger.info(
            "[Review] 开始审查 | 文档=%s | style_profile=%s | terms=%d",
            parsed_doc.filename,
            "有" if style_profile else "无",
            len(terminology),
        )

        # 初始化检查器（支持 DI，否则按需创建）
        format_c = self._format_checker or FormatChecker(style_profile)
        structure_c = self._structure_checker or StructureChecker(style_profile)
        content_c = self._content_checker or ContentChecker(terminology)

        issues: list[ReviewIssue] = []
        try:
            issues.extend(format_c.check(parsed_doc))
        except Exception as e:
            self.logger.warning("[Review] 格式检查失败（跳过）: %s", e, exc_info=True)

        try:
            issues.extend(structure_c.check(parsed_doc))
        except Exception as e:
            self.logger.warning("[Review] 结构检查失败（跳过）: %s", e, exc_info=True)

        try:
            issues.extend(content_c.check(parsed_doc))
        except Exception as e:
            self.logger.warning("[Review] 内容检查失败（跳过）: %s", e, exc_info=True)

        # 汇总报告
        report = summarize_issues(issues)
        report["task_id"] = state.get("task_id", "")

        # 写入 state
        state["review_issues"] = issues
        state["review_report"] = report

        self.logger.info(
            "[Review] 审查完成 | 总问题=%d | 格式=%d | 结构=%d | 内容=%d | 质量=%.1f",
            len(issues),
            report["by_category"].get(IssueCategory.FORMAT_FONT, 0)
            + report["by_category"].get(IssueCategory.FORMAT_SIZE, 0)
            + report["by_category"].get(IssueCategory.FORMAT_SPACING, 0)
            + report["by_category"].get(IssueCategory.FORMAT_INDENT, 0)
            + report["by_category"].get(IssueCategory.FORMAT_HEADING_LEVEL, 0),
            report["by_category"].get(IssueCategory.STRUCTURE_MISSING_SECTION, 0)
            + report["by_category"].get(IssueCategory.STRUCTURE_ORDER, 0),
            report["by_category"].get(IssueCategory.CONTENT_TYPO, 0)
            + report["by_category"].get(IssueCategory.CONTENT_TERMINOLOGY, 0)
            + report["by_category"].get(IssueCategory.CONTENT_WRONG_WORD, 0)
            + report["by_category"].get(IssueCategory.CONTENT_GRAMMAR, 0),
            report["quality_score"],
        )
        return state

    def _build_summary(self, state: DocGuardState) -> str:
        report = state.get("review_report")
        if not report:
            return "issues=0, quality=N/A"
        return (
            f"issues={report.get('total_issues', 0)}, "
            f"quality={report.get('quality_score', 'N/A')}, "
            f"critical={report.get('by_severity', {}).get('critical', 0)}, "
            f"major={report.get('by_severity', {}).get('major', 0)}, "
            f"minor={report.get('by_severity', {}).get('minor', 0)}"
        )
