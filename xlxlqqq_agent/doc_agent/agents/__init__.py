"""
DocGuard Agent - Multi-Agent 层
================================

提供基于 LangGraph 的多 Agent 编排：
- base: Agent 抽象基类，定义统一接口与异常处理
- planner_agent: 任务规划
- parser_agent: DOCX 解析
- retrieval_agent: 知识库检索
- review_agent: 问题审查
- repair_agent: 自动修复
- validation_agent: 修复验证
- workflow: LangGraph 工作流编排
"""
