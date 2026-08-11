"""
DocGuard Agent - Mock Embedding 客户端
=======================================

用途：
1. 无 API Key 环境下的端到端流程验证（开发/测试/演示）
2. 单元测试 fixture（与 tests/test_knowledge/conftest.py 中的 MockEmbeddingClient 同源）
3. 离线场景下的轻量级向量化（仅用于功能验证，不用于生产）

实现原理：
1. 基于词袋模型（Bag-of-Words）：对文本分词，每个词哈希到一个维度
2. 同词共现会使向量在该维度叠加，从而 L2 距离 ↔ 词义相似度
3. 归一化为单位向量，使余弦相似度与 L2 距离等价
4. 维度默认 384（与 sentence-transformers/paraphrase-MiniLM-L3-v2 一致，便于切换）

局限性：
- 不捕获词序与语义（"猫追狗" 与 "狗追猫" 向量相同）
- 仅用于功能验证，检索质量远不如真实 Embedding 模型
- 生产环境请使用 EmbeddingClient（OpenAI 兼容协议）

用法：
    from core.mock_embedding import MockEmbeddingClient
    client = MockEmbeddingClient(dim=384)
    embeddings = await client.embed_texts(["hello world", "你好世界"])
"""

from __future__ import annotations

import hashlib
from typing import Optional

from core.logging_config import get_logger

logger = get_logger("core.mock_embedding")


class MockEmbeddingClient:
    """
    基于词袋模型的 Mock Embedding 客户端。

    仅用于开发/测试/演示，不用于生产环境。
    与 EmbeddingClient 接口完全一致（embed_texts / embed_text）。
    """

    # 中英文常见分隔符（用于简易分词）
    _SEPARATORS = "。！？.;；,，、 \t\n\r()[]{}\"'!:?"

    def __init__(self, dim: int = 384) -> None:
        """
        Args:
            dim: 向量维度（默认 384）
        """
        self.dim = dim
        self.call_count: int = 0
        logger.warning(
            "使用 MockEmbeddingClient（词袋模型），仅适用于开发/测试，"
            "生产环境请配置 LLM api_key 使用真实 Embedding 模型"
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        批量向量化文本（词袋模型 + 哈希 + L2 归一化）。

        Args:
            texts: 待向量化的文本列表

        Returns:
            与输入等长的向量列表，每个向量是 float 列表
        """
        self.call_count += 1
        results: list[list[float]] = []
        for text in texts:
            results.append(self._embed_one(text))
        return results

    async def embed_text(self, text: str) -> list[float]:
        """单条文本向量化"""
        result = await self.embed_texts([text])
        return result[0]

    # ============================================================
    # 内部实现
    # ============================================================
    def _embed_one(self, text: str) -> list[float]:
        """对单条文本生成词袋向量"""
        vec = [0.0] * self.dim
        words = self._tokenize(text)
        for word in words:
            if not word:
                continue
            # MD5 哈希到 [0, dim) 区间
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            vec[idx] += 1.0

        # L2 归一化（使余弦相似度与 L2 距离等价）
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def _tokenize(self, text: str) -> list[str]:
        """
        简易分词：中英文混合按分隔符切分，并过滤常见停用词。

        与 tests/test_knowledge/conftest.py 中的 MockEmbeddingClient 保持一致。
        """
        # 替换分隔符为空格
        normalized = text.lower()
        for sep in self._SEPARATORS:
            normalized = normalized.replace(sep, " ")
        # 过滤常见中文停用词
        for stopword in ("的", "了", "是", "在", "和", "与", "或", "及"):
            normalized = normalized.replace(stopword, " ")
        return normalized.split()


# ============================================================
# 工厂函数
# ============================================================
def create_mock_embedding_client(dim: Optional[int] = None) -> MockEmbeddingClient:
    """
    创建 Mock Embedding 客户端实例。

    Args:
        dim: 向量维度，None 时使用默认 384

    Returns:
        MockEmbeddingClient 实例
    """
    return MockEmbeddingClient(dim=dim or 384)
