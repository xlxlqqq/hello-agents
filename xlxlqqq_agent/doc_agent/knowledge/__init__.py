"""
DocGuard Agent - 知识库与 RAG 层
==================================

提供基于 ChromaDB 的企业历史文档知识库：
- vector_store: ChromaDB 向量存储封装
- ingestor: 知识库文档摄取器（解析 history docs → 切分 → 向量化 → 入库）
- retriever: 检索器（语义检索 + 元数据过滤）
- style_profile: 文档风格画像生成器（从历史文档学习结构/格式/术语规范）

数据流：
    knowledge_docs/*.docx
        ↓ Ingestor
    DocumentParser → 切分 → Embedding → ChromaDB
                                        ↓ Retriever
                                    检索结果 + StyleProfile
                                        ↓
                                RetrievalAgent → state
"""
