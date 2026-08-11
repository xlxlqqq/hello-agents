"""
DocGuard Agent - LLM 客户端（OpenAI 兼容封装）
================================================

设计要点：
1. 基于 openai.AsyncOpenAI，支持所有 OpenAI 兼容服务（OpenAI/百炼/DeepSeek/Ollama 等）
2. 不绑定具体模型，模型名由配置注入
3. 支持三种调用模式：
   - chat(): 普通聊天，返回字符串
   - chat_with_json(): 返回 JSON 对象（response_format=json_object）
   - chat_with_structured_output(): 返回 Pydantic 模型实例
4. 内置重试（429 限流指数退避）、超时、异常封装
5. 提供 token 用量统计，便于成本追踪
"""

import asyncio
import json
import logging
from typing import Any, Optional, Type, TypeVar

import openai
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ValidationError

from core.config import LLMConfig
from core.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMResponseParseError,
    LLMTimeoutError,
)
from core.logging_config import get_logger

T = TypeVar("T", bound=BaseModel)

logger = get_logger("core.llm_client")


# ============================================================
# 数据模型
# ============================================================
class LLMMessage(BaseModel):
    """LLM 消息"""

    role: str  # "system" / "user" / "assistant"
    content: str


class LLMUsage(BaseModel):
    """Token 用量统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """LLM 调用结果"""

    content: str
    model: str
    usage: LLMUsage
    finish_reason: str
    raw: Optional[dict] = None  # 原始响应（调试用）


# ============================================================
# LLM 客户端
# ============================================================
class LLMClient:
    """
    OpenAI 兼容协议 LLM 客户端。

    通过依赖注入 LLMConfig 实例化，不绑定具体模型。
    所有方法均为 async，适配 FastAPI 异步栈。
    """

    def __init__(
        self,
        config: LLMConfig,
        client: Optional[AsyncOpenAI] = None,
    ) -> None:
        """
        Args:
            config: LLM 配置
            client: 可选的 AsyncOpenAI 实例（用于测试 mock）
        """
        if not config.api_key:
            raise LLMError("LLM api_key 未配置，无法初始化 LLMClient")

        self.config = config
        self._client = client or AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        self._total_usage = LLMUsage()

    # ============================================================
    # 公共方法
    # ============================================================
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """
        普通聊天调用，返回 LLMResponse。

        Args:
            messages: 消息列表
            temperature: 覆盖默认温度，None 用配置默认
            max_tokens: 覆盖默认最大 tokens
            model: 覆盖默认模型

        Returns:
            LLMResponse 实例

        Raises:
            LLMError: 调用失败
        """
        return await self._chat_with_retry(
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
            model=model or self.config.chat_model,
            response_format=None,
        )

    async def chat_with_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> dict:
        """
        调用 LLM 并要求返回 JSON 对象。

        会自动在 messages 中追加"请以 JSON 格式输出"的提示，
        并设置 response_format={"type": "json_object"}。

        Args:
            messages: 消息列表
            temperature: 覆盖默认温度
            model: 覆盖默认模型

        Returns:
            解析后的 dict

        Raises:
            LLMResponseParseError: JSON 解析失败
            LLMError: 调用失败
        """
        # 确保 system 消息中有 JSON 输出指令
        msgs = self._ensure_json_instruction(messages)

        response = await self._chat_with_retry(
            messages=msgs,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=self.config.max_tokens,
            model=model or self.config.chat_model,
            response_format={"type": "json_object"},
        )

        try:
            return json.loads(response.content)
        except json.JSONDecodeError as e:
            raise LLMResponseParseError(
                f"LLM 返回内容无法解析为 JSON: {e}",
                context={"raw_content": response.content[:500]},
                cause=e,
            ) from e

    async def chat_with_structured_output(
        self,
        messages: list[LLMMessage],
        output_schema: Type[T],
        *,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> T:
        """
        调用 LLM 并返回 Pydantic 结构化对象。

        将 output_schema 的 JSON Schema 注入 system 消息，
        要求 LLM 输出符合 schema 的 JSON，再解析为 Pydantic 实例。

        Args:
            messages: 消息列表
            output_schema: 目标 Pydantic 模型类
            temperature: 覆盖默认温度
            model: 覆盖默认模型

        Returns:
            output_schema 的实例

        Raises:
            LLMResponseParseError: 解析或校验失败
            LLMError: 调用失败
        """
        schema_json = output_schema.model_json_schema()
        schema_instruction = (
            "请严格按照以下 JSON Schema 输出 JSON，不要输出任何额外文本：\n"
            f"{json.dumps(schema_json, ensure_ascii=False, indent=2)}"
        )

        msgs = self._inject_schema_instruction(messages, schema_instruction)

        result_dict = await self.chat_with_json(
            msgs,
            temperature=temperature,
            model=model,
        )

        try:
            return output_schema.model_validate(result_dict)
        except ValidationError as e:
            raise LLMResponseParseError(
                f"LLM 输出不符合 {output_schema.__name__} schema: {e}",
                context={"raw_dict": str(result_dict)[:500]},
                cause=e,
            ) from e

    # ============================================================
    # 用量统计
    # ============================================================
    @property
    def total_usage(self) -> LLMUsage:
        """累计 token 用量"""
        return self._total_usage

    def reset_usage(self) -> None:
        """重置累计用量"""
        self._total_usage = LLMUsage()

    # ============================================================
    # 内部实现
    # ============================================================
    async def _chat_with_retry(
        self,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int,
        model: str,
        response_format: Optional[dict],
    ) -> LLMResponse:
        """带重试的 LLM 调用（429 指数退避，最多 3 次）"""
        max_retries = 3
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [m.model_dump() for m in messages],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if response_format is not None:
                    kwargs["response_format"] = response_format

                response = await self._client.chat.completions.create(**kwargs)

                content = response.choices[0].message.content or ""
                usage = LLMUsage(
                    prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                    completion_tokens=response.usage.completion_tokens if response.usage else 0,
                    total_tokens=response.usage.total_tokens if response.usage else 0,
                )

                # 累计用量
                self._total_usage.prompt_tokens += usage.prompt_tokens
                self._total_usage.completion_tokens += usage.completion_tokens
                self._total_usage.total_tokens += usage.total_tokens

                return LLMResponse(
                    content=content,
                    model=response.model or model,
                    usage=usage,
                    finish_reason=response.choices[0].finish_reason or "stop",
                )

            except RateLimitError as e:
                last_exception = e
                wait = min(2 ** attempt, 16)
                logger.warning(
                    "LLM 触发限流 (429)，%ds 后重试 (attempt=%d/%d)",
                    wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue

            except APITimeoutError as e:
                last_exception = e
                raise LLMTimeoutError(
                    f"LLM 调用超时 (timeout={self.config.timeout}s)",
                    cause=e,
                ) from e

            except APIConnectionError as e:
                last_exception = e
                raise LLMError(
                    f"LLM 连接失败: {e}",
                    context={"base_url": self.config.base_url},
                    cause=e,
                ) from e

            except Exception as e:
                last_exception = e
                raise LLMError(
                    f"LLM 调用失败: {e}",
                    cause=e,
                ) from e

        # 重试耗尽
        raise LLMRateLimitError(
            f"LLM 调用在 {max_retries} 次重试后仍触发限流",
            cause=last_exception,
        )

    @staticmethod
    def _ensure_json_instruction(messages: list[LLMMessage]) -> list[LLMMessage]:
        """确保消息列表包含 JSON 输出指令"""
        json_hint = "请以合法的 JSON 格式输出，不要包含 markdown 代码块标记。"
        result = list(messages)
        # 如果已有 system 消息，追加指令；否则新建
        for msg in result:
            if msg.role == "system":
                if "JSON" not in msg.content and "json" not in msg.content:
                    msg.content = msg.content + "\n\n" + json_hint
                return result
        result.insert(0, LLMMessage(role="system", content=json_hint))
        return result

    @staticmethod
    def _inject_schema_instruction(
        messages: list[LLMMessage],
        schema_instruction: str,
    ) -> list[LLMMessage]:
        """将 schema 指令注入到 system 消息中"""
        result = [LLMMessage(role=m.role, content=m.content) for m in messages]
        for msg in result:
            if msg.role == "system":
                msg.content = msg.content + "\n\n" + schema_instruction
                return result
        result.insert(0, LLMMessage(role="system", content=schema_instruction))
        return result


# ============================================================
# 工厂函数
# ============================================================
def create_llm_client(config: Optional[LLMConfig] = None) -> LLMClient:
    """
    创建 LLM 客户端实例。

    Args:
        config: LLM 配置，None 时从全局配置获取

    Returns:
        LLMClient 实例
    """
    if config is None:
        from core.config import get_config
        config = get_config().llm
    return LLMClient(config=config)
