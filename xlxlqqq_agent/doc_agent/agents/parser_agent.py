"""
DocGuard Agent - Parser Agent
==============================

职责：
1. 调用 DocxParser 解析 DOCX → StructuredDocument
2. 写入 state.parsed_document / parse_success / parse_error
3. 解析失败时设置 parse_error 但不抛异常（工作流据此跳转到报告节点）

输入 state 字段：
- input_file_path: 必填

输出 state 字段：
- parsed_document: StructuredDocument 实例
- parse_success: bool
- parse_error: Optional[str]
"""

from __future__ import annotations

from typing import Any, Optional

from agents.base import BaseAgent
from core.config import DocGuardConfig
from core.exceptions import DocumentParseError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import DocGuardState
from document.base_parser import parse_any
from document.parser import DocxParser


class ParserAgent(BaseAgent):
    """
    Parser Agent：DOCX 文档解析。

    封装 DocxParser，适配 LangGraph 节点协议。
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient],
        config: DocGuardConfig,
        logger=None,
        parser: Optional[DocxParser] = None,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端（Parser Agent 不使用 LLM，可为 None）
            config: 全局配置
            logger: 可选 logger
            parser: 可选的 DocxParser 实例（测试 mock 用）。
                    若传入 parser，则只走该 parser（DOCX-only 旧行为，兼容单测）；
                    否则使用 Phase 6 多格式入口 parse_any()，支持 DOCX / PDF / PPT / TXT fallback。
        """
        super().__init__(llm_client, config, logger or get_logger("agents.parser_agent"))
        self._parser = parser  # 若为 None 则使用 parse_any 多格式路由
        self._docx_parser_fallback = DocxParser()  # 当 state 中没有 format_hint 时兜底

    def agent_name(self) -> str:
        return "parser_agent"

    async def execute(self, state: DocGuardState) -> DocGuardState:
        """
        执行文档解析。

        - 显式注入 parser（旧行为）→ 只解析 DOCX
        - 默认：按 Phase 6 多格式入口 parse_any 路由（DOCX/PDF/PPT + fallback TXT）

        解析失败时不抛异常，而是将错误写入 state.parse_error，
        工作流的条件边会据此跳转到报告生成节点。
        """
        input_path = self._validate_state_field(state, "input_file_path")
        self.logger.info("[Parser] 开始解析: %s", input_path)

        try:
            format_hint = state.get("input_format") or None
            if self._parser is not None:
                # 兼容旧单测：强制使用注入的 parser（仅 DOCX 路径）
                structured_doc = self._parser.parse(input_path)
            else:
                # Phase 6 多格式入口
                structured_doc = parse_any(
                    input_path,
                    config=self.config,
                    format_hint=format_hint,
                )
            state["parsed_document"] = structured_doc
            state["parse_success"] = True
            state["parse_error"] = None

            stats = structured_doc.get_statistics()
            self.logger.info(
                "[Parser] 解析成功 | 段落=%d | 表格=%d | 图片=%d | 字数=%d",
                stats["paragraph_count"],
                stats["table_count"],
                stats["image_count"],
                stats["word_count"],
            )

        except DocumentParseError as e:
            self.logger.error("[Parser] 解析失败: %s", e)
            state["parsed_document"] = None
            state["parse_success"] = False
            state["parse_error"] = str(e)

        except Exception as e:
            # 兜底：未知异常也写入 state，不中断工作流
            self.logger.error("[Parser] 解析未知异常: %s", e, exc_info=True)
            state["parsed_document"] = None
            state["parse_success"] = False
            state["parse_error"] = f"未知解析异常: {e}"

        return state

    def _build_summary(self, state: DocGuardState) -> str:
        if state.get("parse_success") and state.get("parsed_document"):
            stats = state["parsed_document"].get_statistics()
            return (
                f"success=true, paragraphs={stats['paragraph_count']}, "
                f"tables={stats['table_count']}, images={stats['image_count']}"
            )
        return f"success=false, error={state.get('parse_error', 'unknown')}"
