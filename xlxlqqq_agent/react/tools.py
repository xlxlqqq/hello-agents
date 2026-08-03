import requests

# tools 查询天气
def get_weather(city: str) -> str:
    url = f"https://wttr.in/{city}?format=j1"
    r = requests.get(url)
    data = r.json()
    cond = data["current_condition"][0]
    desc = cond["weatherDesc"][0]["value"]
    temp = cond["temp_C"]
    return f"{city}天气{desc}，气温{temp}℃"