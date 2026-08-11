"""
Phase 3 知识库测试 - 共享 fixture
=================================

提供：
1. MockEmbeddingClient：基于词袋的确定性 mock，无需 API Key
   - 共享词汇的文本会产生相似向量（余弦相似度高）
2. 临时 ChromaConfig：持久化目录指向 pytest tmp_path，互不污染
3. 样本文档：复用 test_document.fixtures 生成 DOCX
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import pytest

from core.config import ChromaConfig, DocGuardConfig
from knowledge.vector_store import VectorStore, create_vector_store
from tests.test_document.fixtures import (
    create_complex_docx,
    create_sample_docx,
)


# ============================================================
# Mock Embedding 客户端
# ============================================================
class MockEmbeddingClient:
    """
    测试用 Mock Embedding 客户端。

    基于词袋模型生成确定性向量：
    - 每个词哈希到一个维度并累加
    - 向量归一化为单位长度
    - 共享词汇的文本余弦相似度更高

    这样 ChromaDB 的 L2 距离可正确反映文本相似度，
    无需调用真实 Embedding API。
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.call_count = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        results: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            # 简单分词：中英文混合按空格 + 常见标点
            words = (
                text.lower()
                .replace("。", " ")
                .replace("，", " ")
                .replace("、", " ")
                .replace("的", " ")
                .split()
            )
            for word in words:
                if not word:
                    continue
                h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                vec[idx] += 1.0
            # 归一化为单位向量（使 L2 距离 ↔ 余弦相似度）
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            results.append(vec)
        return results

    async def embed_text(self, text: str) -> list[float]:
        result = await self.embed_texts([text])
        return result[0]


# ============================================================
# 配置 fixture
# ============================================================
@pytest.fixture
def chroma_config(tmp_path: Path) -> ChromaConfig:
    """临时 ChromaDB 配置（每个测试独立持久化目录）"""
    return ChromaConfig(
        persist_directory=str(tmp_path / "chroma_db"),
        collection_name="test_knowledge",
        top_k=5,
        similarity_threshold=0.0,  # 测试中不按阈值过滤，单独验证过滤逻辑
    )


@pytest.fixture
def doc_config(tmp_path: Path) -> DocGuardConfig:
    """临时全局配置，知识库目录和 ChromaDB 都指向 tmp_path"""
    config = DocGuardConfig()
    config.chroma.persist_directory = str(tmp_path / "chroma_db")
    config.chroma.collection_name = "test_knowledge"
    config.chroma.similarity_threshold = 0.0
    # 知识库目录指向 tmp_path 下的子目录
    config.paths.knowledge_dir = str(tmp_path / "knowledge_docs")
    Path(tmp_path / "knowledge_docs").mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def vector_store(chroma_config: ChromaConfig) -> VectorStore:
    """已初始化的 VectorStore"""
    store = create_vector_store(chroma_config)
    store.initialize()
    return store


@pytest.fixture
def mock_embedding() -> MockEmbeddingClient:
    """Mock Embedding 客户端"""
    return MockEmbeddingClient()


# ============================================================
# 样本文档 fixture
# ============================================================
@pytest.fixture
def sample_docx_path(tmp_path: Path) -> Path:
    """样本文档 DOCX 路径"""
    return create_sample_docx(tmp_path / "sample.docx")


@pytest.fixture
def complex_docx_path(tmp_path: Path) -> Path:
    """复杂文档 DOCX 路径"""
    return create_complex_docx(tmp_path / "complex.docx")


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """知识库源目录（含 2 个 DOCX）"""
    kd = tmp_path / "knowledge_docs"
    kd.mkdir(parents=True, exist_ok=True)
    create_sample_docx(kd / "doc1.docx")
    create_complex_docx(kd / "doc2.docx")
    return kd
