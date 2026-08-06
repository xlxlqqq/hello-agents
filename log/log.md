# 20260803
## 原因
写一个ReAct的Agent，用于回答用户的问题。

目标完成
- 查询天气
- 在天气的基础上，推荐一个景点

## 设计路线
 写一个ReAct的Agent，定义以下步骤：
    定义循环轮次
    给定prompt
    给定tool
    在main函数中实现agent结构

### 详细工作流
    - 用户输入问题
    - LLM思考需要怎么实现相关功能，根据问题调用tool（调用tool需要用正则表达进行解析）。
    - Agent根据问题调用tool，获取天气信息（tool中需要增设查询天气的接口）
    - Agent根据天气信息推荐景点
    - Agent将推荐结果返回给用户

    规定agent有两种回答
    1. 直接回答用户问题，标注为FINISH关键字，用户正则表达匹配到FINISH关键字，即可结束循环
    2. 需要执行下一步动作，标注为ACTION关键字，用户正则表达匹配到ACTION关键字，即可执行下一步动作，比如调用相关tool。

    后续在agent中进行改进，由于各种模型都支持Function Calling / JSON Schema，所以可以使用Function Calling / JSON Schema来解决。不需要正则表达式来搜索FINISH和ACTION关键字，直接根据Function Calling / JSON Schema的返回结果，即可判断是否需要结束循环。

    使用Function Call的详细工作流，可以参考./week1/functioncall.md中的描述

## 踩坑
需要增加设计：
    超时timeout
    与LLM的聊天重试机制，丢错机制


## todo
- 能不能不让模型乱写参数，而是让模型填表？
    通过Function Calling / JSON Schema来解决
    已经解决 20260803
    思路： 由于各种模型都支持Function Calling / JSON Schema，所以可以使用Function Calling / JSON Schema来解决。
- 工具描述、检索、路由（Tool Router）来解决大量tool总是选错的问题
- agent总是在改用tool的时候，不用tool，
    通过强制工具约束（System Prompt + 惩罚）
- 模型的自己部署自己推理，而不是让OpenAI的服务器帮我推理
    硬件（CPU/GPU/NPU，消费卡 CUDA 架构）
    ↓
    算子层：GEMM、Softmax、RoPE、Attention 算子
    ↓
    注意力优化：FlashAttention / PagedAttention / KV Cache 量化
    ↓
    权重压缩：FP16→INT8/INT4，GGUF/K-quant/AWQ/GPTQ
    ↓
    推理引擎：llama.cpp（C++）/ vLLM / TensorRT-LLM
    ↓
    服务层：OpenAI 兼容 API、batch、吞吐/延迟调优
    ↓
    你的 Agent 只在这最上层