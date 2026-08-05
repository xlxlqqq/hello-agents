from tools.base import BaseTool

class AttractionTool(BaseTool):
    name = "recommand_attraction"
    description = "根据城市和天气推荐景点"

    def parameters(self):
        return {
            "city": {"type": "string"},
            "weather": {"type": "string", "description": "天气描述，如 Sunny"},
        }

    def required(self):
        return ["city", "weather"]

    def run(self, city:str, weather:str) -> str:
        if "雨" in weather.lower():
            return f"{city}有雨，推荐室内经典，如国家博物馆等。"
        else:
            return f"{city}天气{weather},适合户外经典，如颐和园等。"

