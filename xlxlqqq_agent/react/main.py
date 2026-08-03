from tools import get_weather
from agent import ask_llm
from prompts import REACT_PROMPT
import re

MAX_STEP = 5
tools = {
    "get_weather": get_weather,
}

def run_agent(user_query: str):
    history = user_query

    for step in range(MAX_STEP):
        print(f"\n--- STEP {step + 1} ---")

        reply = ask_llm(
            system=REACT_PROMPT,
            user=history
        )
        print(reply)

        match = re.search(r"Action:\s*(\w+)\((.*)\)", reply)  # 找到action信号
        finish = re.search(r"Finish\[(.*)\]", reply)  # 找到finish信号

        if finish:
            print(f"\n --- FINAL ANSWER ---", finish.group(1))
            break

        if not match:
            print("没找到 Action，结束")
            break

        # ---- 工具调用逻辑：必须在 for 循环内部 ----
        tool_name, args_str = match.groups()

        # 结果：[('city', '北京'), ('unit', 'c')]
        params = dict(re.findall(r'(\w+)="([^"]+)"', args_str))
        

        if tool_name not in tools:
            obs = f"错误：没有这个工具 {tool_name}"
        else:
            obs = tools[tool_name](**params)

        print("Observation:", obs)
        history += "\n" + reply + f"\nObservation: {obs}"

    else:
        # for...else：循环正常跑完 MAX_STEP（未被 break 中断）才会走到这里
        print("\n⚠️ 达到最大步数，仍未完成")

if __name__ == "__main__": 
    question = "北京今天天气怎么样？"
    run_agent(question)