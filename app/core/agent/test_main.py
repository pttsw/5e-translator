from unittest.mock import Mock

from langchain_core.messages import AIMessage, AIMessageChunk

from app.core.agent import agent as agent_main


def _build_agent(monkeypatch):
    http_client = Mock()
    chat_model = Mock()
    monkeypatch.setattr(agent_main.httpx, "Client", Mock(return_value=http_client))
    monkeypatch.setattr(agent_main, "ChatOpenAI", Mock(return_value=chat_model))
    monkeypatch.setattr(agent_main.tiktoken, "get_encoding", Mock(return_value=Mock()))
    return agent_main.DNDTranslatorAgent(), http_client, chat_model


def test_agent_ignores_environment_proxy_by_default(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_TRUST_ENV", raising=False)
    monkeypatch.delenv("SILICONFLOW_TIMEOUT", raising=False)
    monkeypatch.delenv("SILICONFLOW_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("SILICONFLOW_MAX_RETRIES", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")

    agent, http_client, chat_model = _build_agent(monkeypatch)

    client_kwargs = agent_main.httpx.Client.call_args.kwargs
    timeout = client_kwargs["timeout"]
    assert client_kwargs["trust_env"] is False
    assert timeout.read == 90.0
    assert timeout.connect == 15.0
    assert agent.http_client is http_client

    model_kwargs = agent_main.ChatOpenAI.call_args.kwargs
    assert model_kwargs["http_client"] is http_client
    assert model_kwargs["max_retries"] == 3
    assert model_kwargs["stream_usage"] is True
    assert model_kwargs["api_key"].get_secret_value() == "test-key"
    assert agent.agent is chat_model


def test_agent_connection_settings_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_TRUST_ENV", "true")
    monkeypatch.setenv("SILICONFLOW_TIMEOUT", "45")
    monkeypatch.setenv("SILICONFLOW_CONNECT_TIMEOUT", "8")
    monkeypatch.setenv("SILICONFLOW_MAX_RETRIES", "5")

    _build_agent(monkeypatch)

    client_kwargs = agent_main.httpx.Client.call_args.kwargs
    timeout = client_kwargs["timeout"]
    assert client_kwargs["trust_env"] is True
    assert timeout.read == 45.0
    assert timeout.connect == 8.0
    assert agent_main.ChatOpenAI.call_args.kwargs["max_retries"] == 5


def test_chat_records_standard_token_usage(monkeypatch):
    agent, _, chat_model = _build_agent(monkeypatch)
    chat_model.invoke.return_value = AIMessage(
        content="译文",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_token_details": {"cache_read": 80, "cache_creation": 20},
        },
    )

    response = agent.chat("source", use_tools=False)

    assert response == "译文"
    assert agent.last_token_usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 20,
        "cache_read_available": True,
        "cache_creation_available": True,
    }
    assert agent.format_last_token_usage() == (
        "📊 Token：输入=120 | 输出=30 | 缓存命中=80 | "
        "缓存未命中/写入=20 | 总计=150"
    )


def test_token_usage_accumulates_tool_rounds_and_provider_cache_fields(monkeypatch):
    agent, _, _ = _build_agent(monkeypatch)
    first = AIMessage(
        content="",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_cache_hit_tokens": 60,
                "prompt_cache_miss_tokens": 40,
            }
        },
    )
    second = AIMessage(
        content="完成",
        usage_metadata={
            "input_tokens": 140,
            "output_tokens": 20,
            "total_tokens": 160,
        },
    )

    agent.last_token_usage = agent._empty_token_usage()
    agent._record_token_usage(first)
    agent._record_token_usage(second)

    assert agent.last_token_usage == {
        "input_tokens": 240,
        "output_tokens": 30,
        "total_tokens": 270,
        "cache_read_tokens": 60,
        "cache_creation_tokens": 40,
        "cache_read_available": True,
        "cache_creation_available": True,
    }


def test_token_usage_marks_unreported_cache_details(monkeypatch):
    agent, _, _ = _build_agent(monkeypatch)
    agent.last_token_usage = {
        **agent._empty_token_usage(),
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }

    assert agent.format_last_token_usage() == (
        "📊 Token：输入=12 | 输出=3 | 缓存命中=未提供 | "
        "缓存未命中/写入=未提供 | 总计=15"
    )


def test_streaming_chat_records_usage_from_final_chunk(monkeypatch):
    agent, _, chat_model = _build_agent(monkeypatch)
    chat_model.stream.return_value = iter(
        [
            AIMessageChunk(content="译"),
            AIMessageChunk(
                content="文",
                usage_metadata={
                    "input_tokens": 50,
                    "output_tokens": 2,
                    "total_tokens": 52,
                    "input_token_details": {"cache_read": 30},
                },
            ),
        ]
    )

    chunks = list(agent.chat("source", use_tools=False, stream=True))

    assert chunks == ["译", "文"]
    assert agent.last_token_usage["input_tokens"] == 50
    assert agent.last_token_usage["output_tokens"] == 2
    assert agent.last_token_usage["total_tokens"] == 52
    assert agent.last_token_usage["cache_read_tokens"] == 30
