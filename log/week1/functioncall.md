━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 开始
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【发送给 LLM 的请求】
{
  "model": "deepseek-ai/DeepSeek-V4-Flash",
  "messages": [
    {"role":"system","content":"你是智能旅行助手..."},        ← 业务意图
    {"role":"user",  "content":"北京今天天气怎么样？适合去哪玩？"}
  ],
  "tools": TOOL_SCHEMAS,        ← 结构化工具契约（不是 prompt！）
  "tool_choice": "auto"          ← 让模型自己决定是否调用
}

【LLM 返回】（注意：不是文本，是结构化对象）
message = {
  "role": "assistant",
  "content": null,                          ← 注意没有文本输出
  "tool_calls": [{
    "id": "call_abc123",                     ← 这次调用的唯一 ID
    "type": "function",
    "function": {
      "name": "get_weather",                ← 模型选的工具
      "arguments": '{"city": "北京"}'        ← 参数是 JSON 字符串
    }
  }]
}

【代码处理】main.py:73-86
  1. messages.append(msg)            ← 把 assistant 的 tool_calls 消息加进历史
  2. run_tool_call(tc) → 调用 get_weather(city="北京")
  3. obs = "北京天气Patchy rain nearby，气温28℃"
  4. messages.append({
       "role":"tool",                  ← 注意 role 是 "tool"，不是 "user"
       "tool_call_id":"call_abc123",   ← 必须对应上面的 id
       "name":"get_weather",
       "content":"北京天气...28℃"       ← 工具返回值
     })

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 开始 — 此时 messages 已经有 4 条
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【发送给 LLM 的请求】
messages = [
  {"role":"system","content":"你是智能旅行助手..."},
  {"role":"user",  "content":"北京今天天气怎么样？适合去哪玩？"},
  {"role":"assistant","tool_calls":[{...get_weather...}]},  ← Step 1 的请求
  {"role":"tool","content":"北京天气...28℃"}                 ← Step 1 的结果
]

【LLM 返回】（模型看到 Step 1 的结果后，决定下一步）
message = {
  "role": "assistant",
  "tool_calls": [{
    "id": "call_def456",
    "function": {
      "name": "recommend_attraction",
      "arguments": '{"city":"北京","weather":"Patchy rain nearby"}'
    }
  }]
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — 模型已经拿到所有需要的信息，不再调用工具
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

message = {
  "role": "assistant",
  "content": "北京今天小雨，气温28℃。推荐你去国家博物馆...",  ← 直接回答
  "tool_calls": null                                              ← 不调用工具
}

【代码处理】main.py:68-70
  if not msg.tool_calls:
      print("✅ 结束")
      break