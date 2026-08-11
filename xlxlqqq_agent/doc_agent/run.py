"""
DocGuard Agent - 一键启动脚本
==============================

Phase 6：支持六层工作流 + 多格式 + HITL。

用法：
    # Phase 2：仅解析（支持 --format pdf|ppt|docx）
    python run.py parse <input.docx>
    python run.py parse <input.pdf> --format pdf

    # Phase 4：解析 + 检索 + 审查
    python run.py review <input.docx>

    # Phase 5：解析 + 检索 + 审查 + 修复（输出修复 DOCX）
    python run.py repair <input.docx> --skip-llm --mock-embedding

    # Phase 6：六层完整工作流（含 HITL + Validation 复检 + 迭代闭环）
    python run.py full <input.docx> --skip-llm --mock-embedding
    python run.py full <input.docx> --hitl-mode auto-approve
    python run.py full <input.docx> --hitl-mode interactive

    # Phase 6：仅修复后复检（已有修复后 DOCX 的场景）
    python run.py validate <input_original.docx> --repaired <repaired.docx>

    # 知识库管理
    python run.py ingest <knowledge_docs_dir>
    python run.py stats

示例：
    python run.py full samples/input.docx --skip-llm --mock-embedding --format docx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_config  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.logging_config import get_logger  # noqa: E402
from agents.workflow import (  # noqa: E402
    run_parser_workflow,
    run_retrieval_workflow,
    run_review_workflow,
    run_repair_workflow,
    run_docguard_workflow,
)


# ============================================================
# 通用参数（所有子命令共享 --format / --verbose / --json / --output-dir 等）
# ============================================================
_COMMON_FORMAT_HELP = (
    "格式覆盖（docx/pdf/ppt），不指定则按后缀自动推断。"
    "对于 PDF/PPT，修复阶段仅支持批注，不支持回写。"
)
_COMMON_HITL_HELP = (
    "HITL 模式："
    "auto-approve（默认，按配置自动批准所有需确认 issue）；"
    "interactive（CLI 交互询问，critical/major 由用户手动 decide）；"
    "disable（关闭 HITL，所有 issue 走默认批准）。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DocGuard Agent - 企业文档智能审查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令：
  parse    Phase 2: 解析文档（支持 DOCX/PDF/PPT）
  review   Phase 4: 解析 + 检索 + 内容/格式/结构审查
  repair   Phase 5: 审查 + 自动修复（无 HITL/Validation）
  full     Phase 6: 六层完整工作流（含 HITL + Validation + 迭代闭环）
  validate Phase 6: 修复后复检（对比原始文档与修复后文档）
  ingest   摄取目录下所有 DOCX 到知识库
  stats    查看知识库统计信息

示例：
  python run.py full input.docx --skip-llm --mock-embedding
  python run.py full input.pdf --format pdf
  python run.py validate original.docx --repaired repaired.docx
  python run.py ingest knowledge_docs/
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ---------- parse ----------
    p_parse = subparsers.add_parser("parse", help="Phase 2: 解析文档")
    p_parse.add_argument("input_file", type=str, help="文档路径")
    p_parse.add_argument("--format", type=str, default=None,
                         choices=["docx", "pdf", "ppt"],
                         help=_COMMON_FORMAT_HELP)
    p_parse.add_argument("--output-dir", type=str, default="output/reports")
    p_parse.add_argument("--json", action="store_true")
    p_parse.add_argument("--verbose", action="store_true")

    # ---------- review ----------
    p_review = subparsers.add_parser("review", help="Phase 4: 审查")
    p_review.add_argument("input_file", type=str)
    p_review.add_argument("--format", type=str, default=None,
                          choices=["docx", "pdf", "ppt"], help=_COMMON_FORMAT_HELP)
    p_review.add_argument("--output-dir", type=str, default="output/reports")
    p_review.add_argument("--json", action="store_true")
    p_review.add_argument("--skip-llm", action="store_true")
    p_review.add_argument("--mock-embedding", action="store_true")
    p_review.add_argument("--only-issues", action="store_true")
    p_review.add_argument("--verbose", action="store_true")

    # ---------- repair ----------
    p_repair = subparsers.add_parser(
        "repair", help="Phase 5: 审查+修复（不含 HITL/Validation）",
    )
    p_repair.add_argument("input_file", type=str)
    p_repair.add_argument("--format", type=str, default=None,
                          choices=["docx", "pdf", "ppt"], help=_COMMON_FORMAT_HELP)
    p_repair.add_argument("--output-dir", type=str, default="output/repaired")
    p_repair.add_argument("--json", action="store_true")
    p_repair.add_argument("--skip-llm", action="store_true")
    p_repair.add_argument("--mock-embedding", action="store_true")
    p_repair.add_argument("--only-actions", action="store_true")
    p_repair.add_argument("--verbose", action="store_true")

    # ---------- full（Phase 6 完整） ----------
    p_full = subparsers.add_parser(
        "full",
        help="Phase 6: 完整六层工作流（含 HITL + Validation + 迭代闭环）",
    )
    p_full.add_argument("input_file", type=str)
    p_full.add_argument("--format", type=str, default=None,
                        choices=["docx", "pdf", "ppt"], help=_COMMON_FORMAT_HELP)
    p_full.add_argument("--output-dir", type=str, default="output/full")
    p_full.add_argument("--json", action="store_true")
    p_full.add_argument("--skip-llm", action="store_true")
    p_full.add_argument("--mock-embedding", action="store_true")
    p_full.add_argument(
        "--hitl-mode",
        type=str, default="auto-approve",
        choices=["auto-approve", "interactive", "disable"],
        help=_COMMON_HITL_HELP,
    )
    p_full.add_argument("--max-iterations", type=int, default=2,
                        help="修复-验证迭代闭环最大次数（默认 2）")
    p_full.add_argument("--only-summary", action="store_true",
                        help="仅显示最终摘要（跳过 parse/retrieval/review/repair 细节）")
    p_full.add_argument("--verbose", action="store_true")

    # ---------- validate（仅复检） ----------
    p_val = subparsers.add_parser(
        "validate",
        help="Phase 6: 修复后复检（对比原始文档与修复后文档）",
    )
    p_val.add_argument("input_file", type=str, help="原始文档路径")
    p_val.add_argument("--repaired", type=str, required=True,
                       help="修复后文档路径")
    p_val.add_argument("--format", type=str, default=None,
                       choices=["docx", "pdf", "ppt"], help=_COMMON_FORMAT_HELP)
    p_val.add_argument("--max-iterations", type=int, default=2)
    p_val.add_argument("--json", action="store_true")
    p_val.add_argument("--output-dir", type=str, default="output/validate")
    p_val.add_argument("--mock-embedding", action="store_true")
    p_val.add_argument("--skip-llm", action="store_true")
    p_val.add_argument("--verbose", action="store_true")

    # ---------- ingest ----------
    p_ingest = subparsers.add_parser("ingest", help="摄取 DOCX 到知识库")
    p_ingest.add_argument("directory", type=str)
    p_ingest.add_argument("--rebuild", action="store_true")
    p_ingest.add_argument("--mock-embedding", action="store_true")
    p_ingest.add_argument("--verbose", action="store_true")

    # ---------- stats ----------
    p_stats = subparsers.add_parser("stats", help="查看知识库统计")
    p_stats.add_argument("--verbose", action="store_true")

    return parser.parse_args()


# ============================================================
# 子命令实现
# ============================================================

async def cmd_parse(args: argparse.Namespace) -> int:
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - Phase 2 Parser")
    logger.info("=" * 60)

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", args.input_file)
        return 1

    try:
        final_state = await run_parser_workflow(
            input_file_path=str(input_path.resolve()),
            config=config,
            format_hint=getattr(args, "format", None),
        )
    except Exception as e:
        logger.error("工作流执行失败: %s", e, exc_info=True)
        return 2

    _print_parse_result(final_state, input_path)
    if args.json and final_state.get("parsed_document"):
        _save_json_report(final_state, input_path, args.output_dir, suffix="parse")
    return 0 if final_state.get("parse_success") else 3


async def cmd_review(args: argparse.Namespace) -> int:
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - Phase 4 Review")
    logger.info("=" * 60)

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", args.input_file)
        return 1

    try:
        final_state = await run_review_workflow(
            input_file_path=str(input_path.resolve()),
            config=config,
            skip_llm=args.skip_llm,
            mock_embedding=args.mock_embedding,
            format_hint=getattr(args, "format", None),
        )
    except Exception as e:
        logger.error("工作流执行失败: %s", e, exc_info=True)
        return 2

    if not args.only_issues:
        _print_parse_result(final_state, input_path)
        _print_retrieval_result(final_state)
    _print_review_result(final_state)
    if args.json:
        _save_json_report(final_state, input_path, args.output_dir, suffix="review")
    issues = final_state.get("review_issues", []) or []
    if any(i.get("severity") in ("critical", "major") for i in issues):
        return 4
    return 0 if final_state.get("parse_success") else 3


async def cmd_repair(args: argparse.Namespace) -> int:
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - Phase 5 Repair")
    logger.info("=" * 60)

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", args.input_file)
        return 1

    try:
        final_state = await run_repair_workflow(
            input_file_path=str(input_path.resolve()),
            config=config,
            skip_llm=args.skip_llm,
            mock_embedding=args.mock_embedding,
            output_dir=args.output_dir,
            format_hint=getattr(args, "format", None),
        )
    except Exception as e:
        logger.error("工作流执行失败: %s", e, exc_info=True)
        return 2

    if not args.only_actions:
        _print_parse_result(final_state, input_path)
        _print_retrieval_result(final_state)
        _print_review_result(final_state)
    _print_repair_result(final_state)
    if args.json:
        _save_json_report(final_state, input_path, args.output_dir, suffix="repair")
    if not final_state.get("repair_success", False):
        return 5
    return 0 if final_state.get("parse_success") else 3


async def cmd_full(args: argparse.Namespace) -> int:
    """Phase 6：完整六层工作流。"""
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"

    # HITL 配置
    hitl_mode = args.hitl_mode
    if hitl_mode == "disable":
        config.hitl.enabled = False
    else:
        config.hitl.enabled = True
        config.hitl.auto_approve_all = (hitl_mode == "auto-approve")
        config.hitl.require_confirmation_for = [
            "critical", "major",
        ] if hitl_mode == "interactive" else []
    # Validation 迭代上限
    config.validation.max_iterations = args.max_iterations

    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - Phase 6 完整六层工作流")
    logger.info("=" * 60)
    logger.info("Format: %s", getattr(args, "format", "auto"))
    logger.info("HITL mode: %s", hitl_mode)
    logger.info("Max iterations: %d", args.max_iterations)

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", args.input_file)
        return 1

    try:
        final_state = await run_docguard_workflow(
            input_file_path=str(input_path.resolve()),
            config=config,
            skip_llm=args.skip_llm,
            mock_embedding=args.mock_embedding,
            output_dir=args.output_dir,
            format_hint=getattr(args, "format", None),
        )
    except Exception as e:
        logger.error("工作流执行失败: %s", e, exc_info=True)
        return 2

    if not args.only_summary:
        _print_parse_result(final_state, input_path)
        _print_retrieval_result(final_state)
        _print_review_result(final_state)
        _print_repair_result(final_state)

    _print_validation_result(final_state)

    if args.json:
        _save_json_report(final_state, input_path, args.output_dir, suffix="full")

    # 返回码：validation.pass → 0；critical/major 残留 → 4；修复失败 → 5
    vr = final_state.get("validation_result") or {}
    if not final_state.get("repair_success", True):
        return 5
    if vr and not vr.get("pass_flag", True):
        remaining = vr.get("remaining_issues", []) or []
        if any(i.get("severity") in ("critical", "major") for i in remaining):
            return 4
    return 0 if final_state.get("parse_success") else 3


async def cmd_validate(args: argparse.Namespace) -> int:
    """Phase 6：仅修复后复检。

    流程：重新 parse original → 模拟 review（作为原始 issues）→
          parse repaired → 模拟 review（作为修复后 issues）→
          ValidationAgent 计算差值。
    """
    config = get_config()
    config.validation.max_iterations = args.max_iterations
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - Phase 6 Validation Only")
    logger.info("=" * 60)

    original_path = Path(args.input_file)
    repaired_path = Path(args.repaired)
    if not original_path.exists():
        logger.error("原始文件不存在: %s", original_path)
        return 1
    if not repaired_path.exists():
        logger.error("修复后文件不存在: %s", repaired_path)
        return 1

    try:
        from document.base_parser import parse_any
        from agents.review_agent import ReviewAgent
        from agents.validation_agent import ValidationAgent
        from core.state import DocGuardState, create_initial_state

        # 1. 解析原始与修复后文档
        fmt_hint = getattr(args, "format", None)
        orig_doc = parse_any(str(original_path), config, fmt_hint)
        rep_doc = parse_any(str(repaired_path), config, fmt_hint)

        # 2. 构造 ReviewAgent 分别 review 两份文档
        review_agent = ReviewAgent(llm_client=None, config=config)

        orig_state: DocGuardState = create_initial_state(
            task_id="validate-orig",
            input_file_path=str(original_path),
        )
        orig_state["parsed_document"] = orig_doc
        orig_state["parse_success"] = True
        orig_state = await review_agent.execute(orig_state)
        orig_issues = orig_state["review_issues"]

        rep_state: DocGuardState = create_initial_state(
            task_id="validate-rep",
            input_file_path=str(repaired_path),
        )
        rep_state["parsed_document"] = rep_doc
        rep_state["parse_success"] = True
        rep_state["review_issues"] = orig_issues
        rep_state["repaired_document"] = rep_doc
        rep_state = await review_agent.execute(rep_state)

        # 3. ValidationAgent 计算差值
        val_agent = ValidationAgent(llm_client=None, config=config)
        final_state = await val_agent.execute(rep_state)

    except Exception as e:
        logger.error("Validation 执行失败: %s", e, exc_info=True)
        return 2

    _print_validation_result(final_state)

    if args.json:
        _save_json_report(
            final_state, original_path, args.output_dir, suffix="validate",
        )
    vr = final_state.get("validation_result") or {}
    if not vr.get("pass_flag", True):
        return 4
    return 0


async def cmd_ingest(args: argparse.Namespace) -> int:
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    logger.info("=" * 60)
    logger.info("DocGuard Agent - 知识库摄取")
    logger.info("=" * 60)
    dir_path = Path(args.directory)
    if not dir_path.exists():
        logger.error("目录不存在: %s", args.directory)
        return 1

    try:
        from knowledge.vector_store import create_vector_store
        from knowledge.ingestor import KnowledgeIngestor
        if args.mock_embedding:
            config.chroma.embedding_dim = 384
            from core.mock_embedding import create_mock_embedding_client
            embedding_client = create_mock_embedding_client(dim=config.chroma.embedding_dim)
            logger.warning("启用 Mock Embedding 模式（仅适用于开发/测试）")
        else:
            from core.embedding_client import create_embedding_client
            embedding_client = create_embedding_client(config.llm)

        vector_store = create_vector_store(config.chroma)
        if args.rebuild:
            vector_store.reset()
        ingestor = KnowledgeIngestor(embedding_client, vector_store)
    except Exception as e:
        logger.error("初始化失败: %s", e, exc_info=True)
        return 2

    try:
        result = await ingestor.ingest_directory(args.directory)
    except Exception as e:
        logger.error("摄取失败: %s", e, exc_info=True)
        return 3

    print("\n" + "=" * 60)
    print("📚 知识库摄取结果")
    print("=" * 60)
    print(f"总文件数:       {result.total_files}")
    print(f"成功文件数:     {result.success_files}")
    print(f"失败文件数:     {result.failed_files}")
    print(f"总 chunks 数:   {result.total_chunks}")
    print(f"总耗时:         {result.elapsed_seconds}s")

    if result.per_file_stats:
        print(f"\n📄 各文件 chunk 数:")
        for fname, count in result.per_file_stats.items():
            print(f"  {fname}: {count}")
    if result.failed_files_list:
        print(f"\n❌ 失败文件:")
        for fname in result.failed_files_list:
            print(f"  {fname}")

    total = vector_store.count()
    print(f"\n📊 知识库当前总记录数: {total}")
    print("=" * 60)
    return 0 if result.failed_files == 0 else 4


async def cmd_stats(args: argparse.Namespace) -> int:
    config = get_config()
    if args.verbose:
        config.log.level = "DEBUG"
    setup_logging(config)
    logger = get_logger("run")

    from knowledge.vector_store import create_vector_store
    vector_store = create_vector_store(config.chroma)
    health = vector_store.health_check()

    print("\n" + "=" * 60)
    print("📊 知识库统计信息")
    print("=" * 60)
    print(f"健康状态:       {'✅ 健康' if health['healthy'] else '❌ 异常'}")
    print(f"Collection:     {health.get('collection', 'N/A')}")
    print(f"持久化目录:     {health.get('persist_dir', 'N/A')}")
    print(f"总记录数:       {health.get('count', 0)}")
    if not health["healthy"]:
        print(f"错误信息:       {health.get('error', 'N/A')}")
    print("=" * 60)
    return 0 if health["healthy"] else 1


# ============================================================
# 输出辅助
# ============================================================
def _print_parse_result(final_state, input_path: Path) -> None:
    print("\n" + "=" * 60)
    print("📊 解析结果摘要")
    print("=" * 60)
    print(f"任务 ID:        {final_state.get('task_id', 'N/A')}")
    print(f"解析状态:       {'✅ 成功' if final_state.get('parse_success') else '❌ 失败'}")
    if final_state.get("parse_error"):
        print(f"错误信息:       {final_state['parse_error']}")

    if final_state.get("parsed_document"):
        doc = final_state["parsed_document"]
        stats = doc.get_statistics()
        src_fmt = getattr(doc, "source_format", "docx")
        print(f"源格式:         {src_fmt.upper()}")
        print(f"文档标题:       {doc.title or '(未设置)'}")
        print(f"作者:           {doc.author or '(未设置)'}")
        print(f"段落数:         {stats['paragraph_count']}")
        print(f"表格数:         {stats['table_count']}")
        print(f"图片数:         {stats['image_count']}")
        print(f"标题数:         {stats['heading_count']}")
        print(f"字数:           {stats['word_count']}")

        outline = doc.get_heading_outline()
        if outline:
            print("\n📑 文档大纲:")
            for item in outline[:10]:
                indent = "  " * (item["level"] - 1)
                print(f"  {indent}H{item['level']}: {item['text']}")
            if len(outline) > 10:
                print(f"  ... 共 {len(outline)} 个标题")


def _print_retrieval_result(final_state) -> None:
    print("\n" + "=" * 60)
    print("🔍 知识库检索结果")
    print("=" * 60)
    retrieved = final_state.get("retrieved_documents", [])
    print(f"命中相似文档数: {len(retrieved)}")
    if retrieved:
        print("\n📋 Top 5 相似片段:")
        for i, r in enumerate(retrieved[:5], 1):
            print(f"  {i}. [{r['similarity_score']:.4f}] {r['filename']}")
            snippet = r["content_snippet"][:80].replace("\n", " ")
            print(f"     {snippet}...")

    style_profile = final_state.get("style_profile")
    if style_profile:
        print(f"\n🎯 风格画像:")
        print(f"  生成方式:       {style_profile.get('raw_profile_text') is not None and 'stats+llm' or 'stats'}")
        print(f"  推荐章节数:     {len(style_profile.get('expected_sections', []))}")
        print(f"  正文字体:       {style_profile.get('body_font', '(未知)')}")
        print(f"  正文字号:       {style_profile.get('body_size_pt', '(未知)')}pt")
        print(f"  正文行距:       {style_profile.get('line_spacing', '(未知)')}")
        print(f"  H1 字体:        {style_profile.get('heading_font', '(未知)')}")
        print(f"  H1 字号:        {style_profile.get('heading_size_pt', '(未知)')}pt")
        print(f"  术语库大小:     {len(style_profile.get('terminology', []))}")
        if style_profile.get("expected_sections"):
            print(f"  推荐章节:       {', '.join(style_profile['expected_sections'][:5])}")

    print(f"\n⏱ 执行步骤:")
    for step in final_state.get("step_logs", []):
        status = "✅" if step["success"] else "❌"
        print(f"  {status} {step['step']}: {step['elapsed_seconds']:.3f}s - {step.get('summary', '')}")
    print(f"\n总耗时: {final_state.get('total_elapsed_seconds', 0):.3f}s")
    print("=" * 60)


def _print_review_result(final_state) -> None:
    issues = final_state.get("review_issues") or []
    report = final_state.get("review_report")
    print("\n" + "=" * 60)
    print("🛡  文档审查结果")
    print("=" * 60)

    if report:
        score = report.get("quality_score", 0)
        if score >= 90:
            score_icon = "🟢"
        elif score >= 70:
            score_icon = "🟡"
        elif score >= 50:
            score_icon = "🟠"
        else:
            score_icon = "🔴"
        print(f"  质量评分:       {score_icon} {score} / 100")
        print(f"  问题总数:       {report.get('total_issues', 0)}")
        sev = report.get("by_severity", {})
        print(f"     Critical: {sev.get('critical', 0)}   Major: {sev.get('major', 0)}   "
              f"Minor: {sev.get('minor', 0)}   Info: {sev.get('info', 0)}")
        cat = report.get("by_category", {})
        fmt = sum(v for k, v in cat.items() if k.startswith("format"))
        stc = sum(v for k, v in cat.items() if k.startswith("structure"))
        ctt = sum(v for k, v in cat.items() if k.startswith("content"))
        print(f"     格式: {fmt}   结构: {stc}   内容: {ctt}")

    if not issues:
        print("\n✅ 未发现问题，文档质量良好！")
        print("=" * 60)
        return

    print(f"\n📋 问题清单（共 {len(issues)} 项）:")
    for idx, issue in enumerate(issues, 1):
        sev_icon = {"critical": "🛑", "high": "🔴", "medium": "🟡", "low": "🔵"}.get(
            issue.get("severity", "low"), "•"
        )
        loc = issue.get("location", {})
        loc_parts = []
        if loc.get("section"):
            loc_parts.append(f"章节: {loc['section']}")
        if loc.get("paragraph_index") is not None:
            loc_parts.append(f"段落#{loc['paragraph_index']}")
        loc_str = " | ".join(loc_parts) if loc_parts else "文档范围"

        print(f"\n  {idx}. {sev_icon} [{issue.get('severity', '?').upper()}] "
              f"[{issue.get('category', '?')}] {issue.get('title', '')}")
        print(f"      位置: {loc_str}")
        print(f"      描述: {issue.get('description', '')}")
        if issue.get("original_text"):
            snippet = issue["original_text"].replace("\n", " ")[:60]
            print(f"      原文: {snippet}")
        if issue.get("suggested_fix"):
            fix_snippet = issue["suggested_fix"].replace("\n", " ")[:60]
            print(f"      建议: {fix_snippet}")

    if report and report.get("suggestions"):
        print(f"\n💡 改进建议:")
        for sug in report["suggestions"][:5]:
            print(f"  • {sug}")
    print("=" * 60)


def _print_repair_result(final_state) -> None:
    actions = final_state.get("repair_actions") or []
    repair_success = final_state.get("repair_success")
    output_path = final_state.get("output_docx_path")
    repair_error = final_state.get("repair_error")

    print("\n" + "=" * 60)
    print("🔧 文档修复结果")
    print("=" * 60)
    status_icon = "✅" if repair_success else "⚠"
    print(f"  修复状态:       {status_icon} {'成功' if repair_success else '部分失败'}")
    if repair_error:
        print(f"  错误信息:       {repair_error}")
    if output_path:
        print(f"  输出文件:       {output_path}")
    else:
        print(f"  输出文件:       (未保存)")

    if not actions:
        print("\nℹ 无修复动作执行")
        print("=" * 60)
        return

    success_count = sum(1 for a in actions if a.get("success"))
    annotate_only = sum(1 for a in actions if not a.get("success") and a.get("annotated"))
    failed = sum(1 for a in actions if not a.get("success") and not a.get("annotated") and not a.get("skipped"))
    skipped = sum(1 for a in actions if a.get("skipped"))
    print(f"  动作总数:       {len(actions)}")
    print(f"     成功修复:   {success_count}")
    print(f"     仅批注:     {annotate_only}")
    print(f"     失败:       {failed}")
    print(f"     跳过(reject): {skipped}")

    print(f"\n📋 修复动作清单（共 {len(actions)} 项）:")
    for idx, action in enumerate(actions, 1):
        if action.get("skipped"):
            icon, status = "⏭", "跳过(reject)"
        elif action.get("success"):
            icon, status = "✅", "已修复"
        elif action.get("annotated"):
            icon, status = "📝", "已批注"
        else:
            icon, status = "❌", "失败"
        loc = action.get("location", {})
        loc_str = f"段落#{loc['paragraph_index']}" if loc.get("paragraph_index") is not None else "文档范围"
        decision = action.get("decision", "approve")
        print(f"\n  {idx}. {icon} [{status}] [{action.get('repair_type', '?')}] "
              f"decision={decision} issue={action.get('issue_id', '?')[:12]}...")
        print(f"      位置: {loc_str}")
        if action.get("original_value") or action.get("new_value"):
            print(f"      变更: {action.get('original_value', '(空)')} → {action.get('new_value', '(空)')}")
        if action.get("error_message"):
            print(f"      错误: {action['error_message']}")
    print("=" * 60)


def _print_validation_result(final_state) -> None:
    """打印 Validation 结果（Phase 6）。"""
    vr = final_state.get("validation_result")
    print("\n" + "=" * 60)
    print("✅ 修复后复检结果")
    print("=" * 60)

    if not vr:
        print("（validation_result 为空 — 未执行复检）")
        print("=" * 60)
        return

    pass_flag = vr.get("pass_flag", False)
    iterations = final_state.get("validation_iterations") or 1
    max_iter = vr.get("max_iterations", 2)
    fixed = vr.get("fixed_issue_count", 0)
    remaining = vr.get("remaining_issue_count", 0)
    newly = vr.get("new_issue_count", 0)

    status_icon = "🟢" if pass_flag else "🟠"
    print(f"  复检结论:       {status_icon} {'通过' if pass_flag else '未通过'}")
    print(f"  迭代次数:       {iterations} / {max_iter}")
    print(f"  已修复问题数:   {fixed}")
    print(f"  残留问题数:     {remaining}")
    print(f"  新引入问题数:   {newly}")

    remaining_issues = vr.get("remaining_issues") or []
    if remaining_issues:
        print(f"\n📋 残留问题（共 {len(remaining_issues)} 项）:")
        for idx, issue in enumerate(remaining_issues[:10], 1):
            print(f"  {idx}. [{issue.get('severity','?').upper()}] "
                  f"{issue.get('title', '')}")
        if len(remaining_issues) > 10:
            print(f"  ... 其余 {len(remaining_issues) - 10} 项省略")

    new_issues = vr.get("new_issues") or []
    if new_issues:
        print(f"\n⚠  新引入问题（共 {len(new_issues)} 项）:")
        for idx, issue in enumerate(new_issues[:10], 1):
            print(f"  {idx}. [{issue.get('severity','?').upper()}] "
                  f"{issue.get('title', '')}")
        if len(new_issues) > 10:
            print(f"  ... 其余 {len(new_issues) - 10} 项省略")

    suggestions = vr.get("improvement_suggestions") or []
    if suggestions:
        print(f"\n💡 改进建议:")
        for s in suggestions[:5]:
            print(f"  • {s}")
    print("=" * 60)


def _save_json_report(final_state, input_path: Path, output_dir: str, suffix: str) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{input_path.stem}_{suffix}_report.json"

    report = {
        "task_id": final_state.get("task_id"),
        "input_file": str(input_path),
        "parse_success": final_state.get("parse_success", False),
        "parse_error": final_state.get("parse_error"),
        "document": final_state["parsed_document"].to_dict() if final_state.get("parsed_document") else None,
        "retrieved_documents": final_state.get("retrieved_documents", []),
        "style_profile": final_state.get("style_profile"),
        "terminology_list": final_state.get("terminology_list", []),
        "review_issues": final_state.get("review_issues", []),
        "review_report": final_state.get("review_report"),
        "repair_actions": final_state.get("repair_actions", []),
        "repair_success": final_state.get("repair_success"),
        "repair_error": final_state.get("repair_error"),
        "output_docx_path": final_state.get("output_docx_path"),
        "validation_result": final_state.get("validation_result"),
        "validation_iterations": final_state.get("validation_iterations"),
        "hitl_required": final_state.get("hitl_required"),
        "hitl_completed": final_state.get("hitl_completed"),
        "repair_confirmations": final_state.get("repair_confirmations"),
        "new_introduced_issues": final_state.get("new_introduced_issues"),
        "step_logs": final_state.get("step_logs", []),
        "total_elapsed_seconds": final_state.get("total_elapsed_seconds", 0),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n📄 JSON 报告已保存: {report_path}")


# ============================================================
# 主入口
# ============================================================
async def main() -> int:
    args = parse_args()
    if args.command is None:
        print("请指定子命令。使用 --help 查看用法。")
        return 1

    mapping = {
        "parse": cmd_parse,
        "review": cmd_review,
        "repair": cmd_repair,
        "full": cmd_full,
        "validate": cmd_validate,
        "ingest": cmd_ingest,
        "stats": cmd_stats,
    }
    fn = mapping.get(args.command)
    if fn is None:
        print(f"未知子命令: {args.command}")
        return 1
    return await fn(args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
