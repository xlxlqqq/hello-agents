"""
DocGuard Agent - 知识库文档摄取器
==================================

职责：
1. 扫描 knowledge_docs/ 目录下所有 DOCX
2. 解析为 StructuredDocument
3. 按章节/段落切分为语义块（chunk）
4. 调用 EmbeddingClient 向量化
5. 写入 ChromaDB VectorStore
6. 返回 IngestResult 统计信息

设计要点：
1. 切分策略：以标题为分界，将每段连续段落归入同一 chunk
   - 一个 chunk 通常对应一个章节的内容
   - 过长 chunk 自动按 token 长度二次切分
   - 每个 chunk 携带元数据（source_file/section/chunk_index）
2. 幂等性：基于 source_file 元数据 upsert，重复 ingest 不会产生重复记录
3. 增量更新：删除该文件旧记录后重新写入
4. 失败容错：单文件失败不影响其他文件

数据流：
    knowledge_docs/report.docx
        ↓ DocxParser.parse
    StructuredDocument
        ↓ chunk_by_section
    list[Chunk]
        ↓ EmbeddingClient.embed_texts
    list[VectorRecord]
        ↓ VectorStore.add_documents
    ChromaDB
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.exceptions import KnowledgeBaseError, wrap_exception
from core.logging_config import get_logger
from document.models import StructuredDocument
from document.parser import DocxParser
from knowledge.vector_store import VectorRecord, VectorStore, generate_record_id

logger = get_logger("knowledge.ingestor")


# ============================================================
# 数据模型
# ============================================================
@dataclass
class Chunk:
    """
    文档切分后的语义块。

    一个 Chunk 对应 ChromaDB 中的一条记录。
    """

    chunk_id: str
    text: str
    source_file: str               # 源文件名
    section: str                   # 所属章节标题
    heading_level: Optional[int]   # 章节标题层级
    chunk_index: int               # 在源文件中的 chunk 序号
    paragraph_indices: list[int]   # 包含的段落索引
    char_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """转为 ChromaDB metadata（必须是基本类型）"""
        return {
            "source_file": self.source_file,
            "section": self.section,
            "heading_level": self.heading_level if self.heading_level is not None else -1,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
            "paragraph_indices": ",".join(str(i) for i in self.paragraph_indices),
        }


@dataclass
class IngestResult:
    """摄取结果统计"""

    total_files: int = 0
    success_files: int = 0
    failed_files: int = 0
    total_chunks: int = 0
    total_embeddings: int = 0
    failed_files_list: list[str] = field(default_factory=list)
    per_file_stats: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_files": self.total_files,
            "success_files": self.success_files,
            "failed_files": self.failed_files,
            "total_chunks": self.total_chunks,
            "total_embeddings": self.total_embeddings,
            "failed_files_list": self.failed_files_list,
            "per_file_stats": self.per_file_stats,
            "elapsed_seconds": self.elapsed_seconds,
        }


# ============================================================
# 切分器
# ============================================================
class DocumentChunker:
    """
    文档切分器：按章节切分 StructuredDocument 为 Chunk 列表。

    切分策略：
    1. 遍历段落，遇到标题（heading_level 不为 None）则开始新 chunk
    2. 累积该章节下的正文段落，直到下一个标题
    3. 若 chunk 文本超过 max_chars，按段落边界二次切分
    4. 表格内容单独成 chunk
    """

    def __init__(
        self,
        max_chars: int = 1500,
        min_chars: int = 50,
    ) -> None:
        """
        Args:
            max_chars: 单个 chunk 最大字符数（超出则二次切分）
            min_chars: 单个 chunk 最小字符数（小于则合并到上一个 chunk）
        """
        self.max_chars = max_chars
        self.min_chars = min_chars

    def chunk_document(self, doc: StructuredDocument) -> list[Chunk]:
        """
        切分文档为 Chunk 列表。

        Args:
            doc: StructuredDocument 实例

        Returns:
            Chunk 列表
        """
        chunks: list[Chunk] = []
        current_section = "（文档开头）"
        current_heading_level: Optional[int] = None
        current_paragraphs: list[tuple[int, str, Optional[int]]] = []  # (idx, text, level)
        chunk_counter = 0

        def flush_current() -> None:
            nonlocal chunk_counter, current_section, current_heading_level, current_paragraphs
            if not current_paragraphs:
                return
            # 合并段落文本
            full_text = "\n".join(t for _, t, _ in current_paragraphs if t.strip())
            if not full_text.strip():
                current_paragraphs = []
                return
            # 若过长，二次切分
            if len(full_text) > self.max_chars:
                sub_texts = self._split_by_size(full_text, self.max_chars)
                for sub_idx, sub_text in enumerate(sub_texts):
                    chunks.append(Chunk(
                        chunk_id=generate_record_id("chunk"),
                        text=sub_text,
                        source_file=doc.filename,
                        section=f"{current_section} (Part {sub_idx + 1})" if len(sub_texts) > 1 else current_section,
                        heading_level=current_heading_level,
                        chunk_index=chunk_counter,
                        paragraph_indices=[p_idx for p_idx, _, _ in current_paragraphs],
                        char_count=len(sub_text),
                    ))
                    chunk_counter += 1
            else:
                chunks.append(Chunk(
                    chunk_id=generate_record_id("chunk"),
                    text=full_text,
                    source_file=doc.filename,
                    section=current_section,
                    heading_level=current_heading_level,
                    chunk_index=chunk_counter,
                    paragraph_indices=[p_idx for p_idx, _, _ in current_paragraphs],
                    char_count=len(full_text),
                ))
                chunk_counter += 1
            current_paragraphs = []

        # 遍历段落
        for para in doc.paragraphs:
            if not para.text.strip():
                continue
            if para.heading_level is not None:
                # 遇到新标题，flush 上一节
                flush_current()
                current_section = para.text
                current_heading_level = para.heading_level
                current_paragraphs.append((para.paragraph_index, para.text, para.heading_level))
            else:
                current_paragraphs.append((para.paragraph_index, para.text, None))

        # flush 最后一节
        flush_current()

        # 表格单独成 chunk
        for table in doc.tables:
            table_text = self._table_to_text(table)
            if table_text.strip():
                chunks.append(Chunk(
                    chunk_id=generate_record_id("chunk"),
                    text=table_text,
                    source_file=doc.filename,
                    section=f"{current_section} (表格)",
                    heading_level=None,
                    chunk_index=chunk_counter,
                    paragraph_indices=[],
                    char_count=len(table_text),
                    metadata={"element_type": "table", "table_id": table.table_id},
                ))
                chunk_counter += 1

        # 合并过短的 chunk 到上一个
        chunks = self._merge_short_chunks(chunks)

        logger.debug(
            "文档切分完成: %s | chunks=%d | max_chars=%d",
            doc.filename, len(chunks), self.max_chars,
        )
        return chunks

    def _split_by_size(self, text: str, max_chars: int) -> list[str]:
        """按大小二次切分（优先在句号/换行处断开）"""
        if len(text) <= max_chars:
            return [text]
        parts: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end >= len(text):
                parts.append(text[start:])
                break
            # 优先在句号/换行处断开
            for sep in ["\n", "。", "！", "？", ".", "!", "?", "；", ";"]:
                last_sep = text.rfind(sep, start, end)
                if last_sep > start + self.min_chars:
                    end = last_sep + 1
                    break
            parts.append(text[start:end])
            start = end
        return parts

    def _merge_short_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """合并过短的 chunk 到上一个"""
        if len(chunks) <= 1:
            return chunks
        merged: list[Chunk] = [chunks[0]]
        for c in chunks[1:]:
            if c.char_count < self.min_chars and merged:
                prev = merged[-1]
                prev.text = prev.text + "\n" + c.text
                prev.char_count = len(prev.text)
                prev.paragraph_indices.extend(c.paragraph_indices)
            else:
                merged.append(c)
        # 重新编号 chunk_index
        for i, c in enumerate(merged):
            c.chunk_index = i
        return merged

    @staticmethod
    def _table_to_text(table) -> str:
        """表格转为文本（Markdown 风格）"""
        lines: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append(" | ".join(cells))
        return "\n".join(lines)


# ============================================================
# 摄取器
# ============================================================
class KnowledgeIngestor:
    """
    知识库摄取器：扫描目录 → 解析 → 切分 → 向量化 → 入库。

    用法：
        ingestor = KnowledgeIngestor(embedding_client, vector_store)
        result = await ingestor.ingest_directory("knowledge_docs/")
    """

    def __init__(
        self,
        embedding_client,
        vector_store: VectorStore,
        *,
        parser: Optional[DocxParser] = None,
        chunker: Optional[DocumentChunker] = None,
    ) -> None:
        """
        Args:
            embedding_client: EmbeddingClient 实例（需实现 embed_texts）
            vector_store: VectorStore 实例
            parser: 可选 DocxParser（默认新建）
            chunker: 可选 DocumentChunker
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.parser = parser or DocxParser()
        self.chunker = chunker or DocumentChunker()
        self.logger = get_logger("knowledge.ingestor")

    async def ingest_directory(
        self,
        directory: str,
        *,
        file_pattern: str = "*.docx",
        clear_existing: bool = False,
    ) -> IngestResult:
        """
        摄取整个目录下的 DOCX 文件。

        Args:
            directory: 目录路径
            file_pattern: 文件 glob 模式
            clear_existing: 是否先清空 collection（重建知识库）

        Returns:
            IngestResult 统计
        """
        import time
        start_time = time.time()
        dir_path = Path(directory)
        if not dir_path.exists():
            raise KnowledgeBaseError(
                f"知识库目录不存在: {directory}",
                context={"directory": directory},
            )

        # 可选清空
        if clear_existing:
            self.logger.warning("清空 collection 后重建知识库")
            self.vector_store.reset()

        # 扫描文件
        files = sorted(dir_path.glob(file_pattern))
        self.logger.info("扫描到 %d 个 DOCX 文件: %s", len(files), dir_path)

        result = IngestResult(total_files=len(files))

        for file_path in files:
            try:
                chunk_count = await self.ingest_file(str(file_path))
                result.success_files += 1
                result.total_chunks += chunk_count
                result.per_file_stats[file_path.name] = chunk_count
                self.logger.info(
                    "✓ 摄取成功: %s | chunks=%d", file_path.name, chunk_count
                )
            except Exception as e:
                result.failed_files += 1
                result.failed_files_list.append(file_path.name)
                self.logger.error(
                    "✗ 摄取失败: %s | error=%s", file_path.name, e, exc_info=True
                )

        result.total_embeddings = result.total_chunks
        result.elapsed_seconds = round(time.time() - start_time, 2)

        self.logger.info(
            "知识库摄取完成 | 成功=%d 失败=%d chunks=%d 耗时=%.2fs",
            result.success_files, result.failed_files,
            result.total_chunks, result.elapsed_seconds,
        )
        return result

    async def ingest_file(self, file_path: str) -> int:
        """
        摄取单个 DOCX 文件。

        步骤：
        1. 解析 DOCX
        2. 切分为 chunks
        3. 向量化
        4. 删除该文件旧记录（增量更新）
        5. 写入新记录

        Args:
            file_path: DOCX 文件路径

        Returns:
            写入的 chunk 数

        Raises:
            KnowledgeBaseError: 摄取失败
        """
        path = Path(file_path)
        self.logger.info("开始摄取: %s", path.name)

        # 1. 解析
        try:
            doc = self.parser.parse(str(path))
        except Exception as e:
            raise wrap_exception(
                e,
                KnowledgeBaseError,
                f"解析失败: {path.name}: {e}",
                file_path=str(path),
            ) from e

        # 2. 切分
        chunks = self.chunker.chunk_document(doc)
        if not chunks:
            self.logger.warning("文件无可摄取内容: %s", path.name)
            return 0

        # 3. 向量化
        texts = [c.text for c in chunks]
        try:
            embeddings = await self.embedding_client.embed_texts(texts)
        except Exception as e:
            raise wrap_exception(
                e,
                KnowledgeBaseError,
                f"向量化失败: {path.name}: {e}",
                file_path=str(path),
                chunk_count=len(chunks),
            ) from e

        if len(embeddings) != len(chunks):
            raise KnowledgeBaseError(
                f"向量化数量不匹配: 期望 {len(chunks)}，实际 {len(embeddings)}",
                file_path=str(path),
            )

        # 4. 构建 VectorRecord
        records = [
            VectorRecord(
                id=c.chunk_id,
                document=c.text,
                embedding=emb,
                metadata=c.to_metadata(),
            )
            for c, emb in zip(chunks, embeddings)
        ]

        # 5. 删除旧记录（增量更新）
        try:
            self.vector_store.delete_by_metadata(source_file=path.name)
        except Exception as e:
            self.logger.warning(
                "删除旧记录失败（继续写入新记录）: %s | error=%s",
                path.name, e,
            )

        # 6. 写入
        added = self.vector_store.add_documents(records)
        return added

    def get_stats(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        return self.vector_store.health_check()
