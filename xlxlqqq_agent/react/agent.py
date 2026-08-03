from openai import OpenAI
import os
from dotenv import load_dotenv  # 载入环境变量

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

def ask_llm(system: str, user: str) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    resp = client.chat.completions.create(
        model = os.getenv("MODEL_NAME"),
        messages = messages,
        temperature = 0.2,
    )

    return resp.choices[0].message.content
