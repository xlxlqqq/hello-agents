from tools import get_weather, recommend_attraction
from agent import ask_llm
from prompts import REACT_PROMPT
import re

MAX_STEP = 5
tools = {
    "get_weather": get_weather,
    "recommend_attraction": recommend_attraction,
}

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

def run_agent(user_query: str):
    history = user_query

    for step in range(MAX_STEP):
        print(f"\n--- STEP {step + 1} ---")

        reply = ask_llm(
            system=REACT_PROMPT,
            user=history
        )
        print("reply: \n", reply)
        print(f"[debug] history 长度={len(history)} 字符")

        # params 结果：[('city', '北京'), ('unit', 'c')]
        tool_name, params = parse_action(reply)

        if tool_name == "finish":
            # 此时 params 是最终答案字符串，不是字典
            print("\n✅ Final Answer:", params)
            return

        if tool_name is None:
            print("⚠️ 无法解析 Action，结束")
            return

        if tool_name not in tools:
            obs = f"错误：没有这个工具，可用工具：{list(tools.keys())}"
        else:
            try:
                obs = tools[tool_name](**params)
            except TypeError as e:
                # 参数不对，把错误信息丢给模型
                obs = f"工具调用失败，参数错误：{e}。请严格使用 city=\"...\" weather=\"...\" 格式。"

        print("Observation:", obs)
        history += "\n" + reply + f"\nObservation: {obs}"

    else:
        # for...else：循环正常跑完 MAX_STEP（未被 break 中断）才会走到这里
        print("\n 达到最大步数，仍未完成")

if __name__ == "__main__": 
    question = "北京今天天气怎么样？适合去哪玩？"
    run_agent(question)