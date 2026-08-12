"""
DocGuard Agent - LangGraph 工作流编排
======================================

设计要点：
1. 使用 LangGraph StateGraph 构建 Agent 工作流
2. 每个 Agent 作为图节点，通过 _safe_execute 包装
3. 条件边实现分支路由（parser 失败 → END；issues=0 → skip repair/validation 等）
4. Phase 6 工作流：planner + parser + retrieval + review + hitl + repair + validation
5. repair→validation→repair 的迭代闭环（最多 max_iterations 次，默认 2）
6. 保留 Phase 2~5 build_*_graph() 工厂函数（向后兼容）
7. 提供 build_docguard_graph() 与 run_docguard_workflow() 作为统一入口

当前（Phase 6）六层工作流：
    START → planner → parser → [ok?]
                                  └─ yes → retrieval → review → [issues>0?]
                                                                   ├─ no  → END
                                                                   └─ yes → hitl → repair → validation
                                                                                                 ├─ pass(残留=0 或 达到迭代上限) → END
                                                                                                 └─ fail(有残留且未达上限) → hitl → repair → validation → ...
"""

from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.base import BaseAgent
from agents.hitl_agent import HitlAgent
from agents.parser_agent import ParserAgent
from agents.planner_agent import PlannerAgent
from agents.repair_agent import RepairAgent
from agents.retrieval_agent import RetrievalAgent
from agents.review_agent import ReviewAgent
from agents.validation_agent import ValidationAgent
from core.config import DocGuardConfig, get_config
from core.llm_client import LLMClient, create_llm_client
from core.logging_config import get_logger
from core.state import DocGuardState, ValidationResult

logger = get_logger("agents.workflow")


# ============================================================
# 路由函数
'''
定义状态机的路由函数：条件分支控制流
parser 成功后 → 进入 retrieval
parser 失败 → 结束
review 有问题 → 进入 hitl
review 没问题 → 结束
validation 通过 → 结束
validation 没通过但还没到上限 → 继续修复循环
'''
# ============================================================

def _route_after_parser(state: DocGuardState) -> str:
    """parser 之后路由：
    - parse_success=True → retrieval
    - parse_success=False → END
    """
    if state.get("parse_success") and state.get("parsed_document") is not None:
        return "retrieval"
    return "end"


def _route_after_review(state: DocGuardState) -> str:
    """review 之后路由：
    - review_issues 非空 → hitl
    - review_issues 为空 → END
    """
    issues = state.get("review_issues") or []
    if len(issues) > 0:
        return "hitl"  # 有问题 → 进入 HITL 修复，人工确认
    return "end"


def _route_after_repair(state: DocGuardState) -> str:
    """repair 之后路由：
    - always → validation（Phase 6 强制复检，确保闭环质量）
    """
    return "validation"


def _route_after_validation(state: DocGuardState) -> str:
    """validation 之后路由（迭代闭环）：
    - 若 validation_result 为 None 或 validation_result.pass_flag=True → END
    - 否则检查迭代次数：
        * 超过 max_iterations → END（记录残留）
        * 未超过 → hitl（再给一次修复机会）
    """
    # 类型注解， Optional[ValidationResult] 表示它的类型是 ValidationResult 或 None
    validation_result: Optional[ValidationResult] = state.get("validation_result")
    if validation_result is None:
        return "end"
    if validation_result.get("pass_flag"):
        return "end"

    # 最多允许2次迭代闭环（可通过配置调整）
    max_iter = (
        # 从validation_result中读max_iterations字段，读到了就用它，否则默认2
        validation_result.get("max_iterations", 2)
        if isinstance(validation_result, dict) else 2
    )
    curr = state.get("validation_iterations") or 1
    if curr >= max_iter:
        logger.info(
            "[ValidationLoop] 达到迭代上限 %d，停止闭环 | 当前 remaining=%d | newly=%d",
            max_iter,
            validation_result.get("remaining_issue_count", 0),
            validation_result.get("new_issue_count", 0),
        )
        return "end"

    # 没有达到迭代上限，继续闭环
    # 更新 iterations（用于下一轮判断），注意 StateGraph 的条件边不能改 state，
    # 所以这里只是 log，真正计数在 ValidationAgent.execute 里。
    logger.info(
        "[ValidationLoop] 仍有残留问题，进入第 %d/%d 轮修复循环",
        curr + 1, max_iter,
    )
    return "hitl"


# ============================================================
# Phase 2 ~ Phase 5 的 build_*_graph（保留向后兼容）
'''
一组工厂函数,不是“一个大而全的单体函数”，而是“按阶段分图、按阶段可运行”。
'''
# ============================================================

# 先把文档读进来并结构化
# 工程基线，保证了后续所有更高层的工作流都不是在空白基础上开始，而是建立在：
# 能识别任务，能解析文档，能得到结构化文档对象的前提下，
# 如果这个阶段失败，后面的 retrieval / review / repair 都没有意义。

# 返回一个Graph对象，里面包含了planner和parser两个节点，以及它们之间的边。
# 判空是为了保证这个函数可以在没有传入config和llm_client的情况下使用默认配置和默认LLM客户端。
# 可以方便地用于测试
def build_parser_only_graph(
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    planner: Optional[BaseAgent] = None,
    parser: Optional[BaseAgent] = None,
) -> CompiledStateGraph:
    """Phase 2 工作流（仅 planner + parser），保留用于旧测试。"""
    if config is None:
        config = get_config()  # LLM相关参数，ChromaDB路径，输出目录，日志设置等
    if planner is None:
        planner = PlannerAgent(llm_client=llm_client, config=config)
    if parser is None:
        parser = ParserAgent(llm_client=llm_client, config=config)

    # 把处理流程写成状态机
    # DocGuardState 是共享状态字典，后面 planner/parser 都会往里面写数据，每次调用 workflow 都会拿一份新的状态对象传进去
    workflow = StateGraph(DocGuardState)
    # 每个节点对应一个Agent的_safe_execute方法，这个方法会调用Agent的execute方法，并处理异常
    # 捕获异常，把错误写回 state，保证状态结构不坏，避免整个图因为一个节点报错直接崩掉
    workflow.add_node("planner", planner._safe_execute)
    workflow.add_node("parser", parser._safe_execute)
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "parser")
    workflow.add_edge("parser", END)

    compiled = workflow.compile()
    logger.info("Phase 2 工作流已构建: START → planner → parser → END")
    return compiled

# 读进来 + 再找相似案例
def build_retrieval_graph(
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    planner: Optional[BaseAgent] = None,
    parser: Optional[BaseAgent] = None,
    retrieval: Optional[BaseAgent] = None,
) -> CompiledStateGraph:
    """Phase 3 工作流（planner + parser + retrieval）。"""
    if config is None:
        config = get_config()
    if planner is None:
        planner = PlannerAgent(llm_client=llm_client, config=config)
    if parser is None:
        parser = ParserAgent(llm_client=llm_client, config=config)
    if retrieval is None:
        retrieval = RetrievalAgent(llm_client=llm_client, config=config)

    workflow = StateGraph(DocGuardState)
    workflow.add_node("planner", planner._safe_execute)
    workflow.add_node("parser", parser._safe_execute)
    workflow.add_node("retrieval", retrieval._safe_execute)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "parser")
    # 有分支的边，parser成功 → retrieval，parser失败 → END
    # 路由函数，LangGraph 会在 parser 执行结束后调用它，传入当前 state。
    workflow.add_conditional_edges(
        "parser",
        _route_after_parser,
        {"retrieval": "retrieval", "end": END},
    )
    workflow.add_edge("retrieval", END)

    compiled = workflow.compile()
    logger.info("Phase 3 工作流已构建")
    return compiled

# 读进来 + 找案例 + 审查问题
def build_review_graph(
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    planner: Optional[BaseAgent] = None,
    parser: Optional[BaseAgent] = None,
    retrieval: Optional[BaseAgent] = None,
    review: Optional[BaseAgent] = None,
) -> CompiledStateGraph:
    """Phase 4 工作流（planner + parser + retrieval + review）。"""
    if config is None:
        config = get_config()
    if planner is None:
        planner = PlannerAgent(llm_client=llm_client, config=config)
    if parser is None:
        parser = ParserAgent(llm_client=llm_client, config=config)
    if retrieval is None:
        retrieval = RetrievalAgent(llm_client=llm_client, config=config)
    if review is None:
        review = ReviewAgent(llm_client=llm_client, config=config)

    workflow = StateGraph(DocGuardState)
    workflow.add_node("planner", planner._safe_execute)
    workflow.add_node("parser", parser._safe_execute)
    workflow.add_node("retrieval", retrieval._safe_execute)
    workflow.add_node("review", review._safe_execute)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "parser")
    workflow.add_conditional_edges(
        "parser",
        _route_after_parser,
        {"retrieval": "retrieval", "end": END},
    )
    workflow.add_edge("retrieval", "review")
    workflow.add_edge("review", END)

    # 编译成一个可执行的图对象
    compiled = workflow.compile()
    logger.info("Phase 4 工作流已构建")
    return compiled

# 审查 + 自动修复
def build_repair_graph(
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    planner: Optional[BaseAgent] = None,
    parser: Optional[BaseAgent] = None,
    retrieval: Optional[BaseAgent] = None,
    review: Optional[BaseAgent] = None,
    repair: Optional[BaseAgent] = None,
) -> CompiledStateGraph:
    """Phase 5 工作流（planner + parser + retrieval + review + repair）。

    Phase 6 兼容：该函数保留 Phase 5 结构（不包含 HITL/Validation）。
    若需要六层 + 迭代闭环，请使用 build_docguard_graph()。
    """
    if config is None:
        config = get_config()
    if planner is None:
        planner = PlannerAgent(llm_client=llm_client, config=config)
    if parser is None:
        parser = ParserAgent(llm_client=llm_client, config=config)
    if retrieval is None:
        retrieval = RetrievalAgent(llm_client=llm_client, config=config)
    if review is None:
        review = ReviewAgent(llm_client=llm_client, config=config)
    if repair is None:
        repair = RepairAgent(llm_client=llm_client, config=config)

    workflow = StateGraph(DocGuardState)
    workflow.add_node("planner", planner._safe_execute)
    workflow.add_node("parser", parser._safe_execute)
    workflow.add_node("retrieval", retrieval._safe_execute)
    workflow.add_node("review", review._safe_execute)
    workflow.add_node("repair", repair._safe_execute)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "parser")
    workflow.add_conditional_edges(
        "parser",
        _route_after_parser,
        {"retrieval": "retrieval", "end": END},
    )
    workflow.add_edge("retrieval", "review")
    # Phase 5 的 review→repair 路由（无 HITL/Validation）
    def _p5_route_after_review(state: DocGuardState) -> str:
        issues = state.get("review_issues") or []
        return "repair" if len(issues) > 0 else "end"

    workflow.add_conditional_edges(
        "review",
        _p5_route_after_review,
        {"repair": "repair", "end": END},
    )
    workflow.add_edge("repair", END)

    compiled = workflow.compile()
    logger.info("Phase 5 工作流已构建")
    return compiled


# ============================================================
# Phase 6：完整六层 + 迭代闭环
# ============================================================

# 完整闭环：审查 → 修复 → 验证
def build_docguard_graph(
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    planner: Optional[BaseAgent] = None,
    parser: Optional[BaseAgent] = None,
    retrieval: Optional[BaseAgent] = None,
    review: Optional[BaseAgent] = None,
    hitl: Optional[BaseAgent] = None,
    repair: Optional[BaseAgent] = None,
    validation: Optional[BaseAgent] = None,
) -> CompiledStateGraph:
    """构建 Phase 6 完整 DocGuard 工作流。

    工作流：
        START → planner → parser → [ok?] → retrieval → review → [issues>0?]
                                                                   ├─ no  → END
                                                                   └─ yes → hitl → repair → validation
                                                                                                 ├─ pass → END
                                                                                                 └─ loop(≤N) → hitl → ...

    Args:
        config: 配置实例
        llm_client: LLM 客户端
        planner/parser/retrieval/review/hitl/repair/validation: 可选 Agent 依赖注入
            （用于测试或自定义替换）

    Returns:
        编译后的 LangGraph 工作流
    """
    if config is None:
        config = get_config()
    if planner is None:
        planner = PlannerAgent(llm_client=llm_client, config=config)
    if parser is None:
        parser = ParserAgent(llm_client=llm_client, config=config)
    if retrieval is None:
        retrieval = RetrievalAgent(llm_client=llm_client, config=config)
    if review is None:
        review = ReviewAgent(llm_client=llm_client, config=config)
    if hitl is None:
        hitl = HitlAgent(llm_client=llm_client, config=config)
    if repair is None:
        repair = RepairAgent(llm_client=llm_client, config=config)
    if validation is None:
        validation = ValidationAgent(llm_client=llm_client, config=config)

    workflow = StateGraph(DocGuardState)

    # 6 个节点
    workflow.add_node("planner", planner._safe_execute)
    workflow.add_node("parser", parser._safe_execute)
    workflow.add_node("retrieval", retrieval._safe_execute)
    workflow.add_node("review", review._safe_execute)
    workflow.add_node("hitl", hitl._safe_execute)
    workflow.add_node("repair", repair._safe_execute)
    workflow.add_node("validation", validation._safe_execute)

    # START → planner
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "parser")

    # parser 条件分支
    workflow.add_conditional_edges(
        "parser",
        _route_after_parser,
        {"retrieval": "retrieval", "end": END},
    )
    # retrieval → review
    workflow.add_edge("retrieval", "review")
    # review → hitl / END
    workflow.add_conditional_edges(
        "review",
        _route_after_review,
        {"hitl": "hitl", "end": END},
    )
    # hitl → repair
    workflow.add_edge("hitl", "repair")
    # repair → validation
    workflow.add_edge("repair", "validation")
    # validation → hitl / END（迭代闭环）
    workflow.add_conditional_edges(
        "validation",
        _route_after_validation,
        {"hitl": "hitl", "end": END},
    )

    compiled = workflow.compile()
    logger.info(
        "Phase 6 六层工作流已构建: planner → parser → retrieval → review → hitl → repair → validation (loop≤N)"
    )
    return compiled


# ============================================================
# 便捷运行函数
# ============================================================

# async 声明的函数，可以在异步环境中被 await 调用，返回一个协程对象。
# async 函数不能直接像普通函数那样调用返回结果：
async def run_parser_workflow(
    input_file_path: str,
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    format_hint: Optional[str] = None,
) -> DocGuardState:
    """运行 Phase 2 解析工作流。Phase 6 扩展：支持 format_hint（pdf/ppt/docx）。"""
    if config is None:
        config = get_config()
    if llm_client is None:
        try:
            llm_client = create_llm_client(config.llm)
        except Exception as e:
            logger.warning("LLM 客户端初始化失败（Parser 阶段可忽略）: %s", e)
            llm_client = None

    app = build_parser_only_graph(config, llm_client)

    from core.state import create_initial_state
    initial_state = create_initial_state(
        task_id="",
        input_file_path=input_file_path,
        format_hint=format_hint,
    )
    logger.info("启动 Phase 2 解析工作流: %s (format=%s)", input_file_path, format_hint)
    # 一旦调用 ainvoke，LangGraph 会按顺序跑：planner 节点，parser 节点，很耗时
    return await app.ainvoke(initial_state)


async def run_retrieval_workflow(
    input_file_path: str,
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    skip_llm: bool = False,
    mock_embedding: bool = False,
    format_hint: Optional[str] = None,
) -> DocGuardState:
    """运行 Phase 3 检索工作流。Phase 6 扩展：format_hint。"""
    if config is None:
        config = get_config()
    if llm_client is None and not skip_llm:
        try:
            llm_client = create_llm_client(config.llm)
        except Exception as e:
            logger.warning("LLM 客户端初始化失败（将使用无 LLM 模式）: %s", e)
            llm_client = None

    retrieval_agent: Optional[RetrievalAgent] = None
    if mock_embedding:
        config.chroma.embedding_dim = 384
        from core.mock_embedding import create_mock_embedding_client
        retrieval_agent = RetrievalAgent(
            llm_client=llm_client,
            config=config,
            embedding_client=create_mock_embedding_client(dim=config.chroma.embedding_dim),
        )
        logger.warning("启用 Mock Embedding 模式（仅适用于开发/测试）")

    app = build_retrieval_graph(config, llm_client, retrieval=retrieval_agent)

    from core.state import create_initial_state
    initial_state = create_initial_state(
        task_id="", input_file_path=input_file_path, format_hint=format_hint,
    )
    logger.info("启动 Phase 3 检索工作流: %s", input_file_path)
    return await app.ainvoke(initial_state)


async def run_review_workflow(
    input_file_path: str,
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    skip_llm: bool = False,
    mock_embedding: bool = False,
    format_hint: Optional[str] = None,
) -> DocGuardState:
    """运行 Phase 4 审查工作流。Phase 6 扩展：format_hint。"""
    if config is None:
        config = get_config()
    if llm_client is None and not skip_llm:
        try:
            llm_client = create_llm_client(config.llm)
        except Exception as e:
            logger.warning("LLM 客户端初始化失败（将使用无 LLM 模式）: %s", e)
            llm_client = None

    retrieval_agent: Optional[RetrievalAgent] = None
    if mock_embedding:
        config.chroma.embedding_dim = 384
        from core.mock_embedding import create_mock_embedding_client
        retrieval_agent = RetrievalAgent(
            llm_client=llm_client,
            config=config,
            embedding_client=create_mock_embedding_client(dim=config.chroma.embedding_dim),
        )
        logger.warning("启用 Mock Embedding 模式（仅适用于开发/测试）")

    app = build_review_graph(config, llm_client, retrieval=retrieval_agent)

    from core.state import create_initial_state
    initial_state = create_initial_state(
        task_id="", input_file_path=input_file_path, format_hint=format_hint,
    )
    logger.info("启动 Phase 4 审查工作流: %s", input_file_path)
    return await app.ainvoke(initial_state)


async def run_repair_workflow(
    input_file_path: str,
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    skip_llm: bool = False,
    mock_embedding: bool = False,
    output_dir: Optional[str] = None,
    format_hint: Optional[str] = None,
) -> DocGuardState:
    """运行 Phase 5 修复工作流（保留 Phase 5 构建器，不含 HITL/Validation）。

    Phase 6 扩展：format_hint 支持多格式解析。
    """
    if config is None:
        config = get_config()
    if llm_client is None and not skip_llm:
        try:
            llm_client = create_llm_client(config.llm)
        except Exception as e:
            logger.warning("LLM 客户端初始化失败（将使用无 LLM 模式）: %s", e)
            llm_client = None

    retrieval_agent: Optional[RetrievalAgent] = None
    if mock_embedding:
        config.chroma.embedding_dim = 384
        from core.mock_embedding import create_mock_embedding_client
        retrieval_agent = RetrievalAgent(
            llm_client=llm_client,
            config=config,
            embedding_client=create_mock_embedding_client(dim=config.chroma.embedding_dim),
        )
        logger.warning("启用 Mock Embedding 模式（仅适用于开发/测试）")

    app = build_repair_graph(config, llm_client, retrieval=retrieval_agent)

    from core.state import create_initial_state
    initial_state = create_initial_state(
        task_id="", input_file_path=input_file_path, format_hint=format_hint,
    )
    logger.info("启动 Phase 5 修复工作流: %s", input_file_path)
    final_state = await app.ainvoke(initial_state)

    # 保存修复后的 DOCX（仅对 DOCX 有效，其他格式跳过）
    if output_dir and final_state.get("repair_success") is not None:
        repaired_doc = final_state.get("repaired_document") or final_state.get("parsed_document")
        if (
            repaired_doc is not None
            and repaired_doc._docx_reference is not None
        ):
            try:
                from document.writer import DocxWriter
                writer = DocxWriter()
                output_path = writer.save_as(repaired_doc, output_dir)
                final_state["output_docx_path"] = output_path
                logger.info("修复后 DOCX 已保存: %s", output_path)
            except Exception as e:
                logger.warning("保存修复后 DOCX 失败: %s", e, exc_info=True)
                final_state["repair_error"] = (
                    f"保存输出失败: {e}"
                    if not final_state.get("repair_error")
                    else f"{final_state['repair_error']}; 保存输出失败: {e}"
                )
    return final_state


async def run_docguard_workflow(
    input_file_path: str,
    config: Optional[DocGuardConfig] = None,
    llm_client: Optional[LLMClient] = None,
    *,
    skip_llm: bool = False,
    mock_embedding: bool = False,
    output_dir: Optional[str] = None,
    format_hint: Optional[str] = None,
) -> DocGuardState:
    """运行 Phase 6 完整 DocGuard 六层工作流（带 HITL + Validation 迭代闭环）。

    Args:
        input_file_path: 待处理文档路径（DOCX/PDF/PPT）
        config: 配置实例
        llm_client: LLM 客户端
        skip_llm: 是否跳过 LLM（规则引擎模式）
        mock_embedding: 是否使用 Mock Embedding
        output_dir: 提供时，将修复后的 DOCX 保存到该目录，并写入 output_docx_path
        format_hint: 格式覆盖（"docx"/"pdf"/"ppt"），不提供则按后缀自动推断

    Returns:
        DocGuardState（包含 validation_result 等最终输出）
    """
    if config is None:
        config = get_config()
    if llm_client is None and not skip_llm:
        try:
            llm_client = create_llm_client(config.llm)
        except Exception as e:
            logger.warning("LLM 客户端初始化失败（将使用无 LLM 模式）: %s", e)
            llm_client = None

    retrieval_agent: Optional[RetrievalAgent] = None
    if mock_embedding:
        config.chroma.embedding_dim = 384
        from core.mock_embedding import create_mock_embedding_client
        retrieval_agent = RetrievalAgent(
            llm_client=llm_client,
            config=config,
            embedding_client=create_mock_embedding_client(dim=config.chroma.embedding_dim),
        )
        logger.warning("启用 Mock Embedding 模式（仅适用于开发/测试）")

    app = build_docguard_graph(config, llm_client, retrieval=retrieval_agent)

    from core.state import create_initial_state
    initial_state = create_initial_state(
        task_id="", input_file_path=input_file_path, format_hint=format_hint,
    )
    logger.info("启动 Phase 6 DocGuard 工作流: %s (format=%s)", input_file_path, format_hint)
    final_state = await app.ainvoke(initial_state)

    if output_dir:
        repaired_doc = final_state.get("repaired_document") or final_state.get("parsed_document")
        if (
            repaired_doc is not None
            and repaired_doc._docx_reference is not None
        ):
            try:
                from document.writer import DocxWriter
                writer = DocxWriter()
                output_path = writer.save_as(repaired_doc, output_dir)
                final_state["output_docx_path"] = output_path
                logger.info("修复后 DOCX 已保存: %s", output_path)
            except Exception as e:
                logger.warning("保存修复后 DOCX 失败: %s", e, exc_info=True)
                final_state["repair_error"] = (
                    f"保存输出失败: {e}"
                    if not final_state.get("repair_error")
                    else f"{final_state['repair_error']}; 保存输出失败: {e}"
                )
    return final_state
