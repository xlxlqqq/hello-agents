import requests

# wttr.in 对默认 python-requests 的 User-Agent 不友好，会直接断开连接导致 SSL EOF
HEADERS = {
    "User-Agent": "curl/8.4.0",
}

# 定义工具的 schema，让agent直接填充内容，而不是随意联想
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询城市的天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如 北京"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_attraction",
            "description": "根据城市和天气推荐景点",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "weather": {"type": "string", "description": "天气描述，如 Sunny"}
                },
                "required": ["city", "weather"]
            }
        }
    }
]
