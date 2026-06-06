import json
from unittest.mock import Mock

import pytest
from openai import LengthFinishReasonError, PermissionDeniedError

from app.core.translator.siliconflow_adapter import (
    SiliconFlowAdapter,
    StructuredTranslateResponse,
    TranslatorStatus,
    resolve_llm_api_key,
)


@pytest.fixture
def adapter():
    adapter = SiliconFlowAdapter(api_key="test-key")
    adapter._SiliconFlowAdapter__is_accessable = Mock(return_value=True)
    return adapter


def test_send_text_when_not_accessable(adapter):
    adapter._SiliconFlowAdapter__is_accessable.return_value = False
    result, status = adapter.sendText("test text")
    assert result is None
    assert status == TranslatorStatus.WAITING


def test_extract_source_text_prefers_trans_str(adapter):
    payload = {"trans_str": "druid", "en_str": "ignored"}
    text = json.dumps(payload, ensure_ascii=False)

    source_text = adapter._SiliconFlowAdapter__extract_source_text(text)

    assert source_text == "druid"


def test_extract_source_text_falls_back_to_en_str(adapter):
    payload = {"en_str": "The target gains darkvision."}
    text = json.dumps(payload, ensure_ascii=False)

    source_text = adapter._SiliconFlowAdapter__extract_source_text(text)

    assert source_text == "The target gains darkvision."


def test_adaptive_max_tokens_grows_with_source_length(adapter):
    short_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens("druid")
    long_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens("druid " * 300)

    assert short_budget >= adapter.min_tokens
    assert long_budget > short_budget
    assert long_budget <= adapter.max_tokens_cap


def test_resolve_llm_api_key_prefers_mimo_key_for_mimo_provider(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "mimo-secret")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-secret")

    provider, api_key = resolve_llm_api_key(
        explicit_api_key="legacy-ds-key",
        base_url="https://token-plan-cn.xiaomimimo.com/v1",
        model="mimo-v2.5-pro",
    )

    assert provider == "mimo"
    assert api_key == "mimo-secret"


def test_resolve_llm_api_key_prefers_siliconflow_key_for_siliconflow_provider(monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-secret")

    provider, api_key = resolve_llm_api_key(
        explicit_api_key="legacy-ds-key",
        base_url="https://api.siliconflow.cn/v1",
        model="deepseek-ai/DeepSeek-V3.2",
    )

    assert provider == "siliconflow"
    assert api_key == "sf-secret"


def test_send_text_prefers_structured_output(adapter):
    payload = {"trans_str": "The target gains darkvision until the end of the turn."}
    text = json.dumps(payload, ensure_ascii=False)
    expected_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens(payload["trans_str"])

    structured_runner = Mock()
    structured_runner.invoke.return_value = {
        "parsed": {"trans_str": "目标获得黑暗视觉直到回合结束。", "add_terms": {}},
        "parsing_error": None,
        "raw": Mock(),
    }
    adapter.llm = Mock()
    adapter.llm.with_structured_output.return_value = structured_runner

    result, status = adapter.sendText(text, "prompt")

    adapter.llm.with_structured_output.assert_called_once_with(
        StructuredTranslateResponse,
        method="json_schema",
        strict=True,
        include_raw=True,
        max_tokens=expected_budget,
    )
    adapter.llm.bind.assert_not_called()
    assert status == TranslatorStatus.SUCCESS
    assert result == '{"trans_str": "目标获得黑暗视觉直到回合结束。", "add_terms": {}}'


def test_send_text_falls_back_to_json_object_when_structured_output_is_unsupported(adapter):
    payload = {"trans_str": "The target gains darkvision until the end of the turn."}
    text = json.dumps(payload, ensure_ascii=False)
    expected_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens(payload["trans_str"])

    structured_runner = Mock()
    structured_runner.invoke.side_effect = ValueError("json_schema is not supported by this model")
    json_runner = Mock()
    json_runner.invoke.return_value = Mock(content='{"trans_str":"目标获得黑暗视觉直到回合结束。","add_terms":{}}')

    adapter.llm = Mock()
    adapter.llm.with_structured_output.return_value = structured_runner
    adapter.llm.bind.return_value = json_runner

    result, status = adapter.sendText(text, "prompt")

    adapter.llm.bind.assert_called_once_with(max_tokens=expected_budget)
    assert adapter.structured_output_supported is False
    assert status == TranslatorStatus.SUCCESS
    assert result == '{"trans_str": "目标获得黑暗视觉直到回合结束。", "add_terms": {}}'


def test_send_text_retries_once_after_local_validation_failure(adapter):
    adapter.structured_output_supported = False
    payload = {"trans_str": "spell"}
    text = json.dumps(payload, ensure_ascii=False)
    expected_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens(payload["trans_str"])

    first_runner = Mock()
    first_runner.invoke.return_value = Mock(content='{"trans_str":"法术","add_terms":"oops"}')
    second_runner = Mock()
    second_runner.invoke.return_value = Mock(content='{"trans_str":"法术","add_terms":{}}')

    adapter.llm = Mock()
    adapter.llm.bind.side_effect = [first_runner, second_runner]

    result, status = adapter.sendText(text, "prompt")

    assert adapter.llm.bind.call_args_list[0].kwargs == {"max_tokens": expected_budget}
    assert adapter.llm.bind.call_args_list[1].kwargs == {"max_tokens": expected_budget}
    first_invoke_messages = first_runner.invoke.call_args[0][0]
    second_invoke_messages = second_runner.invoke.call_args[0][0]
    assert first_invoke_messages[0].content == "prompt"
    assert "只返回一个合法 JSON 对象" in second_invoke_messages[0].content
    assert status == TranslatorStatus.SUCCESS
    assert result == '{"trans_str": "法术", "add_terms": {}}'


def test_send_text_accepts_valid_batch_json_in_batch_mode(adapter):
    adapter.structured_output_supported = False
    payload = {"items": [{"uid": "$.spell[0].entries[0]", "en_str": '"False destination" is a place.'}]}
    text = json.dumps(payload, ensure_ascii=False)
    valid_batch_response = json.dumps(
        {
            "batch_id": "spell/test.json",
            "source_hash": "abc123",
            "items": [
                {
                    "uid": "$.spell[0].entries[0]",
                    "trans_str": "“查无此地”指的是一个地方。",
                }
            ],
            "add_terms": {},
        },
        ensure_ascii=False,
    )

    adapter._SiliconFlowAdapter__invoke_once = Mock(return_value=(valid_batch_response, True))

    result, status = adapter.sendText(text, "prompt", structured_output=False, response_mode="batch")

    assert status == TranslatorStatus.SUCCESS
    assert json.loads(result) == json.loads(valid_batch_response)


def test_send_text_rejects_single_schema_for_batch_mode(adapter):
    adapter.structured_output_supported = False
    payload = {"items": [{"uid": "$.spell[0].entries[0]", "en_str": "spell"}]}
    text = json.dumps(payload, ensure_ascii=False)
    invalid_batch_response = '{"trans_str":"法术","add_terms":{}}'

    adapter._SiliconFlowAdapter__invoke_once = Mock(return_value=(invalid_batch_response, True))

    result, status = adapter.sendText(text, "prompt", structured_output=False, response_mode="batch")

    assert result is None
    assert status == TranslatorStatus.FAILURE


def test_post_retries_with_larger_max_tokens_after_length_error(adapter):
    adapter.structured_output_supported = False
    payload = {"trans_str": "spell " * 400}
    text = json.dumps(payload, ensure_ascii=False)
    first_budget = adapter._SiliconFlowAdapter__get_adaptive_max_tokens(payload["trans_str"])
    second_budget = min(adapter.max_tokens_cap, max(first_budget * 2, first_budget + adapter.min_tokens))

    first_runner = Mock()
    first_runner.invoke.side_effect = LengthFinishReasonError(completion=Mock())
    second_runner = Mock()
    second_runner.invoke.return_value = Mock(content='{"trans_str":"法术","add_terms":{}}')

    adapter.llm = Mock()
    adapter.llm.bind.side_effect = [first_runner, second_runner]

    result, status = adapter.sendText(text, "prompt")

    assert adapter.llm.bind.call_args_list[0].kwargs == {"max_tokens": first_budget}
    assert adapter.llm.bind.call_args_list[1].kwargs == {"max_tokens": second_budget}
    assert status == TranslatorStatus.SUCCESS
    assert result == '{"trans_str": "法术", "add_terms": {}}'


def test_send_text_returns_failure_for_nonrecoverable_exception(adapter):
    adapter.structured_output_supported = False
    payload = {"trans_str": "spell"}
    text = json.dumps(payload, ensure_ascii=False)

    failing_runner = Mock()
    failing_runner.invoke.side_effect = ValueError("model returned an unexpected payload")

    adapter.llm = Mock()
    adapter.llm.bind.return_value = failing_runner

    result, status = adapter.sendText(text, "prompt")

    assert result is None
    assert status == TranslatorStatus.FAILURE


def test_send_text_returns_fatal_for_permission_denied(adapter):
    adapter.structured_output_supported = False
    payload = {"trans_str": "spell"}
    text = json.dumps(payload, ensure_ascii=False)

    denied_runner = Mock()
    denied_runner.invoke.side_effect = PermissionDeniedError(
        message="balance insufficient",
        response=Mock(),
        body={"code": 30001, "message": "Sorry, your account balance is insufficient"},
    )

    adapter.llm = Mock()
    adapter.llm.bind.return_value = denied_runner

    result, status = adapter.sendText(text, "prompt")

    assert result is None
    assert status == TranslatorStatus.FATAL
