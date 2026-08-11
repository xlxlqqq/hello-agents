"""
DocGuard Agent - 知识库检索器
==============================

职责：
1. 接收当前文档（或其段落），向量化并查询 ChromaDB
2. 返回相似历史文档片段（RetrievedDoc 列表）
3. 支持按 metadata 过滤（如按章节、按文件）
4. 支持批量检索（文档多个段落并行查询，结果合并去重）

设计要点：
1. 与 EmbeddingClient + VectorStore 协作，自身只负责检索编排
2. 相似度阈值过滤：低于 threshold 的结果被丢弃
3. 结果去重：同一 chunk 不重复返回
4. 上下文扩展：可选返回 chunk 所在章节的其他片段

数据流：
    StructuredDocument
        ↓ 提取代表性段落（标题 + 前若干正文）
    EmbeddingClient.embed_texts
        ↓
    VectorStore.query
        ↓
    过滤 + 去重 + 排序
    list[RetrievedDoc]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.config import ChromaConfig
from core.exceptions import RetrievalError, wrap_exception
from core.logging_config import get_logger
from core.state import RetrievedDoc
from document.models import StructuredDocument
from knowledge.vector_store import QueryResult, VectorStore

logger = get_logger("knowledge.retriever")


# ============================================================
# 检索器
# ============================================================
class KnowledgeRetriever:
    """
    知识库检索器。

    用法：
        retriever = KnowledgeRetriever(embedding_client, vector_store)
        results = await retriever.retrieve_for_document(doc, top_k=5)
    """

    def __init__(
        self,
        embedding_client,
        vector_store: VectorStore,
        *,
        config: Optional[ChromaConfig] = None,
    ) -> None:
        """
        Args:
            embedding_client: EmbeddingClient 实例
            vector_store: VectorStore 实例
            config: ChromaDB 配置（用于 top_k / threshold）
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.config = config or ChromaConfig()
        self.logger = get_logger("knowledge.retriever")

    # ============================================================
    # 文档级检索
    # ============================================================
    async def retrieve_for_document(
        self,
        document: StructuredDocument,
        *,
        top_k: Optional[int] = None,
        max_query_paragraphs: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[RetrievedDoc]:
        """
        为整篇文档检索相似历史片段。

        策略：
        1. 提取文档代表性段落（所有标题 + 前若干正文段落）
        2. 每个段落作为 query 检索 top_k
        3. 合并所有结果，按相似度排序，去重
        4. 截断到 top_k

        Args:
            document: 当前待审查文档
            top_k: 返回结果数（None 用 config.top_k）
            max_query_paragraphs: 最多使用多少段落作为 query
            where: metadata 过滤条件

        Returns:
            RetrievedDoc 列表（按相似度降序）
        """
        n = top_k or self.config.top_k

        # 1. 提取代表性段落
        query_texts = self._extract_representative_paragraphs(document, max_query_paragraphs)
        if not query_texts:
            self.logger.warning("文档无可用于检索的段落")
            return []

        self.logger.info(
            "检索开始 | 文档=%s | 查询段落数=%d | top_k=%d",
            document.filename, len(query_texts), n,
        )

        # 2. 向量化
        try:
            query_embeddings = await self.embedding_client.embed_texts(query_texts)
        except Exception as e:
            raise wrap_exception(
                e,
                RetrievalError,
                f"查询向量化失败: {e}",
                query_count=len(query_texts),
            ) from e

        # 3. 批量查询
        try:
            result = self.vector_store.query(
                query_embeddings=query_embeddings,
                n_results=n,
                where=where,
            )
        except Exception as e:
            raise wrap_exception(
                e,
                RetrievalError,
                f"向量查询失败: {e}",
            ) from e

        # 4. 合并、去重、过滤、排序
        records = self._merge_query_results(result, query_texts)
        records = self._filter_by_similarity(records, self.config.similarity_threshold)
        records = self._deduplicate(records)
        records.sort(key=lambda r: r["similarity"], reverse=True)
        records = records[:n]

        # 5. 转为 RetrievedDoc
        retrieved: list[RetrievedDoc] = []
        for r in records:
            retrieved.append(RetrievedDoc(
                doc_id=r["id"],
                filename=r["metadata"].get("source_file", "unknown"),
                content_snippet=r["document"][:500],  # 截断避免过长
                similarity_score=round(r["similarity"], 4),
                metadata=r["metadata"],
            ))

        self.logger.info(
            "检索完成 | 原始结果=%d | 过滤后=%d",
            result.flatten().__len__() if result else 0,
            len(retrieved),
        )
        return retrieved

    # ============================================================
    # 单段落检索
    # ============================================================
    async def retrieve_for_text(
        self,
        text: str,
        *,
        top_k: Optional[int] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[RetrievedDoc]:
        """
        为单段文本检索相似历史片段。

        Args:
            text: 查询文本
            top_k: 返回结果数
            where: metadata 过滤

        Returns:
            RetrievedDoc 列表
        """
        if not text.strip():
            return []

        n = top_k or self.config.top_k
        try:
            embeddings = await self.embedding_client.embed_texts([text])
        except Exception as e:
            raise wrap_exception(
                e,
                RetrievalError,
                f"文本向量化失败: {e}",
            ) from e

        try:
            result = self.vector_store.query(
                query_embeddings=embeddings,
                n_results=n,
                where=where,
            )
        except Exception as e:
            raise wrap_exception(
                e,
                RetrievalError,
                f"查询失败: {e}",
            ) from e

        records = result.flatten()
        records = self._filter_by_similarity(records, self.config.similarity_threshold)
        records.sort(key=lambda r: r["similarity"], reverse=True)
        records = records[:n]

        return [
            RetrievedDoc(
                doc_id=r["id"],
                filename=r["metadata"].get("source_file", "unknown"),
                content_snippet=r["document"][:500],
                similarity_score=round(r["similarity"], 4),
                metadata=r["metadata"],
            )
            for r in records
        ]

    # ============================================================
    # 内部方法
    # ============================================================
    def _extract_representative_paragraphs(
        self,
        document: StructuredDocument,
        max_count: int,
    ) -> list[str]:
        """
        提取代表性段落作为检索 query。

        策略：优先标题，其次前若干正文段落。
        """
        queries: list[str] = []
        # 先收集标题
        for para in document.paragraphs:
            if para.heading_level is not None and para.text.strip():
                queries.append(para.text.strip())
                if len(queries) >= max_count:
                    return queries
        # 再补充正文段落
        for para in document.paragraphs:
            if para.heading_level is None and len(para.text.strip()) > 20:
                queries.append(para.text.strip())
                if len(queries) >= max_count:
                    break
        return queries

    def _merge_query_results(
        self,
        result: QueryResult,
        query_texts: list[str],
    ) -> list[dict[str, Any]]:
        """合并多 query 的结果（保留原始嵌套结构）"""
        all_records: list[dict[str, Any]] = []
        # QueryResult.flatten() 只返回第一组查询结果
        # 多 query 时需要遍历所有组
        if not result.ids:
            return []
        for query_idx, ids in enumerate(result.ids):
            for i, chunk_id in enumerate(ids):
                doc = result.documents[query_idx][i] if i < len(result.documents[query_idx]) else ""
                meta = result.metadatas[query_idx][i] if i < len(result.metadatas[query_idx]) else {}
                dist = result.distances[query_idx][i] if i < len(result.distances[query_idx]) else 0.0
                similarity = max(0.0, 1.0 - dist / 2.0)
                all_records.append({
                    "id": chunk_id,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                    "similarity": similarity,
                    "query_index": query_idx,
                })
        return all_records

    def _filter_by_similarity(
        self,
        records: list[dict[str, Any]],
        threshold: float,
    ) -> list[dict[str, Any]]:
        """按相似度阈值过滤"""
        return [r for r in records if r["similarity"] >= threshold]

    def _deduplicate(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 chunk_id 去重，保留相似度最高的"""
        seen: dict[str, dict[str, Any]] = {}
        for r in records:
            cid = r["id"]
            if cid not in seen or r["similarity"] > seen[cid]["similarity"]:
                seen[cid] = r
        return list(seen.values())


# ============================================================
# 便捷工厂
# ============================================================
def create_retriever(
    embedding_client,
    vector_store: VectorStore,
    config: Optional[ChromaConfig] = None,
) -> KnowledgeRetriever:
    """创建检索器实例"""
    return KnowledgeRetriever(embedding_client, vector_store, config=config)
