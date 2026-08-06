# 整体设计
Llama-3.3-70B Q4_K_M（llama.cpp 走通原理） + Qwen3-32B Q8/AWQ（vLLM 走通服务化）；量化管权重常驻、KV-Q8/FP8 管长上下文、FlashAttn+PagedAttn 管显存与吞吐，这三件事跑通并各写一份对比数据

## Llama-3.3-70B Q4_K_M（llama.cpp 走通原理）
- 确认 48GB 卡能跑，拿到第一手显存/速度数据，不被黑盒。
- 拆 GGUF 与 Q4_K_M 量化原理
    GGUF 格式：读 gguf.py 和 llama.cpp gguf 头文件，搞懂 magic/version/metadata/tensor-info/data 四段结构、mmap 加载为什么冷启动快。
    Q4_K_M 不是裸 INT4：它是 K-quant——super-block(256) 带全局 scale/min，sub-block(16/32) 带细化 scale，attention 输出投影等敏感 tensor 用更高 bit，FFN 压更低，平均 ~4.5–4.85 bpw。
    自己量化一次：HF FP16 → convert_hf_to_gguf.py → llama-quantize xxx.f16.gguf xxx-Q4_K_M.gguf Q4_K_M，对比 Q8_0 / Q5_K_M / Q4_0 同模型在 5 个 GSM8K/MMLU 子集上的掉点。
    延伸读：AWQ（激活感知）/ GPTQ（Hessian）与 GGUF K-quant 的本质区别——前者要校准集，后者无校准；服务端 vLLM 用 AWQ/GPTQ，端侧 llama.cpp 用 K-quant
- 拆 llama.cpp 推理循环
    从 token 进出看清 Transformer 前向在 C++/CUDA 里怎么跑。
    按这个顺序读 llama.cpp/src：
        llama_context 初始化、llama_init_from_model → 权重 mmap、backend 选择（CUDA/CPU 分层）。
        llama_tokenize → llama_decode（prefill 整批进，decode 单 token 进）。
        ggml 计算图：llama_build_graph 里 RMSNorm → RoPE → MHA/GQA → FFN 的算子怎么拼。
        KV cache 底层：llama_kv_cache 结构，行式存储、GQA 下 K/V 头数少于 Q 头、cache 怎么随 ctx 增长；改一次 --cache-type-k q4_0 看显存差几 GB。
        FlashAttention 入口：llama.cpp 的 -fa 走的是自研融合 kernel（不是 PyTorch FA2），读 ggml-cuda.cu 里 flash attention 分支，理解为什么 O(N²) 中间量被干掉。
        层卸载：-ngl 99 全上 GPU；故意设 -ngl 40 看 CPU offload 哪几层、PCIe 怎么成瓶颈。
- 横向切到服务端引擎
    把同一个 Llama-3.3-70B 用 AWQ INT4​ 转一份，vLLM 起服务：--kv-cache-dtype fp8 --enable-chunked-prefill --gpu-memory-utilization 0.92。
    读 vLLM 三件套源码级概念：PagedAttention（KV 按 block 分页，消除碎片）、Continuous Batching（静态 batch 对比）、Scheduler（FCFS/抢占）。
    用 GenAI-Perf 或 k6 打 8 并发，对比 llama.cpp 单流：TTFT / TPOT / QPS 差几倍。
    顺手读 FlashAttention-2 论文 + Dao 的代码导览，明白 IO-aware 和 llama.cpp 融合 kernel 的异同。
- 挑一个垂直点造轮子
    从下面选 一个，别全做：
        A. 量化：给 llama.cpp 加一种新 K-quant 变体（如 Q4_K_M 的 KV 感知版），或手写一段 dequantize_q4_k_to_f16 的 CUDA kernel，用 ncu 看带宽。
        B. 推理引擎 mini：纯 C++ 写个 300 行 mini-decoder（embedding + RMSNorm + 单层 MHA + LM head），加载 Q4_K_M gguf 的某一层权重跑通前向。
        C. KV 优化：在 llama.cpp 里把 KV cache 换成 paged 布局做 ablation（难，但极度加分）。
        D. 投机解码：draft model（7B）＋ target（70B Q4_K_M）在 llama.cpp 上接起来，测 TPOT 提升。
        
## 目标岗位关键词：LLM Inference Engineer / AI Infra / 推理优化 / 端侧部署。
简历三段式：① 48GB 卡上 Llama-3.3-70B Q4_K_M 白盒部署与显存建模；② llama.cpp 量化与 KV cache 底层改动/分析；③ vLLM PagedAttention 对照实验。
面试常问（提前备好）：Q4_K_M 为什么比 Q4_0 好？GQA 怎么省 KV？FlashAttn 省的是算力还是显存？PagedAttention 解决什么？AWQ 和 K-quant 路线差异？70B Q4 在 48GB 卡上下文能开多大？
公开输出：把阶段 1/2/3 的笔记发成系列文章（知乎/掘金/个人站），面试官会搜到，信任度直接不同