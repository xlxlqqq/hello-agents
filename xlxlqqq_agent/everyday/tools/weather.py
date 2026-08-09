import requests
from tools.base import BaseTool

# wttr.in 对默认 python-requests 的 User-Agent 不友好，会直接断开连接导致 SSL EOF
# 必须伪装成 curl 才能正常拿到数据（react/tools.py 已验证）
HEADERS = {
    "User-Agent": "curl/8.4.0",
}

# wttr.in 的 weatherCode 中代表「有降水」的代码集合
# 用来判断是否需要提醒带伞；包含雨、雪、冻雨、阵雨等
RAIN_CODES = {
    176, 179, 182, 185,             # 局部降水
    263, 266, 281, 284,             # 冻毛毛雨/冻雨
    293, 296, 299, 302, 305, 308,   # 雨
    311, 314, 317,                  # 大雨/冻雨
    350, 353, 356, 359,             # 阵雨
    362, 365, 368, 371,             # 阵雪/雪
    374, 377, 386, 389, 392, 395,   # 阵雨夹雪
}


class WeatherTool(BaseTool):
    name = "get_weather"
    description = "查询城市当日天气，返回天气状况、温度区间、风速、是否有雨"

    def parameters(self):
        return {
            "city": {"type": "string", "description": "城市名称，如 北京、上海"}
        }

    def required(self):
        return ["city"]

    def run(self, city: str) -> str:
        # 注意：穿衣建议不在这里生成，交给 LLM 按 System Prompt 的规则推断
        # 工具只负责返回原始天气数据，避免规则重复维护
        try:
            url = f"https://wttr.in/{city}?format=j1&lang=zh"
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json()

            # 当前实况
            cond = data["current_condition"][0]
            desc = cond["weatherDesc"][0]["value"]
            temp_c = cond["temp_C"]
            wind_kmph = cond["windspeedKmph"]
            weather_code = int(cond["weatherCode"])

            # 当日温度区间（取 weather 数组第一个，即今天）
            today = data["weather"][0]
            min_temp = today["mintempC"]
            max_temp = today["maxtempC"]

            has_rain = weather_code in RAIN_CODES
            rain_tip = "有降水" if has_rain else "无降水"

            return (
                f"{city}天气{desc}，"
                f"气温{min_temp}~{max_temp}℃（当前{temp_c}℃），"
                f"风速{wind_kmph}km/h，{rain_tip}"
            )
        except Exception as e:
            # 网络失败时返回友好字符串，不抛异常，让简报能优雅降级
            return f"天气查询失败：{type(e).__name__}: {e}"


# TODO F10（P1）：多源天气聚合——wttr.in 失败时自动切换和风天气/OpenWeatherMap 备用源
