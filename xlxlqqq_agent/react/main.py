from tools import get_weather, recommend_attraction
from agent import ask_llm, chat_with_tools
from prompts import SYSTEM
from tools import TOOL_SCHEMAS
import re
import json

MAX_STEP = 5
tool_funcs = {
    "get_weather": get_weather,
    "recommend_attraction": recommend_attraction,
}

def run_tool_call(tool_call):
    fn = tool_call.function
    name = fn.name
    args = json.loads(fn.arguments)

    func = tool_funcs.get(name)
    if not func:
        return f"错误：找不到工具 {name}"

    return func(**args)

def parse_action(text: str):
    """
    从 LLM 输出里解析出：
    tool_name, params_dict
    """

    # Finish 优先
    finish = re.search(r"Finish\[(.*)\]", text)
    if finish:
        return "finish", finish.group(1)

    # 匹配 Action: xxx(a="b", c="d")
    m = re.search(r"Action:\s*(\w+)\((.*)\)", text)
    if not m:
        return None, None

    tool_name = m.group(1)
    args_str = m.group(2)

    # 宽容解析：key="value"
    params = {}
    for k, v in re.findall(r'(\w+)\s*=\s*"([^"]+)"', args_str):
        params[k] = v

    return tool_name, params

def run_agent(user_input: str):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_input},
    ]

    for step in range(5):
        print(f"\n--- Step {step + 1} ---")

        resp = chat_with_tools(messages, TOOL_SCHEMAS)
        msg = resp.choices[0].message

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
        for tc in msg.tool_calls:
            obs = run_tool_call(tc)
            print(f"Tool({tc.function.name}) -> {obs}")

            # Observation 塞回去
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.function.name,
                "content": obs,
            })

if __name__ == "__main__": 
    question = "北京今天天气怎么样？适合去哪玩？"
    run_agent(question)