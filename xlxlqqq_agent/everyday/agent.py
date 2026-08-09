import json
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI, APIConnectionError, APITimeoutError

from prompts import SYSTEM
from tools.registry import ToolRegistry

# 载入 .env 环境变量（OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME）
load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

# 运行时数据目录：基于本文件位置推算，保证从任意工作目录启动都一致
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE_DIR, "data")
REPORTS_DIR = os.path.join(_BASE_DIR, "reports")


class EverydayAgent:
    def __init__(self, registry: ToolRegistry):
        self._max_retries = 3
        self._registry = registry
        # 首次运行时自动创建 data/ 和 reports/ 目录，用户无需手动建
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def chat(self, messages):
        # 重试机制：LLM 断连/超时不直接抛异常，指数退避重试 3 次
        for attempt in range(1, self._max_retries + 1):
            try:
                return client.chat.completions.create(
                    model=os.getenv("MODEL_NAME"),
                    messages=messages,
                    temperature=0.2,
                    timeout=30,
                    tools=self._registry.schemas(),
                )
            except (APIConnectionError, APITimeoutError) as e:
                last_err = e
                print(f"  ⚠️ LLM 请求失败（第 {attempt}/{self._max_retries} 次）：{type(e).__name__}")
                time.sleep(2 ** (attempt - 1))  # 指数退避：1s, 2s, 4s

        raise RuntimeError(f"LLM 请求连续 {self._max_retries} 次失败：{last_err}")

    def run(self, user_input: str):
        # 构建 messages：system prompt 定义助手身份与规则，user 是本次输入
        # 注入当天日期，避免 LLM 被新闻内容里的旧日期带偏（简报标题要用今天）
        today = datetime.now().strftime("%Y-%m-%d")
        system_content = f"{SYSTEM}\n【当前日期】今天是 {today}，简报标题请用此日期。"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_input},
        ]

        # final_content 保留最后一轮 assistant 输出（即最终简报），供邮件发送复用
        final_content = ""
        # 多轮工具调用循环：最多 5 步，覆盖「取地址 → 查天气+新闻+待办 → 生成简报」流程
        for step in range(5):
            print(f"\n--- Step {step + 1} ---")

            resp = self.chat(messages)
            # OpenAI API 支持一次返回多个候选，默认 n=1，取第一个
            msg = resp.choices[0].message

            # 模型说话（打印中间思考/最终简报）；同时保留最后一段内容作为返回值
            if msg.content:
                print("Assistant:", msg.content)
                final_content = msg.content

            # 模型没用工具 → 流程结束
            if not msg.tool_calls:
                print("✅ 结束")
                # TODO F8（P1）：把最终简报写入 reports/{日期}.md 存档
                break

            # 把 assistant 消息（含 tool_calls）加入历史，便于下一轮模型看到自己调了什么
            messages.append(msg)

            # 逐个执行本轮的工具调用（一轮可能并发多个：如 get_weather + get_news + list_todos）
            for tc in msg.tool_calls:
                name = tc.function.name
                # tc.function.arguments 是字符串，需反序列化成 dict 才能用 **args 解包
                args = json.loads(tc.function.arguments)

                tool = self._registry.get(name)
                if not tool:
                    obs = f"工具 {name} 不存在"
                else:
                    # **args 解包：把 {"city":"北京"} 解成 tool.run(city="北京")
                    obs = tool.run(**args)

                print(f"Tool({name}) -> {obs}")

                # 工具结果必须带 tool_call_id，否则模型无法对应是哪个调用的返回
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": obs,
                })

        # 循环结束（正常完成或 break），返回最终简报文本；对话模式下 main.py 不使用返回值
        return final_content
