"""
DocGuard Agent - LangGraph 共享状态定义
========================================

设计要点：
1. 使用 TypedDict 定义 LangGraph State，所有 Agent 共享同一状态对象
2. 状态字段分为 5 个阶段：输入 / 解析 / 检索 / 审查 / 修复 / 验证 / 输出
3. Optional 字段允许中间状态缺失，Agent 负责填充
4. 引用 document.models 中的结构化对象，避免数据重复定义
5. 包含执行元数据（步骤日志、耗时、错误），便于追踪
"""

from typing import Any, Optional, TypedDict

# ============================================================
# 执行元数据
# ============================================================


class StepLog(TypedDict):
    """单步执行日志"""

    step: str               # 步骤名（如 "parser_agent"）
    success: bool           # 是否成功
    started_at: str         # ISO8601 开始时间
    elapsed_seconds: float  # 耗时（秒）
    error: Optional[str]    # 失败时的错误信息
    summary: Optional[str]  # 步骤摘要（如 "解析得到 42 段落、3 表格"）


# ============================================================
# 检索阶段输出（轻量结构，避免直接依赖 knowledge 模块的复杂类型）
# ============================================================


class RetrievedDoc(TypedDict):
    """检索到的相似历史文档"""

    doc_id: str
    filename: str
    content_snippet: str       # 内容片段
    similarity_score: float    # 相似度 0-1
    metadata: dict[str, Any]   # 额外元数据


class StyleProfile(TypedDict):
    """文档风格画像（从历史文档学习）"""

    # 结构规范
    expected_sections: list[str]           # 期望章节列表
    # 格式规范
    heading_font: Optional[str]
    heading_size_pt: Optional[float]
    body_font: Optional[str]
    body_size_pt: Optional[float]
    line_spacing: Optional[float]
    first_line_indent_pt: Optional[float]
    # 术语库
    terminology: list[str]                 # 企业术语列表
    # 统计来源
    sample_doc_count: int                  # 学习样本数
    raw_profile_text: Optional[str]        # LLM 生成的原始画像文本


# ============================================================
# 审查阶段输出
# ============================================================


class Location(TypedDict):
    """问题定位"""

    paragraph_id: Optional[str]
    paragraph_index: Optional[int]        # 在所有段落中的全局索引
    run_index: Optional[int]              # 在段落中的 run 索引
    table_id: Optional[str]
    row: Optional[int]
    col: Optional[int]
    char_start: Optional[int]             # 在段落文本中的起始字符位置
    char_end: Optional[int]
    text_snippet: Optional[str]           # 问题上下文文本


class ReviewIssue(TypedDict):
    """审查发现的问题"""

    issue_id: str
    category: str                          # IssueCategory 枚举值
    severity: str                          # IssueSeverity 枚举值
    title: str
    description: str
    location: Location
    original_text: Optional[str]
    suggested_fix: Optional[str]
    source: str                            # "rule" / "rag" / "llm"
    confidence: float                      # 0-1
    auto_repairable: bool


class ReviewReport(TypedDict):
    """审查报告"""

    task_id: str
    total_issues: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
    issues: list[ReviewIssue]
    quality_score: float                   # 0-100
    summary: str                           # LLM 生成的总结
    suggestions: list[str]                 # 整体改进建议


# ============================================================
# 修复阶段输出
# ============================================================


class RepairAction(TypedDict):
    """修复动作"""

    action_id: str
    issue_id: str                          # 对应的 ReviewIssue ID
    repair_type: str                       # RepairType 枚举值
    location: Location
    original_value: Optional[str]
    new_value: Optional[str]
    executed: bool
    success: bool
    error_message: Optional[str]
    annotated: bool                        # 是否在文档中标注


class ValidationResult(TypedDict, total=False):
    """验证结果

    Phase 6 标准字段（total=False 兼容历史字段）：
    - 主视图：pass_flag / fixed / remaining / new / fixed_issues /
              remaining_issues / new_issues
    - 保留字段：passed(=pass_flag), total_repaired(=fixed),
              total_remaining(=remaining), newly_introduced(=new)
    """

    # 标准字段（测试 & 上层逻辑优先使用）
    pass_flag: bool                        # 是否通过验证
    fixed_issue_count: int                 # 已成功修复的问题数
    remaining_issue_count: int             # 仍存在的问题数
    new_issue_count: int                   # 修复引入的新问题数
    fixed_issues: list                     # 已修复的 ReviewIssue 列表
    remaining_issues: list                 # 残留的 ReviewIssue 列表
    new_issues: list                       # 新引入的 ReviewIssue 列表
    max_iterations: int                    # 允许的最大迭代次数
    current_iteration: int                 # 当前迭代次数
    improvement_suggestions: list[str]     # 复检改进建议

    # 兼容旧字段
    total_repaired: int                    # = fixed_issue_count
    total_remaining: int                   # = remaining_issue_count
    newly_introduced: int                  # = new_issue_count
    passed: bool                           # = pass_flag
    remaining_issue_ids: list[str]         # = [i.issue_id for i in remaining_issues]
    new_issue_ids: list[str]               # = [i.issue_id for i in new_issues]
    notes: Optional[str]                   # 附加说明


class RepairConfirmation(TypedDict):
    """
    HITL: 人工修复确认结果。

    Phase 6 Human-in-the-loop 机制：
    - critical / major 严重级别且 auto_repairable = True 的 Issue
      默认暂停修复，等待人工确认（CLI / Web 交互）。
    - 用户决策: approve(批准自动修复) / reject(跳过不修复) /
                approve_with_override(批准并覆写建议修复值)。
    - 配置 hitl.auto_approve_all = True 时跳过确认（默认开发模式）。
    """

    issue_id: str
    decision: str                          # "approve" | "reject" | "approve_with_override"
    override_fix: Optional[str]            # decision=approve_with_override 时覆写的 suggested_fix
    confirmed_by: str                      # 谁批准的（"user" / "system" / "auto"）
    confirmed_at: str                      # ISO8601 时间戳
    notes: Optional[str]                   # 人工备注


# ============================================================
# 主状态：DocGuardState
# ============================================================


class DocGuardState(TypedDict):
    """
    LangGraph 工作流共享状态。

    All agents share this state; each agent reads upstream fields and writes its own fields.
    """

    # ===== 输入 =====
    task_id: str                                  # 任务唯一 ID
    input_file_path: str                          # 输入文档路径（DOCX / PDF / PPT）
    user_requirements: Optional[str]              # 用户额外要求
    original_filename: Optional[str]              # 原始文件名（用于输出命名）
    input_format: Optional[str]                   # "docx" | "pdf" | "ppt"（自动推断或 --format 指定）

    # ===== 解析阶段 =====
    parsed_document: Optional[Any]                # StructuredDocument 实例（document.models）
    parse_success: bool
    parse_error: Optional[str]

    # ===== 检索阶段 =====
    retrieved_documents: list[RetrievedDoc]
    style_profile: Optional[StyleProfile]
    terminology_list: list[str]

    # ===== 规划阶段 =====
    review_plan: Optional[dict[str, Any]]         # 审查计划（可选，预留扩展）

    # ===== 审查阶段 =====
    review_issues: list[ReviewIssue]
    review_report: Optional[ReviewReport]

    # ===== HITL 阶段（Human-in-the-loop） =====
    hitl_required: bool                           # 是否需要人工确认（有 critical/major issue 需确认）
    hitl_completed: bool                          # 人工确认是否已完成
    repair_confirmations: list[RepairConfirmation]  # 人工确认结果

    # ===== 修复阶段 =====
    repaired_document: Optional[Any]              # 修复后的 StructuredDocument
    repair_actions: list[RepairAction]
    repair_success: bool
    repair_error: Optional[str]

    # ===== 验证阶段 =====
    validation_result: Optional[ValidationResult]
    remaining_issues: list[ReviewIssue]           # 修复后仍存在的问题
    new_introduced_issues: list[ReviewIssue]      # 修复新引入的问题

    # ===== 输出 =====
    output_docx_path: Optional[str]               # 修复后 DOCX 路径（非 docx 输入也导出为 docx）
    output_report_json_path: Optional[str]        # JSON 报告路径
    output_report_html_path: Optional[str]        # HTML 报告路径

    # ===== 执行元数据 =====
    current_step: str                             # 当前步骤名
    step_logs: list[StepLog]                      # 各步骤执行日志
    total_elapsed_seconds: float                  # 总耗时（秒）
    _repair_iterations: int                       # 修复-验证迭代计数（内部字段）


def create_initial_state(
    task_id: str,
    input_file_path: str,
    user_requirements: Optional[str] = None,
    original_filename: Optional[str] = None,
    input_format: Optional[str] = None,
    format_hint: Optional[str] = None,
    **_: Any,
) -> DocGuardState:
    """
    创建初始状态对象。

    各阶段字段初始化为空值，由对应 Agent 填充。

    Args:
        task_id: 任务 ID
        input_file_path: 输入文档绝对路径（DOCX / PDF / PPT）
        user_requirements: 用户额外要求
        original_filename: 原始文件名
        input_format: 显式指定格式（None 时按扩展名推断）

    Returns:
        初始化的 DocGuardState
    """
    # 自动推断 input_format（format_hint 优先级最高，为 Phase 6 多格式工作流入口保留）
    chosen_format: Optional[str] = input_format or format_hint
    if chosen_format is None and input_file_path:
        ext = input_file_path.lower().rsplit(".", 1)[-1] if "." in input_file_path else ""
        if ext == "docx":
            chosen_format = "docx"
        elif ext in ("pdf",):
            chosen_format = "pdf"
        elif ext in ("pptx", "ppt"):
            chosen_format = "ppt"
    input_format = chosen_format

    return DocGuardState(
        # 输入
        task_id=task_id,
        input_file_path=input_file_path,
        user_requirements=user_requirements,
        original_filename=original_filename,
        input_format=input_format,
        # 解析
        parsed_document=None,
        parse_success=False,
        parse_error=None,
        # 检索
        retrieved_documents=[],
        style_profile=None,
        terminology_list=[],
        # 规划
        review_plan=None,
        # 审查
        review_issues=[],
        review_report=None,
        # HITL
        hitl_required=False,
        hitl_completed=False,
        repair_confirmations=[],
        # 修复
        repaired_document=None,
        repair_actions=[],
        repair_success=False,
        repair_error=None,
        # 验证
        validation_result=None,
        remaining_issues=[],
        new_introduced_issues=[],
        # 输出
        output_docx_path=None,
        output_report_json_path=None,
        output_report_html_path=None,
        # 元数据
        current_step="init",
        step_logs=[],
        total_elapsed_seconds=0.0,
        _repair_iterations=0,
    )
