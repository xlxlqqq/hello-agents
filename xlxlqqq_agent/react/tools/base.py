from abc import ABC, abstractmethod
from typing import Any, Dict
# 类型注解 用，不影响代码运行，只是给人看的（以及给 IDE / 静态检查工具用的）。

'''
抽象基类（Abstract Base Classes）
# ABC（抽象基类）：定义了工具的基本接口，所有工具都必须实现这个接口。
# abstractmethod：定义了抽象方法，必须在子类中实现。
# 不遵守就 直接报错，不让你实例化 。这就是 ABC + abstractmethod 的核心价值。

我叫它干嘛？ name
它干嘛用？ description
怎么用它？ parameters + run()
怎么告诉模型？ to_schema()
'''

class BaseTool(ABC): 
    name: str
    description: str

    @abstractmethod
    # @abstractmethod = "子类必须实现这个方法，不然不算合格的子类"
    def parameters(self) -> Dict[str, Any]:
        # 返回值注解 -> Dict[str, Any] 这个方法返回一个字典，key 是字符串，value 任意类型。
        """返回 JSON Schema 的 properties"""
        pass
        # "空语句占位符"——抽象方法不需要有具体实现，写 pass 就行。真正逻辑在子类里。
    
    @abstractmethod
    def required(self) -> list[str]:
        pass

    @abstractmethod
    def run(run, **kwargs) -> str:
        pass

    # to_schema() 普通方法（不是抽象方法！）
    def to_schema(self) -> Dict:
        ''' 将工具转换为 JSON Schema 格式 '''
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters(),
                    "required": self.required(),
                },
            },
        }