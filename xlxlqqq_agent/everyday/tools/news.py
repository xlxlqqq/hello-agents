import json
import re
import xml.etree.ElementTree as ET

import requests
from tools.base import BaseTool

# 候选 RSS 源（按序尝试，首个成功即用）
# 说明：RSSHub 公共实例 rsshub.app 经常返回 403（限流/封禁），不稳定；
# 改用人民网官方 RSS 直连，稳定且为中文内容。多个分类保证新闻多样性。
RSS_SOURCES = [
    "http://www.people.com.cn/rss/politics.xml",   # 人民网-政治
    "http://www.people.com.cn/rss/world.xml",      # 人民网-国际
    "http://www.people.com.cn/rss/society.xml",    # 人民网-社会
    "http://www.people.com.cn/rss/finance.xml",    # 人民网-财经
]

HEADERS = {
    "User-Agent": "curl/8.4.0",
}

# 匹配 HTML 标签，用来清理 description 里的 <p>...</p> 等标签
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(text: str) -> str:
    """去掉 HTML 标签和多余空白，给 LLM 干净的摘要输入"""
    if not text:
        return ""
    text = HTML_TAG_RE.sub("", text)
    return text.strip()


class NewsTool(BaseTool):
    name = "get_news"
    description = "获取当日重大新闻，每条包含标题和摘要（摘要由 LLM 进一步压缩到 50 字以内）"

    def parameters(self):
        return {}

    def required(self):
        return []

    def run(self) -> str:
        # 聚合多个源，去重后取前 5 条给 LLM，LLM 在生成简报时挑 3 条并压缩摘要
        all_items = []
        seen_titles = set()

        for src_url in RSS_SOURCES:
            try:
                items = self._fetch_and_parse(src_url)
                for it in items:
                    if it["title"] and it["title"] not in seen_titles:
                        seen_titles.add(it["title"])
                        all_items.append(it)
                # 已攒够候选就不再抓更多源
                if len(all_items) >= 5:
                    break
            except Exception as e:
                # 当前源失败，静默切换下一个源（打印便于调试，不崩程序）
                print(f"  ⚠️ 新闻源 {src_url} 失败：{type(e).__name__}")
                continue

        if all_items:
            return json.dumps(all_items[:5], ensure_ascii=False)

        # 所有源都失败，返回降级字符串，简报会显示「新闻暂时不可用」
        return "新闻暂时不可用，请稍后重试"

    def _fetch_and_parse(self, url: str) -> list:
        """抓取并解析单个 RSS 源，返回 [{title, summary, link}, ...]"""
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()

        # RSS 是 XML 格式，用标准库 ElementTree 解析，不引入 feedparser（依赖最小化）
        root = ET.fromstring(r.content)

        items = []
        # RSS 2.0 结构：rss > channel > item
        for item in root.iter("item"):
            title_el = item.find("title")
            desc_el = item.find("description")
            link_el = item.find("link")

            title = _clean_text(title_el.text) if title_el is not None and title_el.text else ""
            summary = _clean_text(desc_el.text) if desc_el is not None and desc_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""

            if title:
                items.append({"title": title, "summary": summary, "link": link})

        return items
