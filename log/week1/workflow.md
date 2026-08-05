# OpenAI Chat API 规范里角色的处理方式

## 接口形式
OpenAI Chat API 规范里 role 字段有 4 种， 模型对不同 role 的处理方式不同 ：
system： 系统提示，用于设置模型的行为和规则。
user： 用户输入的消息，用于触发模型的思考。
assistant： 模型的回复，用于回答用户的问题。
tool： 模型调用函数的消息，用于执行函数调用。

## 否则
如果只用拼接，相当于把所有内容塞进一个 user 消息：  
    "你是旅行助手... 用户问：北京天气... 我之前回答了... 工具返回了..."
模型会分不清哪句是"指令"（要遵守）、哪句是"用户问题"（要回答）、哪句是"事实"（要基于它推理）。

而用结构化 messages，模型训练时已经学到：
    system → 当作硬约束
    user → 当作要响应的输入
    assistant → 当作自己说过的话（保持一致性）
    tool → 当作权威事实（不能编造）

## 工作流 workflow
通过append操作，在循环中输入给LLM的messages将会变成：

    STEP 1 开始时：messages 有 2 条
    ─────────────────────────────────────
    [0] {role: "system",  content: "你是旅行助手..."}
    [1] {role: "user",    content: "北京天气今天怎么样？"}

    STEP 1 模型返回 tool_calls=get_weather(...)
    ─────────────────────────────────────
    [2] {role: "assistant", tool_calls: [{name:"get_weather",...}]}  ← append
    [3] {role: "tool", content: "北京天气Patchy rain，气温28℃"}      ← append

    STEP 2 模型返回 tool_calls=recommend_attraction(...)
    ─────────────────────────────────────
    [4] {role: "assistant", tool_calls: [{name:"recommend_attraction",...}]}  ← append
    [5] {role: "tool", content: "北京下雨，推荐国家博物馆"}                  ← append

    STEP 3 模型返回 content="最终答案..."，不再调用工具
    ─────────────────────────────────────
    [6] {role: "assistant", content: "北京今天小雨..."}  ← append
    → break 退出