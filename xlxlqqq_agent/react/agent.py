from openai import OpenAI, APIConnectionError, APITimeoutError
import os
from dotenv import load_dotenv  # 载入环境变量

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

def ask_llm(system: str, user: str, max_retries: int = 3) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # 网络偶发故障（代理 SSL EOF 等）自动重试，不让一次抖动直接崩掉整个 Agent
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=os.getenv("MODEL_NAME"),
                messages=messages,
                temperature=0.2,
                timeout=30,
            )
            return resp.choices[0].message.content
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            print(f"  ⚠️ LLM 请求失败（第 {attempt}/{max_retries} 次）：{type(e).__name__}")
            # 指数退避：1s, 2s, 4s
            import time
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"LLM 请求连续 {max_retries} 次失败：{last_err}")
