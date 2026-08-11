"""
DocGuard Agent - Retrieval Agent
=================================

职责：
1. 接收 Parser 解析的 StructuredDocument
2. 调用 KnowledgeRetriever 检索相似历史文档
3. 调用 StyleProfileGenerator 生成文档风格画像
4. 提取企业术语列表
5. 写入 state.retrieved_documents / style_profile / terminology_list

输入 state 字段：
- parsed_document: 必填（来自 Parser Agent）

输出 state 字段：
- retrieved_documents: list[RetrievedDoc]
- style_profile: Optional[StyleProfile]
- terminology_list: list[str]

设计要点：
1. 知识库为空时优雅降级：返回空检索结果，使用默认 StyleProfile
2. StyleProfile 生成失败不中断流程：回退到统计模式
3. 支持"跳过 LLM"模式：当 LLM 未配置时仅用统计模式
4. 检索结果数量为 0 不算失败
"""

from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from core.config import DocGuardConfig, get_config
from core.embedding_client import EmbeddingClient
from core.exceptions import KnowledgeBaseError
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.state import DocGuardState
from knowledge.retriever import KnowledgeRetriever
from knowledge.style_profile import StyleProfileGenerator
from knowledge.vector_store import VectorStore


class RetrievalAgent(BaseAgent):
    """
    Retrieval Agent：知识库检索 + 风格画像生成。

    依赖注入 EmbeddingClient / VectorStore / KnowledgeRetriever / StyleProfileGenerator，
    便于测试 mock。
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient],
        config: DocGuardConfig,
        logger=None,
        *,
        embedding_client: Optional[EmbeddingClient] = None,
        vector_store: Optional[VectorStore] = None,
        retriever: Optional[KnowledgeRetriever] = None,
        style_generator: Optional[StyleProfileGenerator] = None,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端（None 时不启用 LLM 增强）
            config: 全局配置
            logger: 可选 logger
            embedding_client: 可选 EmbeddingClient（None 时自动创建）
            vector_store: 可选 VectorStore
            retriever: 可选 KnowledgeRetriever（测试 mock 用）
            style_generator: 可选 StyleProfileGenerator
        """
        super().__init__(llm_client, config, logger or get_logger("agents.retrieval_agent"))
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._retriever = retriever
        self._style_generator = style_generator

    def agent_name(self) -> str:
        return "retrieval_agent"

    async def execute(self, state: DocGuardState) -> DocGuardState:
        """
        执行检索 + 风格画像生成。

        流程：
        1. 校验 parsed_document
        2. 检索相似历史文档
        3. 生成风格画像
        4. 提取术语列表
        """
        # 1. 校验上游
        parsed_doc = self._validate_state_field(state, "parsed_document")
        if not state.get("parse_success"):
            self.logger.warning("[Retrieval] 解析失败，跳过检索")
            state["retrieved_documents"] = []
            state["style_profile"] = None
            state["terminology_list"] = []
            return state

        self.logger.info("[Retrieval] 开始检索 | 文档=%s", parsed_doc.filename)

        # 2. 检索
        try:
            retriever = self._get_retriever()
            if retriever is not None:
                retrieved_docs = await retriever.retrieve_for_document(
                    parsed_doc,
                    top_k=self.config.chroma.top_k,
                )
                state["retrieved_documents"] = retrieved_docs
                self.logger.info(
                    "[Retrieval] 检索完成 | 命中=%d", len(retrieved_docs)
                )
            else:
                self.logger.warning("[Retrieval] 检索器未初始化，跳过检索")
                state["retrieved_documents"] = []
        except KnowledgeBaseError as e:
            self.logger.warning("[Retrieval] 检索失败（降级处理）: %s", e)
            state["retrieved_documents"] = []
        except Exception as e:
            self.logger.warning("[Retrieval] 检索未知异常（降级处理）: %s", e, exc_info=True)
            state["retrieved_documents"] = []

        # 3. 生成风格画像（从知识库统计，可选 LLM 增强）
        try:
            style_generator = self._get_style_generator()
            if style_generator is not None:
                # 从知识库读取样本文档（用于生成画像）
                sample_docs = self._load_sample_documents()
                if sample_docs:
                    profile = await style_generator.generate_from_documents(sample_docs)
                    state["style_profile"] = profile.to_state_dict()
                    state["terminology_list"] = profile.terminology
                    self.logger.info(
                        "[Retrieval] 风格画像生成完成 | sections=%d | terms=%d | method=%s",
                        len(profile.expected_sections),
                        len(profile.terminology),
                        profile.generation_method,
                    )
                else:
                    self.logger.warning("[Retrieval] 无样本文档，跳过风格画像生成")
                    state["style_profile"] = None
                    state["terminology_list"] = []
            else:
                self.logger.warning("[Retrieval] 风格生成器未初始化")
                state["style_profile"] = None
                state["terminology_list"] = []
        except Exception as e:
            self.logger.warning(
                "[Retrieval] 风格画像生成失败（降级处理）: %s", e, exc_info=True
            )
            state["style_profile"] = None
            state["terminology_list"] = []

        return state

    def _build_summary(self, state: DocGuardState) -> str:
        retrieved_count = len(state.get("retrieved_documents", []))
        terms_count = len(state.get("terminology_list", []))
        has_profile = state.get("style_profile") is not None
        return (
            f"retrieved={retrieved_count}, "
            f"has_profile={has_profile}, "
            f"terms={terms_count}"
        )

    # ============================================================
    # 懒加载依赖
    # ============================================================
    def _get_embedding_client(self) -> Optional[EmbeddingClient]:
        if self._embedding_client is None:
            try:
                from core.embedding_client import create_embedding_client
                self._embedding_client = create_embedding_client(self.config.llm)
            except Exception as e:
                self.logger.warning("EmbeddingClient 创建失败: %s", e)
                return None
        return self._embedding_client

    def _get_vector_store(self) -> Optional[VectorStore]:
        if self._vector_store is None:
            try:
                from knowledge.vector_store import create_vector_store
                self._vector_store = create_vector_store(self.config.chroma)
            except Exception as e:
                self.logger.warning("VectorStore 创建失败: %s", e)
                return None
        return self._vector_store

    def _get_retriever(self) -> Optional[KnowledgeRetriever]:
        if self._retriever is not None:
            return self._retriever
        embedding = self._get_embedding_client()
        vector_store = self._get_vector_store()
        if embedding is None or vector_store is None:
            return None
        self._retriever = KnowledgeRetriever(
            embedding_client=embedding,
            vector_store=vector_store,
            config=self.config.chroma,
        )
        return self._retriever

    def _get_style_generator(self) -> Optional[StyleProfileGenerator]:
        if self._style_generator is not None:
            return self._style_generator
        self._style_generator = StyleProfileGenerator(
            llm_client=self.llm,
            use_llm=self.llm is not None,
        )
        return self._style_generator

    def _load_sample_documents(self) -> list:
        """从知识库目录加载样本文档（用于生成风格画像）"""
        from pathlib import Path
        from document.parser import DocxParser

        knowledge_dir = self.config.get_path("knowledge_dir")
        if not knowledge_dir.exists():
            self.logger.info("知识库目录不存在: %s", knowledge_dir)
            return []

        parser = DocxParser()
        docs = []
        files = sorted(knowledge_dir.glob("*.docx"))
        for f in files[:5]:  # 最多 5 个样本
            try:
                docs.append(parser.parse(str(f)))
            except Exception as e:
                self.logger.warning("样本文档解析失败 %s: %s", f.name, e)
        return docs
