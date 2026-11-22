"""Unit tests for OpenAI-compatible ReasoningChatOpenAI and ModelFactory."""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from backend.app.domains.agent.model_adapter import ModelFactory, ReasoningChatOpenAI
from backend.app.shared.llm_credentials import LLMCredentials


def test_model_factory_builds_reasoning_chat_openai_with_no_sdk_retries_and_no_env_proxy() -> None:
    creds = LLMCredentials(
        base_url="http://localhost:8001/v1",
        model="qwen3.8-flash",
        api_key="sk-local-test-key",
        enable_thinking=True,
    )
    model = ModelFactory().build(creds)
    assert isinstance(model, ReasoningChatOpenAI)
    assert model.max_retries == 0
    assert model.model_name == "qwen3.8-flash"
    assert model.extra_body == {"enable_thinking": True}
    assert model.http_client is not None
    assert model.http_async_client is not None
    assert model.http_client.trust_env is False
    assert model.http_async_client.trust_env is False


def test_model_factory_omits_enable_thinking_when_disabled() -> None:
    creds = LLMCredentials(
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        api_key="sk-test-key-123",
        enable_thinking=False,
    )
    model = ModelFactory().build(creds)
    assert isinstance(model, ReasoningChatOpenAI)
    assert not model.extra_body


def test_reasoning_chat_openai_preserves_streaming_and_non_streaming_reasoning_content() -> None:
    creds = LLMCredentials(
        base_url="http://localhost:8001/v1",
        model="qwen3.8-flash",
        api_key="sk-local-test-key",
        enable_thinking=True,
    )
    model = ModelFactory().build(creds)
    assert isinstance(model, ReasoningChatOpenAI)

    chunk = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Step 1: inspect",
                },
                "finish_reason": None,
            }
        ],
    }
    gen_chunk = model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen_chunk is not None
    assert gen_chunk.message.additional_kwargs.get("reasoning_content") == "Step 1: inspect"

    response = {
        "id": "chatcmpl-2",
        "model": "qwen3.8-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Final answer.",
                    "reasoning_content": "Step 2: verify",
                },
                "finish_reason": "stop",
            }
        ],
    }
    result = model._create_chat_result(response)
    reasoning = result.generations[0].message.additional_kwargs.get("reasoning_content")
    assert reasoning == "Step 2: verify"
