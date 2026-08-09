from typing import List


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    # tool 是 BaseTool 子类的实例
    def register(self, tool):
        self._tools[tool.name] = tool

    def get(self, name: str):
        return self._tools.get(name)

    def all(self):
        return list(self._tools.values())

    def schemas(self):
        return [tool.to_schema() for tool in self._tools.values()]
