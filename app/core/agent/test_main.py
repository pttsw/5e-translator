from unittest.mock import Mock

from app.core.agent import main as agent_main


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
    assert model_kwargs["openai_api_key"] == "test-key"
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
