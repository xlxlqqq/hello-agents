"""
DocGuard Agent - DOCX 写入器
==============================

设计要点：
1. 基于 StructuredDocument._docx_reference（原始 python-docx.Document）持久化
2. 修复阶段直接修改原始 docx 对象，Writer 负责保存到目标路径
3. 支持另存为新文件（保留原始文件不变）
4. 保存失败时抛出 DocumentWriteError，携带上下文
5. 提供保存路径规范化与目录自动创建

为何不在 Writer 中"重新构建"DOCX？
- 重新构建会丢失页眉页脚、目录、域、修订等复杂元素
- 直接保存修改后的原始对象能最大程度保留格式
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.exceptions import DocumentWriteError, wrap_exception
from core.logging_config import get_logger
from document.models import StructuredDocument

logger = get_logger("document.writer")


class DocxWriter:
    """
    DOCX 写入器：将 StructuredDocument 保存为 DOCX 文件。

    用法：
        writer = DocxWriter()
        output_path = writer.save(structured_doc, "output/repaired/output.docx")
    """

    def __init__(self) -> None:
        self.logger = get_logger("document.writer")

    def save(
        self,
        document: StructuredDocument,
        output_path: str,
        *,
        overwrite: bool = True,
    ) -> str:
        """
        保存文档为 DOCX 文件。

        Args:
            document: StructuredDocument 实例（必须包含 _docx_reference）
            output_path: 输出文件路径
            overwrite: 是否覆盖已存在文件，默认 True

        Returns:
            实际保存的绝对路径

        Raises:
            DocumentWriteError: 保存失败
        """
        if document._docx_reference is None:
            raise DocumentWriteError(
                "StructuredDocument 缺少 _docx_reference，无法保存（可能是从字典反序列化的）",
                context={"document_id": document.document_id},
            )

        out_path = Path(output_path).resolve()
        self.logger.info("保存 DOCX: %s", out_path)

        # 路径校验
        if out_path.exists() and not overwrite:
            raise DocumentWriteError(
                f"输出文件已存在且 overwrite=False: {out_path}",
                context={"output_path": str(out_path)},
            )

        # 后缀校验
        if out_path.suffix.lower() != ".docx":
            out_path = out_path.with_suffix(".docx")
            self.logger.warning("输出路径后缀非 .docx，已自动调整为: %s", out_path)

        # 确保父目录存在
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise wrap_exception(
                e,
                DocumentWriteError,
                f"创建输出目录失败: {e}",
                output_path=str(out_path),
            ) from e

        # 保存
        try:
            docx_doc = document._docx_reference
            docx_doc.save(str(out_path))
        except Exception as e:
            raise wrap_exception(
                e,
                DocumentWriteError,
                f"DOCX 保存失败: {e}",
                output_path=str(out_path),
            ) from e

        # 更新 StructuredDocument 的 file_path
        document.file_path = str(out_path)

        self.logger.info("DOCX 保存成功: %s", out_path)
        return str(out_path)

    def save_as(
        self,
        document: StructuredDocument,
        output_dir: str,
        filename: Optional[str] = None,
    ) -> str:
        """
        按目录 + 文件名方式保存。

        Args:
            document: StructuredDocument 实例
            output_dir: 输出目录
            filename: 文件名（None 时基于原始文件名生成）

        Returns:
            实际保存的绝对路径
        """
        if filename is None:
            # 基于原始文件名生成：原名_repaired.docx
            original_name = document.filename or "document.docx"
            stem = Path(original_name).stem
            filename = f"{stem}_repaired.docx"

        if not filename.endswith(".docx"):
            filename = f"{filename}.docx"

        output_path = Path(output_dir) / filename
        return self.save(document, str(output_path))


# ============================================================
# 便捷函数
# ============================================================
def save_docx(
    document: StructuredDocument,
    output_path: str,
    *,
    overwrite: bool = True,
) -> str:
    """
    便捷函数：保存 DOCX 文件。

    Args:
        document: StructuredDocument 实例
        output_path: 输出文件路径
        overwrite: 是否覆盖

    Returns:
        实际保存的绝对路径
    """
    return DocxWriter().save(document, output_path, overwrite=overwrite)
