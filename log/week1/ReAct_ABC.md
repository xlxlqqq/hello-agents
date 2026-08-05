# 使用抽象基类和工具注册，实现工具的动态加载
## 抽象基类（Abstract Base Classes） workflow
               BaseTool(ABC) 抽象基类
               ┌──────────────────────────────┐
               │ name: str            ← 声明  │
               │ description: str     ← 声明  │
               │                              │
               │ @abstractmethod              │
               │ parameters() → Dict  ← 必实现│
               │ required() → list    ← 必实现│
               │ run(**kwargs) → str  ← 必实现│
               │                              │
               │ to_schema() → Dict   ← 已实现│
               │  (模板方法，子类不用改)       │
               └──────────┬───────────────────┘
                          │ 继承
            ┌─────────────┴──────────────┐
            │                            │
    WeatherTool                    AttractionTool
    ┌───────────────────┐    ┌───────────────────────┐
    │ name = "get_weather"│    │ name = "recommend_attraction"│
    │ description = "..."   │    │ description = "..."          │
    │ parameters() → {...} │    │ parameters() → {city, weather}│
    │ required() → ["city"]│    │ required() → ["city","weather"]│
    │ run(city) → 查天气  │    │ run(city, weather) → 推荐景点  │
    └───────────────────┘    └───────────────────────┘

## 使用原因
"契约式编程"——先定接口契约，所有人按契约写代码，违反就报错。