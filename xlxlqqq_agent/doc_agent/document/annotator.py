"""
DocGuard Agent - 文档标注器
============================

PRD 要求："repaired_document.docx 文档必须要在修改的地方做好明确标注"

本模块实现三种标注方式，可组合使用：
1. 文本高亮：在修改的 Run 上添加黄色底色（最直观）
2. Word 批注：在修改位置插入批注，说明原文与修改后内容
3. 修订模式：可选开启 Word 修订（track changes），保留修改痕迹

设计要点：
1. 直接操作 python-docx 底层 XML（OOXML），因为 python-docx 高级 API
   不直接支持高亮颜色、批注等高级特性
2. 通过 paragraph_id / paragraph_index / run_index 精确定位修改位置
3. 标注失败不中断主流程，记录到日志并继续
4. 每个标注动作都生成 AnnotationRecord，便于报告追溯

OOXML 关键元素：
- w:highlight：文本高亮（在 rPr 内）
- w:comment：批注（需在 comments part 注册）
- w:ins / w:del：修订模式下的插入/删除标记
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from docx.document import Document as _DocumentType
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.oxml import OxmlElement

from core.exceptions import AnnotationError, wrap_exception
from core.logging_config import get_logger
from document.models import (
    FontStyle,
    Paragraph,
    RepairType,
    Run,
    StructuredDocument,
)

logger = get_logger("document.annotator")


# ============================================================
# 标注记录
# ============================================================
@dataclass
class AnnotationRecord:
    """单次标注记录"""

    annotation_id: str
    target_paragraph_id: str
    annotation_type: str = "mixed"   # "highlight" / "comment" / "revision" / "mixed"
    target_run_index: Optional[int] = None
    content: Optional[str] = None    # 批注内容
    success: bool = True
    error_message: Optional[str] = None


# ============================================================
# 标注配置
# ============================================================
@dataclass
class AnnotationConfig:
    """标注配置"""

    enable_highlight: bool = True           # 文本高亮
    highlight_color: str = "yellow"         # yellow / green / cyan / red
    enable_comment: bool = True             # Word 批注
    comment_author: str = "DocGuard Agent"  # 批注作者
    enable_track_changes: bool = False      # 修订模式（默认关闭，避免与高亮冲突）
    comment_prefix: str = "[DocGuard]"      # 批注内容前缀


# ============================================================
# 文档标注器
# ============================================================
class DocxAnnotator:
    """
    文档标注器：在 DOCX 修改位置添加标注（高亮 + 批注）。

    用法：
        annotator = DocxAnnotator()
        annotator.annotate_modification(
            docx_doc=structured_doc._docx_reference,
            paragraph_index=5,
            run_index=0,
            comment_text="原文: 错别字 → 修改: 正确字",
        )
    """

    def __init__(self, config: Optional[AnnotationConfig] = None) -> None:
        self.config = config or AnnotationConfig()
        self.logger = get_logger("document.annotator")
        self._comment_id_counter: int = 0  # 批注 ID 计数器

    # ============================================================
    # 公共方法
    # ============================================================
    def annotate_modification(
        self,
        docx_doc: Any,
        paragraph_index: int,
        run_index: Optional[int],
        comment_text: str,
        *,
        paragraph_id: Optional[str] = None,
    ) -> AnnotationRecord:
        """
        在指定位置添加标注（高亮 + 批注）。

        Args:
            docx_doc: python-docx Document 实例
            paragraph_index: 段落全局索引
            run_index: Run 索引（None 表示整段）
            comment_text: 批注内容
            paragraph_id: 段落 ID（用于记录，可选）

        Returns:
            AnnotationRecord 实例
        """
        ann_id = f"ann_{self._comment_id_counter}"
        self._comment_id_counter += 1

        target_para_id = paragraph_id or f"para_idx_{paragraph_index}"
        record = AnnotationRecord(
            annotation_id=ann_id,
            target_paragraph_id=target_para_id,
            target_run_index=run_index,
            annotation_type="mixed",
            content=comment_text,
        )

        try:
            # 定位段落
            paragraph = self._get_paragraph(docx_doc, paragraph_index)
            if paragraph is None:
                raise AnnotationError(
                    f"无法定位段落: index={paragraph_index}",
                    context={"paragraph_index": paragraph_index},
                )

            # 1. 高亮
            if self.config.enable_highlight:
                self._apply_highlight(paragraph, run_index)

            # 2. 批注
            if self.config.enable_comment:
                self._add_comment(docx_doc, paragraph, run_index, comment_text)

            self.logger.debug(
                "标注成功: paragraph_index=%d run_index=%s comment=%s",
                paragraph_index, run_index, comment_text[:50],
            )

        except AnnotationError as e:
            record.success = False
            record.error_message = str(e)
            self.logger.warning("标注失败（不中断主流程）: %s", e)
        except Exception as e:
            record.success = False
            record.error_message = str(e)
            self.logger.warning(
                "标注异常（不中断主流程）: %s", e, exc_info=True
            )

        return record

    def annotate_paragraph_range(
        self,
        docx_doc: Any,
        paragraph_index: int,
        char_start: int,
        char_end: int,
        comment_text: str,
        *,
        paragraph_id: Optional[str] = None,
    ) -> AnnotationRecord:
        """
        在段落内的字符范围添加标注。

        用于"问题定位到字符级"的场景（如错别字）。
        会将指定字符范围拆分为独立 Run 并高亮。

        Args:
            docx_doc: python-docx Document 实例
            paragraph_index: 段落索引
            char_start: 起始字符位置（含）
            char_end: 结束字符位置（不含）
            comment_text: 批注内容

        Returns:
            AnnotationRecord 实例
        """
        ann_id = f"ann_{self._comment_id_counter}"
        self._comment_id_counter += 1

        target_para_id = paragraph_id or f"para_idx_{paragraph_index}"
        record = AnnotationRecord(
            annotation_id=ann_id,
            target_paragraph_id=target_para_id,
            annotation_type="range_highlight",
            content=comment_text,
        )

        try:
            paragraph = self._get_paragraph(docx_doc, paragraph_index)
            if paragraph is None:
                raise AnnotationError(
                    f"无法定位段落: index={paragraph_index}",
                    context={"paragraph_index": paragraph_index},
                )

            # 拆分 Run，使指定字符范围成为独立 Run
            target_run = self._split_run_at_range(
                paragraph, char_start, char_end
            )
            if target_run is not None:
                if self.config.enable_highlight:
                    self._apply_highlight_to_run(target_run)
                if self.config.enable_comment:
                    self._add_comment(docx_doc, paragraph, None, comment_text)

            self.logger.debug(
                "范围标注成功: paragraph=%d chars=[%d:%d] comment=%s",
                paragraph_index, char_start, char_end, comment_text[:50],
            )

        except Exception as e:
            record.success = False
            record.error_message = str(e)
            self.logger.warning("范围标注失败（不中断主流程）: %s", e, exc_info=True)

        return record

    def enable_track_changes(self, docx_doc: Any) -> None:
        """
        开启 Word 修订模式（track changes）。

        开启后，所有对文档的修改都会被标记为"修订"，
        用户在 Word 中可以"接受"或"拒绝"每个修改。

        注意：与高亮标注可能冲突，建议二选一。

        Args:
            docx_doc: python-docx Document 实例
        """
        try:
            settings = docx_doc.settings.element
            # 移除已有 trackChanges
            existing = settings.find(qn("w:trackChanges"))
            if existing is not None:
                settings.remove(existing)
            # 添加 trackChanges 元素
            track_changes = OxmlElement("w:trackChanges")
            settings.append(track_changes)
            self.logger.info("已开启 Word 修订模式（track changes）")
        except Exception as e:
            raise wrap_exception(
                e,
                AnnotationError,
                f"开启修订模式失败: {e}",
            ) from e

    # ============================================================
    # 内部实现：定位
    # ============================================================
    @staticmethod
    def _get_paragraph(docx_doc: Any, paragraph_index: int) -> Any:
        """按索引获取 python-docx Paragraph 实例"""
        try:
            return docx_doc.paragraphs[paragraph_index]
        except IndexError:
            return None
        except Exception:
            return None

    # ============================================================
    # 内部实现：高亮
    # ============================================================
    def _apply_highlight(self, paragraph: Any, run_index: Optional[int]) -> None:
        """在段落的指定 Run 上应用高亮"""
        if run_index is not None:
            runs = paragraph.runs
            if 0 <= run_index < len(runs):
                self._apply_highlight_to_run(runs[run_index])
        else:
            # 整段高亮
            for run in paragraph.runs:
                self._apply_highlight_to_run(run)

    def _apply_highlight_to_run(self, run: Any) -> None:
        """在单个 Run 上应用高亮颜色"""
        rPr = run._element.get_or_add_rPr()
        # 移除已有 highlight
        existing = rPr.find(qn("w:highlight"))
        if existing is not None:
            rPr.remove(existing)
        # 添加新 highlight
        highlight = OxmlElement("w:highlight")
        highlight.set(qn("w:val"), self.config.highlight_color)
        rPr.append(highlight)

    # ============================================================
    # 内部实现：批注
    # ============================================================
    def _add_comment(
        self,
        docx_doc: Any,
        paragraph: Any,
        run_index: Optional[int],
        comment_text: str,
    ) -> None:
        """
        在段落上添加 Word 批注。

        python-docx 不直接支持批注，需通过 OOXML 操作：
        1. 在 comments part 中注册批注内容
        2. 在段落中插入 commentRangeStart / commentRangeEnd / commentReference

        实现简化：在段落开头插入 commentRangeStart，末尾插入 commentRangeEnd，
        并在末尾追加 commentReference。批注内容通过 comments part 注入。

        注意：完整的批注实现较为复杂，这里采用"批注文本直接作为
        段落末尾的红色括号注释"的简化方案，保证兼容性。
        更完整的实现可使用 docx-comments 等扩展库。
        """
        full_comment = f"{self.config.comment_prefix} {comment_text}"

        # 在段落末尾追加红色标注 Run
        from docx.shared import RGBColor
        comment_run = paragraph.add_run(f"  {full_comment}")
        comment_run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)  # 深红
        comment_run.font.size = Pt(9)
        comment_run.italic = True

    # ============================================================
    # 内部实现：Run 拆分（用于字符级标注）
    # ============================================================
    def _split_run_at_range(
        self,
        paragraph: Any,
        char_start: int,
        char_end: int,
    ) -> Optional[Any]:
        """
        将段落在指定字符范围处拆分，使该范围成为独立 Run。

        实现思路：
        1. 遍历所有 Run，累计字符位置
        2. 找到包含 [char_start, char_end) 的 Run
        3. 必要时拆分该 Run

        Args:
            paragraph: python-docx Paragraph
            char_start: 起始字符位置
            char_end: 结束字符位置

        Returns:
            目标 Run 对象（拆分后），失败返回 None
        """
        runs = paragraph.runs
        if not runs:
            return None

        # 计算每个 Run 的字符范围
        current_pos = 0
        target_run = None
        target_start_in_run = 0
        target_end_in_run = 0

        for run in runs:
            run_len = len(run.text)
            run_start = current_pos
            run_end = current_pos + run_len

            if run_start <= char_start < run_end:
                target_run = run
                target_start_in_run = char_start - run_start
                # 确定结束位置（可能在同一 Run 内或后续 Run）
                if char_end <= run_end:
                    target_end_in_run = char_end - run_start
                else:
                    target_end_in_run = run_len
                break

            current_pos = run_end

        if target_run is None:
            return None

        # 简化处理：只处理范围在同一 Run 内的情况
        # 完整跨 Run 拆分较复杂，此处仅高亮目标 Run
        if target_start_in_run == 0 and target_end_in_run == len(target_run.text):
            return target_run

        # 拆分 Run：前段 + 目标段 + 后段
        # python-docx 不直接支持 Run 拆分，需操作 XML
        full_text = target_run.text
        before_text = full_text[:target_start_in_run]
        target_text = full_text[target_start_in_run:target_end_in_run]
        after_text = full_text[target_end_in_run:]

        # 修改原 Run 为目标段文本
        target_run.text = target_text
        # 在原 Run 前后插入新 Run
        if before_text:
            before_run = self._insert_run_before(paragraph, target_run, before_text)
            self._copy_run_format(target_run, before_run)
        if after_text:
            after_run = self._insert_run_after(paragraph, target_run, after_text)
            self._copy_run_format(target_run, after_run)

        return target_run

    @staticmethod
    def _insert_run_before(paragraph: Any, ref_run: Any, text: str) -> Any:
        """在 ref_run 之前插入新 Run"""
        new_run_elem = OxmlElement("w:r")
        t_elem = OxmlElement("w:t")
        t_elem.text = text
        t_elem.set(qn("xml:space"), "preserve")
        new_run_elem.append(t_elem)
        ref_run._element.addprevious(new_run_elem)
        # 返回 docx Run 对象
        from docx.text.run import Run as DocxRun
        return DocxRun(new_run_elem, paragraph)

    @staticmethod
    def _insert_run_after(paragraph: Any, ref_run: Any, text: str) -> Any:
        """在 ref_run 之后插入新 Run"""
        new_run_elem = OxmlElement("w:r")
        t_elem = OxmlElement("w:t")
        t_elem.text = text
        t_elem.set(qn("xml:space"), "preserve")
        new_run_elem.append(t_elem)
        ref_run._element.addnext(new_run_elem)
        from docx.text.run import Run as DocxRun
        return DocxRun(new_run_elem, paragraph)

    @staticmethod
    def _copy_run_format(src_run: Any, dst_run: Any) -> None:
        """复制 Run 格式（粗略复制 rPr）"""
        try:
            src_rPr = src_run._element.rPr
            if src_rPr is not None:
                # 深拷贝 rPr
                from copy import deepcopy
                dst_run._element.rPr = deepcopy(src_rPr)
        except Exception:
            pass


# ============================================================
# 便捷函数
# ============================================================
def annotate_repair(
    docx_doc: Any,
    paragraph_index: int,
    comment_text: str,
    run_index: Optional[int] = None,
    config: Optional[AnnotationConfig] = None,
) -> AnnotationRecord:
    """
    便捷函数：在修改位置添加标注。

    Args:
        docx_doc: python-docx Document 实例
        paragraph_index: 段落索引
        comment_text: 批注内容
        run_index: Run 索引（None=整段）
        config: 标注配置

    Returns:
        AnnotationRecord 实例
    """
    annotator = DocxAnnotator(config)
    return annotator.annotate_modification(
        docx_doc=docx_doc,
        paragraph_index=paragraph_index,
        run_index=run_index,
        comment_text=comment_text,
    )
