from agent import ReactAgent
from tools.registry import ToolRegistry
from tools.weather import WeatherTool
from tools.attraction import AttractionTool


if __name__ == "__main__": 
    registry = ToolRegistry()
    registry.register(WeatherTool())
    registry.register(AttractionTool())

    agent = ReactAgent(registry)

    agent.run("北京天气今天怎么样？适合去哪里游玩？")