"""
DocGuard Agent - Embedding 客户端（OpenAI 兼容封装）
======================================================

设计要点：
1. 复用 LLMConfig 中的 base_url 和 api_key
2. 支持批量向量化，避免单次请求过大
3. 内置重试，应对 429 限流
4. 不绑定具体 embedding 模型，由配置注入
"""

import asyncio
import logging
from typing import Optional

from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError

from core.config import LLMConfig
from core.exceptions import EmbeddingError, LLMRateLimitError
from core.logging_config import get_logger

logger = get_logger("core.embedding_client")


class EmbeddingClient:
    """
    OpenAI 兼容协议 Embedding 客户端。

    通过依赖注入 LLMConfig 实例化。
    所有方法均为 async。
    """

    def __init__(
        self,
        config: LLMConfig,
        client: Optional[AsyncOpenAI] = None,
    ) -> None:
        """
        Args:
            config: LLM 配置（复用 api_key/base_url/timeout）
            client: 可选的 AsyncOpenAI 实例（测试 mock 用）
        """
        if not config.api_key:
            raise EmbeddingError("LLM api_key 未配置，无法初始化 EmbeddingClient")

        self.config = config
        self._client = client or AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        批量向量化文本。

        自动按 embedding_batch_size 分片，避免单次请求超出 API 限制。
        保留输入顺序。

        Args:
            texts: 待向量化的文本列表

        Returns:
            与输入等长的向量列表，每个向量是 float 列表

        Raises:
            EmbeddingError: 向量化失败
        """
        if not texts:
            return []

        batch_size = self.config.embedding_batch_size
        all_embeddings: list[list[float]] = []

        # 分片处理
        for start_idx in range(0, len(texts), batch_size):
            batch = texts[start_idx:start_idx + batch_size]
            batch_embeddings = await self._embed_batch_with_retry(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def embed_text(self, text: str) -> list[float]:
        """
        单条文本向量化。

        Args:
            text: 待向量化的文本

        Returns:
            向量（float 列表）
        """
        result = await self.embed_texts([text])
        return result[0]

    # ============================================================
    # 内部实现
    # ============================================================
    async def _embed_batch_with_retry(self, texts: list[str]) -> list[list[float]]:
        """带重试的批量 embedding 调用"""
        max_retries = 3
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                response = await self._client.embeddings.create(
                    model=self.config.embedding_model,
                    input=texts,
                )
                # 按 index 排序确保顺序正确
                sorted_data = sorted(response.data, key=lambda x: x.index)
                return [item.embedding for item in sorted_data]

            except RateLimitError as e:
                last_exception = e
                wait = min(2 ** attempt, 16)
                logger.warning(
                    "Embedding 触发限流 (429)，%ds 后重试 (attempt=%d/%d)",
                    wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue

            except APITimeoutError as e:
                raise EmbeddingError(
                    f"Embedding 调用超时 (timeout={self.config.timeout}s)",
                    cause=e,
                ) from e

            except APIConnectionError as e:
                raise EmbeddingError(
                    f"Embedding 连接失败: {e}",
                    context={"base_url": self.config.base_url},
                    cause=e,
                ) from e

            except Exception as e:
                last_exception = e
                raise EmbeddingError(
                    f"Embedding 调用失败: {e}",
                    cause=e,
                ) from e

        raise LLMRateLimitError(
            f"Embedding 调用在 {max_retries} 次重试后仍触发限流",
            cause=last_exception,
        )


# ============================================================
# 工厂函数
# ============================================================
def create_embedding_client(config: Optional[LLMConfig] = None) -> EmbeddingClient:
    """
    创建 Embedding 客户端实例。

    Args:
        config: LLM 配置，None 时从全局配置获取

    Returns:
        EmbeddingClient 实例
    """
    if config is None:
        from core.config import get_config
        config = get_config().llm
    return EmbeddingClient(config=config)
