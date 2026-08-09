# 每日简报 AI 助手 Agent — PRD（产品需求文档）

> 本文档作为 vibe coding 的规范输入，由 AI 据此自动生成项目骨架与代码。

---

## 一、项目背景与目标

### 背景
用户每天早上需要花时间在多个 App 之间切换：看天气、看新闻、记今日待办、决定穿什么衣服。信息分散、效率低、容易遗漏。

### 目标
构建一个本地运行的「每日简报 AI 助手 Agent」，每天早上自动汇总：
- 当日天气 + 穿衣建议 + 出行注意事项
- 三条重大新闻简要报告
- 今日 todo list（支持新增项目）

通过**一次调用**输出一份结构化简报，并支持通过对话动态修改地址、新增待办。

### 一句话定位
> 一个跑在本地终端的、可对话配置的、基于 Function Calling 的每日简报生成 Agent。

---

## 二、用户故事（User Stories）

| 编号 | 角色 | 故事 | 优先级 |
|---|---|---|---|
| US-1 | 用户 | 我每天早上运行 `python main.py`，能在终端看到一份完整简报 | P0 |
| US-2 | 用户 | 我可以通过对话"把地址改成上海"，下次天气就按上海查 | P0 |
| US-3 | 用户 | 我可以对话"加一条 todo：下午3点开会"，简报里就有这条 | P0 |
| US-4 | 用户 | 简报包含当日天气、穿衣建议、注意事项 | P0 |
| US-5 | 用户 | 简报包含三条重大新闻的简要报告 | P0 |
| US-6 | 用户 | 简报包含今日 todo 列表 | P0 |
| US-7 | 用户 | 配置和 todo 列表能持久化，重启不丢 | P0 |
| US-8 | 用户 | 我可以直接运行命令跳过对话，直接出简报 | P1 |
| US-9 | 用户 | 简报能输出为 Markdown 文件存档 | P1 |

---

## 三、功能需求清单

### 3.1 核心功能（P0 必做）

#### F1：天气查询与穿衣建议
- **输入**：城市名（来自用户配置）
- **输出**：当日天气（温度区间、天气状况、风力），并基于天气给出：
  - 穿衣建议（如"建议穿薄外套+长裤"）
  - 出行注意事项（如"有雨，记得带伞"、"风大，注意高空坠物"）
- **工具**：`get_weather(city) -> dict`
  - 优先用 `wttr.in`（免 key、国内可达性一般，需带 User-Agent: curl/8.4.0）
  - 备选：和风天气 / OpenWeatherMap（需要 API Key）

#### F2：重大新闻获取
- **输入**：无（抓取当日热点）
- **输出**：3 条重大新闻，每条包含：标题 + 50 字以内摘要
- **工具**：`get_news() -> list[dict]`
  - 优先用免费 RSS 源（如央视新闻、澎湃新闻 RSS）
  - 备选：NewsAPI、GDELT（需 key）
  - 抓取后由 LLM 做摘要压缩

#### F3：Todo List 管理
- **能力**：
  - 查询当前所有 todo：`list_todos() -> list[dict]`
  - 新增 todo：`add_todo(content, priority="normal") -> str`
  - 标记完成：`complete_todo(todo_id) -> str`
  - 删除 todo：`delete_todo(todo_id) -> str`
- **持久化**：存到本地 JSON 文件 `data/todos.json`

#### F4：地址配置管理
- **能力**：
  - 查询当前地址：`get_location() -> str`
  - 修改地址：`set_location(city) -> str`
- **持久化**：存到本地 JSON 文件 `data/config.json`

#### F5：简报生成
- 输入：天气 + 新闻 + todos
- 输出：一份格式化 Markdown 简报，结构如下：

```markdown
# 📅 每日简报 — 2026-08-06 上海

## 🌤️ 天气与出行
- 天气：多云转小雨，气温 25~30℃
- 风力：东南风 3 级
- 👔 穿衣建议：建议薄外套+短袖，备伞
- ⚠️ 注意事项：下午有雨，出行带伞；紫外线中等

## 📰 今日要闻
1. **{新闻标题1}** — {50 字摘要}
2. **{新闻标题2}** — {50 字摘要}
3. **{新闻标题3}** — {50 字摘要}

## ✅ 今日待办
- [ ] [高] 下午 3 点开会
- [ ] [中] 提交周报
- [ ] [低] 健身 1 小时

---
_由每日简报 Agent 生成_
```

#### F6：对话式配置
- 用户可以输入自然语言：
  - "把地址改成上海" → 触发 `set_location`
  - "加一条 todo：下午3点开会" → 触发 `add_todo`
  - "直接出简报" → 跳过对话直接生成
- 通过 Function Calling 实现

### 3.2 进阶功能（P1 可选）

| 编号 | 功能 | 说明 |
|---|---|---|
| F7 | 命令行参数模式 | `python main.py --report` 直接出简报，不进对话 |
| F8 | Markdown 存档 | 简报输出到 `reports/2026-08-06.md` |
| F9 | 定时任务 | 配合 Windows 任务计划程序每天自动跑 |
| F10 | 多源天气聚合 | 天气源失败自动切备用源 |

---

## 四、技术架构

### 4.1 技术栈
- **语言**：Python 3.10+
- **LLM SDK**：`openai` 兼容客户端（SiliconFlow / DeepSeek）
- **HTTP**：`requests`（同步、简单）
- **配置加载**：`python-dotenv`
- **持久化**：本地 JSON 文件（不引入数据库）
- **日志**：`print` 即可，不引入 logging 模块（保持学习项目简单）

### 4.2 项目结构（建议骨架）

```
everyday/
├── PRD.md                    ← 本文档
├── .env                      ← API Key 等配置（不入 git）
├── .env.example              ← 配置模板
├── main.py                   ← 入口：对话循环 + 简报生成
├── agent.py                  ← Agent 类：chat / run 逻辑
├── prompts.py                ← System Prompt
├── tools/                    ← 工具包
│   ├── __init__.py
│   ├── base.py               ← BaseTool 抽象基类
│   ├── registry.py           ← ToolRegistry
│   ├── weather.py            ← WeatherTool（天气+穿衣建议）
│   ├── news.py               ← NewsTool（新闻抓取+摘要）
│   ├── todo.py               ← TodoTool（增删改查）
│   └── config.py             ← ConfigTool（地址管理）
├── data/                     ← 运行时数据（自动创建）
│   ├── config.json           ← 用户配置（地址等）
│   └── todos.json             ← todo 列表
└── reports/                  ← 简报存档（自动创建，P1）
    └── 2026-08-06.md
```

### 4.3 信息流（核心循环）

```
用户输入 / "出简报"
     ↓
Agent.run()
     ↓
LLM（带 tools schema）→ 决定调用哪些工具
     ↓
并行/串行执行：
  ├─ get_weather(city) → 天气数据
  ├─ get_news() → 新闻列表
  └─ list_todos() → 待办列表
     ↓
LLM 第二轮（拿到所有工具结果）→ 生成结构化简报
     ↓
打印到终端 + 写入 reports/日期.md
```

### 4.4 工具调用 Schema 设计

```python
# 工具清单（供 LLM 调用）
tools = [
    get_weather(city: str) -> str          # 查天气
    get_news() -> str                       # 查新闻
    list_todos() -> str                     # 列出所有 todo
    add_todo(content: str, priority: str) -> str   # 新增 todo
    complete_todo(todo_id: str) -> str     # 标记完成
    delete_todo(todo_id: str) -> str       # 删除 todo
    get_location() -> str                   # 查当前地址
    set_location(city: str) -> str         # 修改地址
]
```

---

## 五、System Prompt 规范

```text
你是「每日简报助手」，每天为用户生成一份结构化简报。

【能力】
你可以调用以下工具：
- get_weather(city)：查询天气，返回温度、天气、风力
- get_news()：获取 3 条重大新闻
- list_todos()：查看今日待办
- add_todo(content, priority)：新增待办（priority: high/normal/low）
- complete_todo(todo_id)：标记完成
- delete_todo(todo_id)：删除待办
- get_location()：查询当前地址
- set_location(city)：修改地址

【工作流程】
1. 用户说"出简报/今天的简报"时：
   a. 先调用 get_location 获取当前地址
   b. 并行调用 get_weather、get_news、list_todos
   c. 基于结果生成一份 Markdown 格式简报
2. 用户说"改地址为XX"时：调用 set_location 后确认
3. 用户说"加 todo XXX"时：调用 add_todo 后确认

【穿衣建议规则】
- 气温 < 5℃：羽绒服+保暖内衣
- 5-15℃：薄羽绒/厚外套
- 15-22℃：薄外套+长袖
- 22-28℃：短袖+薄外套备用
- > 28℃：短袖短裤
- 有雨：提醒带伞
- 风力 ≥ 5 级：提醒防风
- 紫外线强：提醒防晒

【输出格式】
简报必须用 Markdown，包含三部分：天气与出行、今日要闻、今日待办。
新闻摘要控制在 50 字以内。

【约束】
- 不会编造天气和新闻，必须基于工具返回结果
- 不知道的工具不调用，告知用户不支持
- 地址修改后立即生效
```

---

## 六、数据格式约定

### 6.1 `data/config.json`
```json
{
  "city": "北京",
  "updated_at": "2026-08-06T10:00:00"
}
```

### 6.2 `data/todos.json`
```json
[
  {
    "id": "todo_001",
    "content": "下午3点开会",
    "priority": "high",
    "done": false,
    "created_at": "2026-08-06T09:00:00"
  }
]
```

### 6.3 `.env.example`
```
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
MODEL_NAME=deepseek-ai/DeepSeek-V3
```

---

## 七、开发约束与规范

1. **不引入额外框架**：不使用 LangChain、AutoGen、CrewAI，纯 `openai` SDK + Function Calling，便于学习。
2. **沿用 react 项目结构**：参考 `xlxlqqq_agent/react/` 的代码风格（BaseTool / ToolRegistry / ReactAgent 模式），保持一致。
3. **错误处理**：所有工具调用 try/except，异常转成字符串 Observation 返回给 LLM，不崩程序。
4. **网络重试**：LLM 请求加 3 次指数退避重试（同 react 项目）。
5. **中文优先**：所有 prompt、输出、注释用中文。
6. **代码注释**：关键逻辑加中文注释，解释 why 不解释 what。
7. **依赖最小化**：只用 `openai`、`requests`、`python-dotenv`，其他尽量用标准库。

---

## 八、验收标准（Definition of Done）

- [ ] `python main.py` 能进入对话模式
- [ ] 输入"出简报"能调用 3 个工具并生成完整 Markdown 简报
- [ ] 简报包含天气、穿衣建议、注意事项、3 条新闻摘要、todo 列表
- [ ] 输入"改地址为上海"能修改配置并下次简报按上海查
- [ ] 输入"加 todo 下午开会"能新增 todo 并持久化
- [ ] 重启程序后配置和 todo 不丢
- [ ] 工具失败时简报能优雅降级（如新闻拉不到，提示"新闻暂时不可用"）
- [ ] 代码结构与 `react/` 项目风格一致

---

## 九、Vibe Coding 启动指令（给 AI 的第一条 prompt）

> 你可以基于下面的指令启动 vibe coding：

```
请基于 d:\xlxlqqq\documents\project\hello-agents\xlxlqqq_agent\everyday\PRD.md
构建「每日简报 AI 助手 Agent」。

要求：
1. 严格按 PRD 中的项目结构创建文件
2. 沿用 ../react/ 项目的代码风格（BaseTool / ToolRegistry / Agent 类）
3. 优先实现 P0 功能，P1 先留 TODO
4. 先生成项目骨架（空文件 + docstring），再逐个填充实现
5. 每生成一个模块，运行一次确保 import 不报错

第一步：先创建目录结构和所有空文件，并写好每个文件的 docstring 说明用途。
```
