"""
DocGuard Agent - 文档格式规则定义
==================================

设计要点：
1. 规则与代码解耦：规则以 JSON 文件存储在 rules/ 目录，运行时加载
2. 本模块提供规则数据模型 + 加载器 + 默认规则生成器
3. 规则分两类：
   - StyleRule: 格式规则（字体/字号/行距等）
   - ContentRule: 内容规则（必含章节/术语等）
4. 规则带 severity 字段，便于 Review Agent 分级报告
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.exceptions import ConfigError
from core.logging_config import get_logger

logger = get_logger("document.style_rules")


# ============================================================
# 规则数据模型
# ============================================================
@dataclass
class StyleRule:
    """单条格式规则"""

    rule_id: str
    name: str
    description: str
    category: str                 # "font" / "size" / "alignment" / "indent" / "spacing" / "heading"
    severity: str = "major"       # "critical" / "major" / "minor" / "info"
    # 适用范围
    applies_to: str = "body"      # "body" / "heading" / "table" / "all"
    heading_level: Optional[int] = None  # 仅 applies_to=heading 时生效
    # 规则参数（键值对，由具体规则解释）
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "applies_to": self.applies_to,
            "heading_level": self.heading_level,
            "params": self.params,
            "enabled": self.enabled,
        }


@dataclass
class ContentRule:
    """单条内容规则"""

    rule_id: str
    name: str
    description: str
    category: str                 # "structure" / "terminology" / "completeness"
    severity: str = "major"
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "params": self.params,
            "enabled": self.enabled,
        }


@dataclass
class RuleSet:
    """规则集合"""

    style_rules: list[StyleRule] = field(default_factory=list)
    content_rules: list[ContentRule] = field(default_factory=list)
    terminology: list[str] = field(default_factory=list)
    version: str = "1.0"

    def get_enabled_style_rules(self) -> list[StyleRule]:
        return [r for r in self.style_rules if r.enabled]

    def get_enabled_content_rules(self) -> list[ContentRule]:
        return [r for r in self.content_rules if r.enabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "style_rules": [r.to_dict() for r in self.style_rules],
            "content_rules": [r.to_dict() for r in self.content_rules],
            "terminology": self.terminology,
        }


# ============================================================
# 默认规则集（企业研发文档规范）
# ============================================================
def get_default_rule_set() -> RuleSet:
    """
    获取默认规则集（企业研发文档通用规范）。

    当 rules/ 目录下没有配置文件时使用此默认规则。
    可作为初始化模板。
    """
    style_rules = [
        # ----- 标题规则 -----
        StyleRule(
            rule_id="SR-H001",
            name="一级标题字体",
            description="一级标题应为黑体或微软雅黑",
            category="font",
            severity="major",
            applies_to="heading",
            heading_level=1,
            params={"allowed_fonts": ["黑体", "微软雅黑", "SimHei", "Microsoft YaHei"]},
        ),
        StyleRule(
            rule_id="SR-H002",
            name="一级标题字号",
            description="一级标题字号应为 18pt（二号）",
            category="size",
            severity="major",
            applies_to="heading",
            heading_level=1,
            params={"size_pt": 18.0, "tolerance_pt": 0.5},
        ),
        StyleRule(
            rule_id="SR-H003",
            name="二级标题字号",
            description="二级标题字号应为 16pt（三号）",
            category="size",
            severity="major",
            applies_to="heading",
            heading_level=2,
            params={"size_pt": 16.0, "tolerance_pt": 0.5},
        ),
        StyleRule(
            rule_id="SR-H004",
            name="三级标题字号",
            description="三级标题字号应为 14pt（四号）",
            category="size",
            severity="minor",
            applies_to="heading",
            heading_level=3,
            params={"size_pt": 14.0, "tolerance_pt": 0.5},
        ),
        # ----- 正文规则 -----
        StyleRule(
            rule_id="SR-B001",
            name="正文字体",
            description="正文应为宋体",
            category="font",
            severity="major",
            applies_to="body",
            params={"allowed_fonts": ["宋体", "SimSun"]},
        ),
        StyleRule(
            rule_id="SR-B002",
            name="正文字号",
            description="正文字号应为 12pt（小四）",
            category="size",
            severity="major",
            applies_to="body",
            params={"size_pt": 12.0, "tolerance_pt": 0.5},
        ),
        StyleRule(
            rule_id="SR-B003",
            name="正文行距",
            description="正文行距应为 1.5 倍",
            category="spacing",
            severity="minor",
            applies_to="body",
            params={"line_spacing": 1.5, "tolerance": 0.1},
        ),
        StyleRule(
            rule_id="SR-B004",
            name="正文首行缩进",
            description="正文段落首行缩进 2 字符（约 24pt）",
            category="indent",
            severity="minor",
            applies_to="body",
            params={"first_line_indent_pt": 24.0, "tolerance_pt": 4.0},
        ),
        StyleRule(
            rule_id="SR-B005",
            name="正文对齐方式",
            description="正文应两端对齐",
            category="alignment",
            severity="minor",
            applies_to="body",
            params={"alignment": "justify"},
        ),
    ]

    content_rules = [
        ContentRule(
            rule_id="CR-S001",
            name="必含章节-项目背景",
            description="文档应包含'项目背景'章节",
            category="structure",
            severity="major",
            params={"keywords": ["项目背景", "背景介绍", "Project Background"]},
        ),
        ContentRule(
            rule_id="CR-S002",
            name="必含章节-系统设计",
            description="文档应包含'系统设计'或'总体设计'章节",
            category="structure",
            severity="major",
            params={"keywords": ["系统设计", "总体设计", "架构设计", "System Design"]},
        ),
        ContentRule(
            rule_id="CR-S003",
            name="必含章节-测试方案",
            description="技术文档建议包含'测试方案'章节",
            category="structure",
            severity="minor",
            params={"keywords": ["测试方案", "测试计划", "Test Plan"]},
        ),
    ]

    terminology = [
        "API", "SDK", "URL", "URI", "HTTP", "HTTPS", "JSON", "XML",
        "SQL", "TCP", "UDP", "SSL", "TLS", "DNS", "CDN",
        "前端", "后端", "中台", "微服务", "容器", "镜像",
    ]

    return RuleSet(
        style_rules=style_rules,
        content_rules=content_rules,
        terminology=terminology,
        version="1.0",
    )


# ============================================================
# 规则加载器
# ============================================================
class RuleLoader:
    """规则文件加载器"""

    def __init__(self, rules_dir: str) -> None:
        self.rules_dir = Path(rules_dir)
        self.logger = get_logger("document.style_rules.loader")

    def load(self) -> RuleSet:
        """
        从 rules/ 目录加载规则集。

        加载顺序：
        1. style_rules.json（格式规则）
        2. content_rules.json（内容规则）
        3. terminology.json（术语库）

        任一文件缺失则使用默认规则。

        Returns:
            RuleSet 实例
        """
        if not self.rules_dir.exists():
            self.logger.warning(
                "规则目录不存在，使用默认规则: %s", self.rules_dir
            )
            return get_default_rule_set()

        style_rules = self._load_style_rules()
        content_rules = self._load_content_rules()
        terminology = self._load_terminology()

        return RuleSet(
            style_rules=style_rules,
            content_rules=content_rules,
            terminology=terminology,
            version="1.0",
        )

    def _load_style_rules(self) -> list[StyleRule]:
        """加载格式规则"""
        path = self.rules_dir / "style_rules.json"
        if not path.exists():
            self.logger.info("style_rules.json 不存在，使用默认格式规则")
            return get_default_rule_set().style_rules

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rules = []
            for item in data.get("rules", []):
                rules.append(StyleRule(
                    rule_id=item["rule_id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    category=item["category"],
                    severity=item.get("severity", "major"),
                    applies_to=item.get("applies_to", "body"),
                    heading_level=item.get("heading_level"),
                    params=item.get("params", {}),
                    enabled=item.get("enabled", True),
                ))
            self.logger.info("加载格式规则 %d 条: %s", len(rules), path)
            return rules
        except Exception as e:
            self.logger.error("加载 style_rules.json 失败，使用默认: %s", e)
            return get_default_rule_set().style_rules

    def _load_content_rules(self) -> list[ContentRule]:
        """加载内容规则"""
        path = self.rules_dir / "content_rules.json"
        if not path.exists():
            self.logger.info("content_rules.json 不存在，使用默认内容规则")
            return get_default_rule_set().content_rules

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rules = []
            for item in data.get("rules", []):
                rules.append(ContentRule(
                    rule_id=item["rule_id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    category=item["category"],
                    severity=item.get("severity", "major"),
                    params=item.get("params", {}),
                    enabled=item.get("enabled", True),
                ))
            self.logger.info("加载内容规则 %d 条: %s", len(rules), path)
            return rules
        except Exception as e:
            self.logger.error("加载 content_rules.json 失败，使用默认: %s", e)
            return get_default_rule_set().content_rules

    def _load_terminology(self) -> list[str]:
        """加载术语库"""
        path = self.rules_dir / "terminology.json"
        if not path.exists():
            self.logger.info("terminology.json 不存在，使用默认术语库")
            return get_default_rule_set().terminology

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            terms = data.get("terms", [])
            self.logger.info("加载术语 %d 条: %s", len(terms), path)
            return terms
        except Exception as e:
            self.logger.error("加载 terminology.json 失败，使用默认: %s", e)
            return get_default_rule_set().terminology

    def save_default_rules(self) -> None:
        """将默认规则集保存到 rules/ 目录（作为初始化模板）"""
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        default = get_default_rule_set()

        # style_rules.json
        style_path = self.rules_dir / "style_rules.json"
        style_path.write_text(
            json.dumps(
                {"version": default.version, "rules": [r.to_dict() for r in default.style_rules]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # content_rules.json
        content_path = self.rules_dir / "content_rules.json"
        content_path.write_text(
            json.dumps(
                {"version": default.version, "rules": [r.to_dict() for r in default.content_rules]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # terminology.json
        term_path = self.rules_dir / "terminology.json"
        term_path.write_text(
            json.dumps(
                {"version": default.version, "terms": default.terminology},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.logger.info("默认规则集已保存到: %s", self.rules_dir)
