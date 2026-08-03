import requests

# wttr.in 对默认 python-requests 的 User-Agent 不友好，会直接断开连接导致 SSL EOF
HEADERS = {
    "User-Agent": "curl/8.4.0",
}

# tools 查询天气
def get_weather(city: str) -> str:
    url = f"https://wttr.in/{city}?format=j1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        cond = data["current_condition"][0]
        desc = cond["weatherDesc"][0]["value"]
        temp = cond["temp_C"]
        return f"{city}天气{desc}，气温{temp}℃"
    except Exception as e:
        # 把异常作为 Observation 返回给 LLM，让它自己决定重试还是换思路
        return f"工具调用失败：{type(e).__name__}: {e}"

# tools 推荐景点
def recommend_attraction(city: str, weather: str) -> str:
    """
    这个函数故意写得“很笨”
    目的是：让模型来做判断，而不是代码来做
    """
    if "雨" in weather or "rain" in weather.lower():
        return f"{city}下雨，推荐室内景点：国家博物馆"
    return f"{city}天气{weather}，适合户外景点：颐和园"