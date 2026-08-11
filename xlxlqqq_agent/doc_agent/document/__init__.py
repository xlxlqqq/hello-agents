"""
DocGuard Agent - 文档处理层
============================

提供 DOCX 文档的解析、写入、标注能力：
- models: 结构化文档对象模型（与 python-docx 解耦）
- parser: DOCX → StructuredDocument
- writer: StructuredDocument → DOCX（保留原始格式）
- annotator: 修改标注器（高亮 + 批注）
- style_rules: 格式规则定义
"""
