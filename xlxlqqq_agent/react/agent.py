from openai import OpenAI, APIConnectionError, APITimeoutError

import os
import json
from dotenv import load_dotenv  # 载入环境变量

from tools.registry import ToolRegistry

# load ENV
load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)


class ReactAgent:
    def __init__(self, registry:ToolRegistry):
        self._max_retries = 3
        self._registry = registry

    def chat(self, messages):
        # 重试机制，不会在LLM断开连接的时候直接抛出异常
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
                import time
                time.sleep(2 ** (attempt - 1))  # 指数退避：1s, 2s, 4s

        raise RuntimeError(f"LLM 请求连续 {self._max_retries} 次失败：{last_err}")
    
    def run(self, user_input:str):
        # http会将下面的字符串序列化成json，放在HTTP请求体里面
        '''
            {
                "model": "deepseek-ai/DeepSeek-V4-Flash",
                "messages": [
                    {"role": "system", "content": "你是旅行助手，未知信息必须查询工具。"},
                    {"role": "user", "content": "北京天气今天怎么样？适合去哪里游玩？"}
                ],
                "temperature": 0.2,
                "tools": [...]
            }
        '''
        messages = [
            {"role": "system", "content":"你是旅行助手，未知信息必须查询工具。"},
            {"role": "user", "content":user_input},
        ]
        
        for step in range(5):
            print(f"\n--- Step {step + 1} ---")

            resp = self.chat(messages)
            # OpenAI API支持一次多个回复，需要挑选第一个候选回复
            msg = resp.choices[0].message
            '''
                msg 不是 dict，是 OpenAI SDK 返回的 ChatCompletionMessage 对象。但 SDK 内部做了适配，序列化时会自动转成正确的 JSON 结构
                等价于：
                    messages.append({
                        "role":"tool",
                        "tool_call_id": tc.id,    # ← 必须有，对应上面 assistant 的 tool_calls[].id
                        "name": name,             # ← 工具名（有些 API 可选）
                        "content": obs,           # ← 工具返回的字符串
                    })
                其中tool_call_id必须有，缺了它，模型会困惑"这个结果对应我哪个调用？"，多工具并发时尤其重要
            '''

            # 模型说话
            if msg.content:
                print("Assistant:", msg.content)

            # 如果模型没用工具 → 结束
            if not msg.tool_calls:
                print("✅ 结束")
                break

            # 把 assistant 的消息（含 tool_calls）加入历史
            messages.append(msg)

            # 逐个执行
            # 一次回答里面可能调用多个工具
            for tc in msg.tool_calls:
                name = tc.function.name
                # tc.function.arguments 是 字符串 （不是 dict）：
                # json.loads() 把它 反序列化成 dict ：
                args = json.loads(tc.function.arguments)

                # tool 是 BaseTool 子类的实例
                tool = self._registry.get(name)
                if not tool:
                    obs = f"工具 {name} 不存在"
                else:
                    # 真正调用工具。 **args 是字典解包，把 {"city":"北京"} 解成 tool.run(city="北京")
                    # obs 就是工具返回的字符串结果（observation）
                    obs = tool.run(**args)
                
                print(f"Tool({name}) -> {obs}")

                messages.append({
                    "role":"tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": obs,
                })

            # 重置工具调用
            msg.tool_calls = []
