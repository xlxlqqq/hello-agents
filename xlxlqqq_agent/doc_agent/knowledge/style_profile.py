"""
DocGuard Agent - 文档风格画像生成器
====================================

职责：
从知识库历史文档中学习企业文档规范，生成 StyleProfile：
1. 结构规范：期望章节列表（如 "项目背景/系统设计/测试方案"）
2. 格式规范：正文/标题的字体、字号、行距、缩进
3. 专业术语：企业术语库（从历史文档高频术语提取）

设计要点：
1. 两阶段生成：
   - 统计阶段：从 StructuredDocument 直接统计格式众数（无 LLM）
   - LLM 阶段：可选调用 LLM 提取章节结构与术语（更智能）
2. 与 RAG 解耦：可独立于 ChromaDB 运行（直接读取解析后的文档）
3. 失败容错：LLM 调用失败时回退到纯统计模式
4. 可序列化：StyleProfile 可转为 dict，存入 state 供 Review Agent 使用

StyleProfile 与 Review Agent 的对接：
- expected_sections → 检查文档结构完整性
- body_font/size/line_spacing → 检查正文格式
- heading_style（按层级）→ 检查标题格式
- terminology → 检查术语一致性
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from core.exceptions import LLMError, LLMResponseParseError
from core.llm_client import LLMClient, LLMMessage
from core.logging_config import get_logger
from core.state import StyleProfile as StateStyleProfile
from document.models import StructuredDocument

logger = get_logger("knowledge.style_profile")


# ============================================================
# 数据模型
# ============================================================
@dataclass
class StyleProfile:
    """
    文档风格画像。

    包含从历史文档学习到的格式/结构/术语规范。
    与 core.state.StyleProfile（TypedDict）兼容，通过 to_state_dict() 转换。
    """

    # 结构规范
    expected_sections: list[str] = field(default_factory=list)
    section_order: list[str] = field(default_factory=list)

    # 正文格式规范
    body_font: Optional[str] = None
    body_size_pt: Optional[float] = None
    body_line_spacing: Optional[float] = None
    body_first_line_indent_pt: Optional[float] = None
    body_alignment: Optional[str] = None

    # 标题格式规范（按层级）
    heading_styles: dict[int, dict[str, Any]] = field(default_factory=dict)
    # 例: {1: {"font": "黑体", "size_pt": 18.0}, 2: {...}}

    # 术语库
    terminology: list[str] = field(default_factory=list)

    # 元信息
    sample_doc_count: int = 0
    sample_doc_filenames: list[str] = field(default_factory=list)
    generation_method: str = "stats"  # "stats" / "stats+llm"
    raw_profile_text: Optional[str] = None  # LLM 生成的原始画像文本

    def to_state_dict(self) -> StateStyleProfile:
        """转为 core.state.StyleProfile（TypedDict）格式"""
        return StateStyleProfile(
            expected_sections=self.expected_sections,
            heading_font=self.heading_styles.get(1, {}).get("font"),
            heading_size_pt=self.heading_styles.get(1, {}).get("size_pt"),
            body_font=self.body_font,
            body_size_pt=self.body_size_pt,
            line_spacing=self.body_line_spacing,
            first_line_indent_pt=self.body_first_line_indent_pt,
            terminology=self.terminology,
            sample_doc_count=self.sample_doc_count,
            raw_profile_text=self.raw_profile_text,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_sections": self.expected_sections,
            "section_order": self.section_order,
            "body_font": self.body_font,
            "body_size_pt": self.body_size_pt,
            "body_line_spacing": self.body_line_spacing,
            "body_first_line_indent_pt": self.body_first_line_indent_pt,
            "body_alignment": self.body_alignment,
            "heading_styles": self.heading_styles,
            "terminology": self.terminology,
            "sample_doc_count": self.sample_doc_count,
            "sample_doc_filenames": self.sample_doc_filenames,
            "generation_method": self.generation_method,
            "raw_profile_text": self.raw_profile_text,
        }


# ============================================================
# 风格画像生成器
# ============================================================
class StyleProfileGenerator:
    """
    文档风格画像生成器。

    用法：
        generator = StyleProfileGenerator(llm_client)
        profile = await generator.generate_from_documents([doc1, doc2, ...])
    """

    # 默认企业术语候选（辅助 LLM 提取）
    DEFAULT_TERMINOLOGY_CANDIDATES = [
        "API", "SDK", "URL", "HTTP", "HTTPS", "JSON", "XML", "SQL",
        "TCP", "UDP", "SSL", "TLS", "DNS", "CDN", "JWT", "OAuth",
        "前端", "后端", "中台", "微服务", "容器", "镜像", "编排",
        "灰度", "回滚", "降级", "熔断", "限流", "缓存", "队列",
    ]

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        *,
        use_llm: bool = True,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端（None 或 use_llm=False 时仅用统计模式）
            use_llm: 是否启用 LLM 增强提取
        """
        self.llm = llm_client
        self.use_llm = use_llm and llm_client is not None
        self.logger = get_logger("knowledge.style_profile")

    async def generate_from_documents(
        self,
        documents: list[StructuredDocument],
        *,
        terminology_candidates: Optional[list[str]] = None,
    ) -> StyleProfile:
        """
        从多个历史文档生成风格画像。

        Args:
            documents: StructuredDocument 列表
            terminology_candidates: 术语候选列表（None 用默认）

        Returns:
            StyleProfile 实例
        """
        if not documents:
            self.logger.warning("无历史文档，返回空 StyleProfile")
            return StyleProfile()

        self.logger.info("开始生成风格画像 | 样本数=%d", len(documents))

        # 1. 统计模式：提取格式与结构
        profile = self._extract_stat_profile(documents)
        profile.sample_doc_count = len(documents)
        profile.sample_doc_filenames = [d.filename or "" for d in documents]

        # 2. 术语提取（统计 + 候选词匹配）
        candidates = terminology_candidates or self.DEFAULT_TERMINOLOGY_CANDIDATES
        profile.terminology = self._extract_terminology(documents, candidates)

        # 3. LLM 增强（可选）
        if self.use_llm:
            try:
                await self._enhance_with_llm(profile, documents)
                profile.generation_method = "stats+llm"
                self.logger.info("LLM 增强完成")
            except Exception as e:
                self.logger.warning(
                    "LLM 增强失败（回退到纯统计模式）: %s", e
                )
                profile.generation_method = "stats"

        self.logger.info(
            "风格画像生成完成 | method=%s | sections=%d | terminology=%d",
            profile.generation_method,
            len(profile.expected_sections),
            len(profile.terminology),
        )
        return profile

    # ============================================================
    # 统计提取
    # ============================================================
    def _extract_stat_profile(
        self,
        documents: list[StructuredDocument],
    ) -> StyleProfile:
        """从文档统计提取格式规范"""
        profile = StyleProfile()

        # 收集所有段落
        all_body_fonts: Counter = Counter()
        all_body_sizes: Counter = Counter()
        all_body_line_spacing: Counter = Counter()
        all_body_indent: Counter = Counter()
        all_body_alignment: Counter = Counter()

        heading_data: dict[int, dict[str, Counter]] = {}
        all_sections: Counter = Counter()

        for doc in documents:
            # 复用文档自带的统计
            if doc.body_style_stats.get("font"):
                all_body_fonts[doc.body_style_stats["font"]] += doc.body_style_stats.get("count", 1)
            if doc.body_style_stats.get("size_pt"):
                all_body_sizes[doc.body_style_stats["size_pt"]] += doc.body_style_stats.get("count", 1)

            for level, stats in doc.heading_style_stats.items():
                if level not in heading_data:
                    heading_data[level] = {"fonts": Counter(), "sizes": Counter(), "count": 0}
                if stats.get("font"):
                    heading_data[level]["fonts"][stats["font"]] += stats.get("count", 1)
                if stats.get("size_pt"):
                    heading_data[level]["sizes"][stats["size_pt"]] += stats.get("count", 1)
                heading_data[level]["count"] += stats.get("count", 1)

            # 遍历段落补充行距/缩进/对齐/章节
            for para in doc.paragraphs:
                if para.heading_level is not None:
                    all_sections[para.text] += 1
                elif para.text.strip():
                    if para.style.line_spacing is not None:
                        all_body_line_spacing[para.style.line_spacing] += 1
                    if para.style.first_line_indent_pt is not None:
                        all_body_indent[para.style.first_line_indent_pt] += 1
                    if para.style.alignment is not None:
                        all_body_alignment[para.style.alignment] += 1

        # 取众数
        profile.body_font = all_body_fonts.most_common(1)[0][0] if all_body_fonts else None
        profile.body_size_pt = all_body_sizes.most_common(1)[0][0] if all_body_sizes else None
        profile.body_line_spacing = all_body_line_spacing.most_common(1)[0][0] if all_body_line_spacing else None
        profile.body_first_line_indent_pt = all_body_indent.most_common(1)[0][0] if all_body_indent else None
        profile.body_alignment = all_body_alignment.most_common(1)[0][0] if all_body_alignment else None

        # 各级标题样式
        for level, data in heading_data.items():
            profile.heading_styles[level] = {
                "font": data["fonts"].most_common(1)[0][0] if data["fonts"] else None,
                "size_pt": data["sizes"].most_common(1)[0][0] if data["sizes"] else None,
                "count": data["count"],
            }

        # 章节标题（按出现频次，保留 top 20）
        profile.expected_sections = [s for s, _ in all_sections.most_common(20)]

        return profile

    def _extract_terminology(
        self,
        documents: list[StructuredDocument],
        candidates: list[str],
    ) -> list[str]:
        """通过候选词匹配提取术语"""
        found: Counter = Counter()
        for doc in documents:
            full_text = doc.get_full_text()
            for term in candidates:
                count = full_text.count(term)
                if count > 0:
                    found[term] += count
        # 出现频次 >=1 的术语
        return [term for term, _ in found.most_common(50)]

    # ============================================================
    # LLM 增强
    # ============================================================
    async def _enhance_with_llm(
        self,
        profile: StyleProfile,
        documents: list[StructuredDocument],
    ) -> None:
        """使用 LLM 增强风格画像（提取章节结构建议 + 术语补充）"""
        assert self.llm is not None

        # 准备输入：合并所有文档的大纲
        all_outlines: list[str] = []
        for doc in documents:
            outline = doc.get_heading_outline()
            if outline:
                outline_text = "\n".join(
                    f"{'  ' * (item['level'] - 1)}H{item['level']}: {item['text']}"
                    for item in outline
                )
                all_outlines.append(f"--- {doc.filename} ---\n{outline_text}")

        if not all_outlines:
            return

        outlines_text = "\n\n".join(all_outlines[:5])  # 最多 5 个文档

        prompt = f"""你是企业文档规范专家。请分析以下 {len(documents)} 个历史技术文档的章节大纲，提取企业文档规范。

历史文档章节大纲：
{outlines_text}

已知术语库：{', '.join(profile.terminology[:30])}

请输出 JSON，包含以下字段：
{{
  "recommended_sections": ["章节1", "章节2", ...],  // 推荐的标准章节结构（按顺序）
  "section_descriptions": {{"章节名": "该章节应包含的内容简述"}},  // 每个章节的内容说明
  "additional_terminology": ["术语1", "术语2", ...],  // 补充识别的专业术语
  "style_summary": "对企业文档风格的一段总结"  // 100-200 字
}}

只输出 JSON，不要其他文字。"""

        messages = [LLMMessage(role="user", content=prompt)]
        try:
            result = await self.llm.chat_with_json(messages)

            # 合并 LLM 输出
            recommended = result.get("recommended_sections", [])
            if recommended:
                # 与统计的 sections 合并去重
                existing = set(profile.expected_sections)
                merged = list(profile.expected_sections)
                for sec in recommended:
                    if sec not in existing:
                        merged.append(sec)
                        existing.add(sec)
                profile.expected_sections = merged
                profile.section_order = recommended  # LLM 给出的顺序更权威

            additional_terms = result.get("additional_terminology", [])
            if additional_terms:
                existing_terms = set(profile.terminology)
                for term in additional_terms:
                    if term not in existing_terms:
                        profile.terminology.append(term)
                        existing_terms.add(term)

            summary = result.get("style_summary")
            if summary:
                profile.raw_profile_text = summary

        except LLMResponseParseError as e:
            self.logger.warning("LLM 响应解析失败: %s", e)
            raise
        except LLMError as e:
            self.logger.warning("LLM 调用失败: %s", e)
            raise


# ============================================================
# 便捷函数
# ============================================================
async def generate_style_profile(
    documents: list[StructuredDocument],
    llm_client: Optional[LLMClient] = None,
    *,
    use_llm: bool = True,
) -> StyleProfile:
    """
    便捷函数：生成风格画像。

    Args:
        documents: 历史文档列表
        llm_client: LLM 客户端
        use_llm: 是否启用 LLM

    Returns:
        StyleProfile
    """
    generator = StyleProfileGenerator(llm_client, use_llm=use_llm)
    return await generator.generate_from_documents(documents)
