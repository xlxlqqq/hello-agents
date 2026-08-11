"""
DocGuard Agent - 结构化文档对象模型
====================================

设计原则：
1. 与 python-docx 解耦：StructuredDocument 是纯数据载体，不依赖 docx 库
2. 完整保留格式信息：字体/字号/对齐/缩进/行距/标题层级/表格/图片
3. 可序列化：支持 to_dict / from_dict，便于日志与测试
4. 保留原始引用：_docx_reference 字段持有 python-docx 对象，用于修复回写
5. 稳定 ID：每个段落/表格/图片有唯一 ID，供 Review/Repair Agent 定位

python-docx 单位说明：
- 字号: Pt (磅)，1 Pt = 1/72 inch
- 缩进/间距: EMU (English Metric Unit)，1 Pt = 12700 EMU
- 图片尺寸: EMU
本模型统一转换为 Pt 存储，便于规则匹配与展示。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# ============================================================
# 辅助函数
# ============================================================
def generate_id(prefix: str = "el") -> str:
    """生成带前缀的唯一 ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 枚举定义
# ============================================================
class IssueCategory(str, Enum):
    """问题类别"""

    # 内容问题
    CONTENT_TYPO = "content_typo"                  # 错别字
    CONTENT_WRONG_WORD = "content_wrong_word"      # 错误用词
    CONTENT_TERMINOLOGY = "content_terminology"    # 术语不一致
    CONTENT_CONFLICT = "content_conflict"          # 前后概念冲突
    CONTENT_INCOMPLETE = "content_incomplete"      # 内容不完整
    CONTENT_GRAMMAR = "content_grammar"            # 语法错误

    # 格式问题
    FORMAT_FONT = "format_font"                    # 字体错误
    FORMAT_SIZE = "format_size"                    # 字号错误
    FORMAT_ALIGNMENT = "format_alignment"          # 对齐错误
    FORMAT_INDENT = "format_indent"                # 缩进错误
    FORMAT_SPACING = "format_spacing"              # 行距错误
    FORMAT_HEADING_LEVEL = "format_heading"        # 标题层级错误
    FORMAT_TABLE = "format_table"                  # 表格格式错误

    # 结构问题
    STRUCTURE_MISSING_SECTION = "structure_missing_section"  # 缺失章节
    STRUCTURE_ORDER = "structure_order"            # 章节顺序错误


class IssueSeverity(str, Enum):
    """问题严重程度"""

    CRITICAL = "critical"   # 必须修复
    MAJOR = "major"         # 建议修复
    MINOR = "minor"         # 可选修复
    INFO = "info"           # 提示信息


class RepairType(str, Enum):
    """修复动作类型"""

    REPLACE_TEXT = "replace_text"                  # 替换文本
    CHANGE_FONT = "change_font"                    # 修改字体
    CHANGE_SIZE = "change_size"                    # 修改字号
    CHANGE_BOLD = "change_bold"                    # 修改加粗
    CHANGE_ITALIC = "change_italic"                # 修改斜体
    CHANGE_COLOR = "change_color"                  # 修改颜色
    CHANGE_ALIGNMENT = "change_alignment"          # 修改对齐
    CHANGE_INDENT = "change_indent"                # 修改缩进
    CHANGE_SPACING = "change_spacing"              # 修改行距/段间距
    CHANGE_LINE_SPACING = "change_line_spacing"    # 修改行距
    APPLY_HEADING_STYLE = "apply_heading_style"    # 应用标题样式
    APPLY_BODY_STYLE = "apply_body_style"          # 应用正文样式
    ADD_COMMENT = "add_comment"                    # 添加批注（不改内容）


# ============================================================
# 样式模型
# ============================================================
@dataclass
class FontStyle:
    """
    字体样式（最小样式单元）。

    None 表示该属性未显式设置（继承自样式或文档默认）。
    """

    name: Optional[str] = None            # 字体名: "宋体", "Times New Roman"
    name_east_asian: Optional[str] = None # 东亚字体（中文）: "宋体", "微软雅黑"
    size_pt: Optional[float] = None       # 字号（磅）: 10.5, 12, 14
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    color_hex: Optional[str] = None       # RGB 十六进制: "FF0000"（无 # 前缀）

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_east_asian": self.name_east_asian,
            "size_pt": self.size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "color_hex": self.color_hex,
        }


@dataclass
class ParagraphFormat:
    """
    段落格式。

    None 表示该属性未显式设置（继承自样式）。
    所有长度单位为磅（Pt）。
    """

    alignment: Optional[str] = None          # left/center/right/justify
    left_indent_pt: Optional[float] = None   # 左缩进
    right_indent_pt: Optional[float] = None  # 右缩进
    first_line_indent_pt: Optional[float] = None  # 首行缩进
    line_spacing: Optional[float] = None     # 行距（倍数，如 1.5）或磅值
    line_spacing_rule: Optional[str] = None  # "multiple" / "exact" / "at_least"
    space_before_pt: Optional[float] = None  # 段前间距
    space_after_pt: Optional[float] = None   # 段后间距

    def to_dict(self) -> dict[str, Any]:
        return {
            "alignment": self.alignment,
            "left_indent_pt": self.left_indent_pt,
            "right_indent_pt": self.right_indent_pt,
            "first_line_indent_pt": self.first_line_indent_pt,
            "line_spacing": self.line_spacing,
            "line_spacing_rule": self.line_spacing_rule,
            "space_before_pt": self.space_before_pt,
            "space_after_pt": self.space_after_pt,
        }


# ============================================================
# 内容元素
# ============================================================
@dataclass
class Run:
    """
    文本片段（最小样式单元）。

    一个 Run 对应 Word 中一段相同样式的连续文本。
    """

    text: str
    style: FontStyle = field(default_factory=FontStyle)
    run_index: int = 0                      # 在段落中的索引
    run_id: str = field(default_factory=lambda: generate_id("run"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_index": self.run_index,
            "text": self.text,
            "style": self.style.to_dict(),
        }


@dataclass
class Paragraph:
    """段落"""

    paragraph_id: str = field(default_factory=lambda: generate_id("para"))
    paragraph_index: int = 0                # 在文档所有段落中的全局索引
    text: str = ""
    runs: list[Run] = field(default_factory=list)
    style: ParagraphFormat = field(default_factory=ParagraphFormat)
    style_name: Optional[str] = None        # Word 内置样式名: "Heading 1", "Normal"
    heading_level: Optional[int] = None     # None=正文, 1-9=标题层级
    is_list: bool = False                   # 是否是列表项
    list_level: Optional[int] = None        # 列表层级（0=顶级）
    list_style_name: Optional[str] = None   # 列表样式名: "List Bullet", "List Number"
    in_table: bool = False                  # 是否在表格内
    parent_table_id: Optional[str] = None   # 所属表格 ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_id": self.paragraph_id,
            "paragraph_index": self.paragraph_index,
            "text": self.text,
            "runs": [r.to_dict() for r in self.runs],
            "style": self.style.to_dict(),
            "style_name": self.style_name,
            "heading_level": self.heading_level,
            "is_list": self.is_list,
            "list_level": self.list_level,
            "list_style_name": self.list_style_name,
            "in_table": self.in_table,
            "parent_table_id": self.parent_table_id,
        }


@dataclass
class TableCell:
    """表格单元格"""

    cell_id: str = field(default_factory=lambda: generate_id("cell"))
    text: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    row_index: int = 0
    col_index: int = 0
    row_span: int = 1
    col_span: int = 1
    shading_color: Optional[str] = None     # 单元格底色: "FFFF00"

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "text": self.text,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "row_index": self.row_index,
            "col_index": self.col_index,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "shading_color": self.shading_color,
        }


@dataclass
class TableRow:
    """表格行"""

    row_id: str = field(default_factory=lambda: generate_id("row"))
    row_index: int = 0
    cells: list[TableCell] = field(default_factory=list)
    is_header: bool = False                 # 是否是表头行

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "row_index": self.row_index,
            "cells": [c.to_dict() for c in self.cells],
            "is_header": self.is_header,
        }


@dataclass
class Table:
    """表格"""

    table_id: str = field(default_factory=lambda: generate_id("tbl"))
    table_index: int = 0                    # 在文档所有表格中的索引
    rows: list[TableRow] = field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    style_name: Optional[str] = None        # 表格样式名: "Table Grid"
    has_borders: bool = True
    alignment: Optional[str] = None         # 表格对齐: left/center/right

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "table_index": self.table_index,
            "rows": [r.to_dict() for r in self.rows],
            "row_count": self.row_count,
            "col_count": self.col_count,
            "style_name": self.style_name,
            "has_borders": self.has_borders,
            "alignment": self.alignment,
        }


@dataclass
class ImageInfo:
    """图片信息"""

    image_id: str = field(default_factory=lambda: generate_id("img"))
    filename: Optional[str] = None
    width_pt: Optional[float] = None        # 宽度（磅）
    height_pt: Optional[float] = None       # 高度（磅）
    width_emu: Optional[int] = None         # 原始 EMU 宽度
    height_emu: Optional[int] = None        # 原始 EMU 高度
    content_type: Optional[str] = None      # image/png, image/jpeg
    paragraph_id: Optional[str] = None      # 所在段落 ID
    paragraph_index: Optional[int] = None   # 所在段落索引

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "filename": self.filename,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "width_emu": self.width_emu,
            "height_emu": self.height_emu,
            "content_type": self.content_type,
            "paragraph_id": self.paragraph_id,
            "paragraph_index": self.paragraph_index,
        }


# ============================================================
# 顶层文档对象
# ============================================================
@dataclass
class StructuredDocument:
    """
    结构化文档对象（核心数据模型）。

    由 DocxParser 从 python-docx.Document 解析得到。
    Repair Agent 通过 _docx_reference 直接操作原始文档对象，
    修改后由 DocxWriter 持久化为新的 DOCX 文件。

    设计说明：
    - paragraphs / tables / images 分别存储，但保留索引关联
    - _docx_reference 是可选的原始 python-docx.Document 引用，
      用于修复阶段直接操作底层对象，避免格式丢失
    """

    document_id: str = field(default_factory=lambda: generate_id("doc"))
    file_path: str = ""
    filename: str = ""

    # 源格式（Phase 6 多格式）："docx" / "pdf" / "ppt"，默认 "docx" 兼容旧数据
    source_format: str = "docx"

    # 内容元素
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)

    # 文档元数据
    title: Optional[str] = None
    author: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    page_count: Optional[int] = None
    word_count: Optional[int] = None

    # 文档级样式统计（解析时填充）
    body_style_stats: dict[str, Any] = field(default_factory=dict)
    heading_style_stats: dict[int, dict[str, Any]] = field(default_factory=dict)

    # 原始 python-docx 对象引用（不序列化，仅用于修复回写）
    _docx_reference: Any = field(default=None, repr=False, compare=False)

    # ============================================================
    # 便捷查询方法
    # ============================================================
    def get_heading_outline(self) -> list[dict[str, Any]]:
        """
        获取文档大纲（仅标题段落）。

        Returns:
            标题列表，每项包含 paragraph_id, level, text, index
        """
        outline: list[dict[str, Any]] = []
        for p in self.paragraphs:
            if p.heading_level is not None:
                outline.append({
                    "paragraph_id": p.paragraph_id,
                    "paragraph_index": p.paragraph_index,
                    "level": p.heading_level,
                    "text": p.text,
                })
        return outline

    def get_paragraph_by_id(self, paragraph_id: str) -> Optional[Paragraph]:
        """按 ID 查找段落"""
        for p in self.paragraphs:
            if p.paragraph_id == paragraph_id:
                return p
        return None

    def get_paragraph_by_index(self, index: int) -> Optional[Paragraph]:
        """按全局索引查找段落"""
        if 0 <= index < len(self.paragraphs):
            return self.paragraphs[index]
        return None

    def get_table_by_id(self, table_id: str) -> Optional[Table]:
        """按 ID 查找表格"""
        for t in self.tables:
            if t.table_id == table_id:
                return t
        return None

    def get_full_text(self) -> str:
        """获取文档全文（段落文本，不含表格）"""
        return "\n".join(p.text for p in self.paragraphs if p.text.strip())

    def get_statistics(self) -> dict[str, Any]:
        """获取文档统计信息"""
        return {
            "paragraph_count": len(self.paragraphs),
            "table_count": len(self.tables),
            "image_count": len(self.images),
            "heading_count": sum(1 for p in self.paragraphs if p.heading_level),
            "word_count": self.word_count,
            "page_count": self.page_count,
        }

    def to_dict(self, include_docx_ref: bool = False) -> dict[str, Any]:
        """
        序列化为字典（用于日志、JSON 报告）。

        Args:
            include_docx_ref: 是否包含 _docx_reference（通常为 False）
        """
        result = {
            "document_id": self.document_id,
            "file_path": self.file_path,
            "filename": self.filename,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "tables": [t.to_dict() for t in self.tables],
            "images": [i.to_dict() for i in self.images],
            "title": self.title,
            "author": self.author,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "page_count": self.page_count,
            "word_count": self.word_count,
            "body_style_stats": self.body_style_stats,
            "heading_style_stats": self.heading_style_stats,
            "statistics": self.get_statistics(),
        }
        if include_docx_ref:
            result["_has_docx_reference"] = self._docx_reference is not None
        return result
