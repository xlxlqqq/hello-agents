"""
DocGuard Agent - ChromaDB 向量存储封装
========================================

设计要点：
1. 封装 ChromaDB 持久化客户端，提供增删查接口
2. 支持 metadata 过滤（按文件名、段落类型、章节等）
3. 不直接依赖 Embedding 客户端，由调用方注入向量（解耦）
4. 异常统一封装为 VectorStoreError，携带上下文
5. 提供 collection 健康检查与统计

ChromaDB 数据模型：
- collection: 类似数据库表
- document: 文本内容
- embedding: 向量
- id: 唯一标识
- metadata: 元数据 dict（用于过滤）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from core.config import ChromaConfig
from core.exceptions import VectorStoreError, wrap_exception
from core.logging_config import get_logger

logger = get_logger("knowledge.vector_store")


# ============================================================
# 数据模型
# ============================================================
@dataclass
class VectorRecord:
    """向量记录"""

    id: str                             # 唯一 ID
    document: str                       # 文本内容
    embedding: list[float]              # 向量
    metadata: dict[str, Any]            # 元数据

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document": self.document,
            "metadata": self.metadata,
        }


@dataclass
class QueryResult:
    """查询结果"""

    ids: list[list[str]]                # ChromaDB 返回嵌套列表
    documents: list[list[str]]
    metadatas: list[list[dict]]
    distances: list[list[float]]

    def flatten(self) -> list[dict[str, Any]]:
        """扁平化为记录列表（取第一组查询结果）"""
        if not self.ids or not self.ids[0]:
            return []
        records = []
        for i in range(len(self.ids[0])):
            # ChromaDB distance 越小越相似，转为 0-1 的相似度分数
            distance = self.distances[0][i] if self.distances and self.distances[0] else 0.0
            similarity = max(0.0, 1.0 - distance / 2.0)  # 余弦距离 0-2 转相似度
            records.append({
                "id": self.ids[0][i],
                "document": self.documents[0][i] if self.documents and self.documents[0] else "",
                "metadata": self.metadatas[0][i] if self.metadatas and self.metadatas[0] else {},
                "distance": distance,
                "similarity": similarity,
            })
        return records


# ============================================================
# 向量存储
# ============================================================
class VectorStore:
    """
    ChromaDB 向量存储封装。

    用法：
        store = VectorStore(config)
        store.add_documents(records)
        results = store.query(query_embeddings=[...], n_results=5)
    """

    def __init__(
        self,
        config: ChromaConfig,
        *,
        collection_name: Optional[str] = None,
    ) -> None:
        """
        Args:
            config: ChromaDB 配置
            collection_name: 自定义 collection 名（默认用 config.collection_name）
        """
        self.config = config
        self.collection_name = collection_name or config.collection_name
        self._client: Optional[chromadb.api.ClientAPI] = None
        self._collection: Optional[chromadb.api.Collection] = None
        self.logger = get_logger("knowledge.vector_store")

    # ============================================================
    # 生命周期
    # ============================================================
    def initialize(self) -> None:
        """
        初始化 ChromaDB 客户端与 collection。

        首次调用时创建持久化客户端和 collection（若不存在）。
        幂等：重复调用安全。

        Raises:
            VectorStoreError: 初始化失败
        """
        if self._client is not None:
            return

        try:
            persist_dir = Path(self.config.persist_directory)
            persist_dir.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(persist_dir),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self.logger.info(
                "ChromaDB 客户端已初始化 | persist_dir=%s", persist_dir
            )

            # 获取或创建 collection
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={
                    "description": "DocGuard knowledge base",
                    "embedding_dim": str(self.config.embedding_dim),
                },
            )
            self._ensure_collection_dimension()
            self.logger.info(
                "Collection '%s' 已就绪 | 现有记录数=%d | embedding_dim=%s",
                self.collection_name,
                self._collection.count(),
                self.config.embedding_dim,
            )
        except Exception as e:
            raise wrap_exception(
                e,
                VectorStoreError,
                f"ChromaDB 初始化失败: {e}",
                persist_directory=self.config.persist_directory,
                collection_name=self.collection_name,
            ) from e

    def _ensure_collection_dimension(self) -> None:
        """确保 collection 维度与当前 embedding 配置一致。"""
        if self._collection is None:
            return

        expected_dim = int(getattr(self.config, "embedding_dim", 0) or 0)
        if expected_dim <= 0:
            return

        existing_dim = self._detect_collection_dimension()
        if existing_dim is not None and existing_dim != expected_dim:
            self.logger.warning(
                "Collection '%s' embedding 维度不一致: expected=%d, existing=%d，正在重建 collection",
                self.collection_name,
                expected_dim,
                existing_dim,
            )
            assert self._client is not None
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={
                    "description": "DocGuard knowledge base",
                    "embedding_dim": str(expected_dim),
                },
            )
            return

        meta = self._collection.metadata or {}
        if str(meta.get("embedding_dim", "")).strip() and str(meta.get("embedding_dim")) != str(expected_dim):
            try:
                self._collection.modify(
                    metadata={
                        **meta,
                        "embedding_dim": str(expected_dim),
                    }
                )
            except Exception:
                # Collection may be empty or metadata not writable in some Chroma versions.
                pass

    def _detect_collection_dimension(self) -> Optional[int]:
        """返回现有 collection 里的 embedding 维度。"""
        if self._collection is None:
            return None

        try:
            meta = self._collection.metadata or {}
            if meta.get("embedding_dim") is not None:
                return int(str(meta["embedding_dim"]))
        except Exception:
            pass

        try:
            peek = self._collection.peek()
            embeddings = peek.get("embeddings") or []
            if embeddings and len(embeddings) > 0 and embeddings[0] is not None:
                return len(embeddings[0])
        except Exception:
            pass
        return None

    def reset(self) -> None:
        """
        重置 collection（清空所有数据）。

        危险操作：仅用于测试或重新构建知识库。

        Raises:
            VectorStoreError: 重置失败
        """
        self.initialize()
        try:
            assert self._client is not None
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "DocGuard knowledge base"},
            )
            self.logger.warning("Collection '%s' 已重置（数据全部清空）", self.collection_name)
        except Exception as e:
            raise wrap_exception(
                e,
                VectorStoreError,
                f"Collection 重置失败: {e}",
                collection_name=self.collection_name,
            ) from e

    # ============================================================
    # 增删
    # ============================================================
    def add_documents(
        self,
        records: list[VectorRecord],
        *,
        batch_size: int = 100,
    ) -> int:
        """
        批量添加文档向量。

        自动分批，避免单次请求过大。
        重复 ID 会被覆盖（upsert 语义）。

        Args:
            records: 向量记录列表
            batch_size: 单批大小

        Returns:
            实际添加的记录数

        Raises:
            VectorStoreError: 添加失败
        """
        if not records:
            return 0

        self.initialize()
        assert self._collection is not None

        total = 0
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            try:
                self._collection.upsert(
                    ids=[r.id for r in batch],
                    documents=[r.document for r in batch],
                    embeddings=[r.embedding for r in batch],
                    metadatas=[r.metadata for r in batch],
                )
                total += len(batch)
            except Exception as e:
                raise wrap_exception(
                    e,
                    VectorStoreError,
                    f"批量添加失败（batch_start={start}）: {e}",
                    batch_size=len(batch),
                ) from e

        self.logger.info("已添加 %d 条记录到 collection '%s'", total, self.collection_name)
        return total

    def delete_by_metadata(self, **metadata_filter: Any) -> int:
        """
        按 metadata 删除记录（如删除某个文件的所有 chunk）。

        Args:
            **metadata_filter: 元数据过滤条件，如 source_file="report.docx"

        Returns:
            删除的记录数
        """
        self.initialize()
        assert self._collection is not None

        try:
            # 先查询匹配的 ID
            where = {k: v for k, v in metadata_filter.items()}
            result = self._collection.get(where=where)
            ids_to_delete = result.get("ids", [])
            if not ids_to_delete:
                return 0
            self._collection.delete(ids=ids_to_delete)
            self.logger.info(
                "按 metadata 删除 %d 条记录 | filter=%s",
                len(ids_to_delete), metadata_filter,
            )
            return len(ids_to_delete)
        except Exception as e:
            raise wrap_exception(
                e,
                VectorStoreError,
                f"按 metadata 删除失败: {e}",
                filter=str(metadata_filter),
            ) from e

    # ============================================================
    # 查询
    # ============================================================
    def query(
        self,
        query_embeddings: list[list[float]],
        *,
        n_results: Optional[int] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> QueryResult:
        """
        向量相似度查询。

        Args:
            query_embeddings: 查询向量列表
            n_results: 返回结果数（None 用 config.top_k）
            where: metadata 过滤条件

        Returns:
            QueryResult 实例

        Raises:
            VectorStoreError: 查询失败
        """
        self.initialize()
        assert self._collection is not None

        n = n_results or self.config.top_k
        try:
            result = self._collection.query(
                query_embeddings=query_embeddings,
                n_results=n,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            return QueryResult(
                ids=result.get("ids", [[]]),
                documents=result.get("documents", [[]]),
                metadatas=result.get("metadatas", [[]]),
                distances=result.get("distances", [[]]),
            )
        except Exception as e:
            raise wrap_exception(
                e,
                VectorStoreError,
                f"查询失败: {e}",
                n_results=n,
            ) from e

    def query_by_text(
        self,
        query_text: str,
        embedding_func,
        *,
        n_results: Optional[int] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> QueryResult:
        """
        文本查询（自动向量化）。

        Args:
            query_text: 查询文本
            embedding_func: 提供 embed_texts 方法的对象
            n_results: 返回结果数
            where: metadata 过滤

        Returns:
            QueryResult
        """
        import asyncio
        # 同步上下文调用异步 embedding
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在异步上下文中，需用 ensure_future
                raise VectorStoreError(
                    "query_by_text 不能在异步事件循环中同步调用，请改用 query() + 预先计算的 embedding",
                )
        except RuntimeError:
            pass

        embeddings = embedding_func.embed_texts([query_text])
        return self.query(embeddings, n_results=n_results, where=where)

    def get_all(self, limit: int = 1000) -> list[dict[str, Any]]:
        """
        获取所有记录（用于调试或风格画像生成）。

        Args:
            limit: 最大返回数

        Returns:
            记录列表
        """
        self.initialize()
        assert self._collection is not None
        try:
            result = self._collection.get(limit=limit)
            records = []
            ids = result.get("ids", [])
            docs = result.get("documents", [])
            metas = result.get("metadatas", [])
            for i in range(len(ids)):
                records.append({
                    "id": ids[i],
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                })
            return records
        except Exception as e:
            raise wrap_exception(
                e,
                VectorStoreError,
                f"get_all 失败: {e}",
            ) from e

    # ============================================================
    # 统计与健康检查
    # ============================================================
    def count(self) -> int:
        """返回 collection 中的记录数"""
        self.initialize()
        assert self._collection is not None
        try:
            return self._collection.count()
        except Exception as e:
            self.logger.warning("count 失败: %s", e)
            return 0

    def health_check(self) -> dict[str, Any]:
        """
        健康检查。

        Returns:
            {"healthy": bool, "count": int, "collection": str, "persist_dir": str}
        """
        try:
            count = self.count()
            return {
                "healthy": True,
                "count": count,
                "collection": self.collection_name,
                "persist_dir": self.config.persist_directory,
            }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "collection": self.collection_name,
            }


# ============================================================
# 便捷工厂
# ============================================================
def create_vector_store(
    config: Optional[ChromaConfig] = None,
    *,
    collection_name: Optional[str] = None,
) -> VectorStore:
    """
    创建 VectorStore 实例。

    Args:
        config: ChromaDB 配置（None 时从全局配置获取）
        collection_name: 可选的 collection 名

    Returns:
        未初始化的 VectorStore（首次操作时自动 initialize）
    """
    if config is None:
        from core.config import get_config
        config = get_config().chroma
    return VectorStore(config, collection_name=collection_name)


def generate_record_id(prefix: str = "doc") -> str:
    """生成唯一的向量记录 ID"""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
