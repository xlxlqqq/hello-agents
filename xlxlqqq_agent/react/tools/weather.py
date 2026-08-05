import requests
from tools.base import BaseTool

class WeatherTool(BaseTool):
    name = "get_weather"
    description = "查询城市的天气"

    def parameters(self):
        return {
            "city": {"type": "string", "description": "城市名称，如 北京"}
        }

    def required(self):
        return ["city"]

    def run(self, city: str) -> str:
        url = f"https://wttr.in/{city}?format=j1"
        r = requests.get(url)
        data = r.json()
        cond = data["current_condition"][0]
        desc = cond["weatherDesc"][0]["value"]
        temp = cond["temp_C"]
        return f"{city}天气{desc}，气温{temp}℃"
