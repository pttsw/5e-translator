import json
import os
import time
from typing import Optional

import httpx
import requests
import tiktoken
from langchain.schema import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    LengthFinishReasonError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.core.utils import TranslatorStatus, format_llm_msg
from config import *


class StructuredTranslateResponse(BaseModel):
    trans_str: str
    add_terms: dict[str, str] = {}


class StructuredBatchTranslateItem(BaseModel):
    uid: str
    trans_str: str


class StructuredBatchTranslateResponse(BaseModel):
    batch_id: str
    source_hash: str
    items: list[StructuredBatchTranslateItem]
    add_terms: dict[str, str] = {}


def is_mimo_provider(base_url: Optional[str], model: Optional[str]) -> bool:
    normalized_base_url = (base_url or "").lower()
    normalized_model = (model or "").lower()
    return "xiaomimimo.com" in normalized_base_url or normalized_model.startswith("mimo-")


def resolve_llm_api_key(
    explicit_api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[str, str]:
    normalized_base_url = base_url or os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    normalized_model = model or os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2")

    if is_mimo_provider(normalized_base_url, normalized_model):
        provider = "mimo"
        api_key = (
            os.getenv("MIMO_API_KEY")
            or os.getenv("SILICONFLOW_API_KEY")
            or explicit_api_key
            or ""
        )
    else:
        provider = "siliconflow"
        api_key = (
            os.getenv("SILICONFLOW_API_KEY")
            or explicit_api_key
            or os.getenv("DS_KEY")
            or ""
        )

    return provider, api_key.strip()


class SiliconFlowAdapter:
    def __init__(self, api_key, promot="", knowledge_promot=""):
        self.retry_time = 0
        self.access_time = 0
        self.id = "None"
        self.base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        self.model = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2")
        self.provider, self.api_key = resolve_llm_api_key(
            explicit_api_key=api_key,
            base_url=self.base_url,
            model=self.model,
        )
        self.max_tokens_cap = int(os.getenv("SILICONFLOW_MAX_TOKENS", "8192"))
        self.min_tokens = int(os.getenv("SILICONFLOW_MIN_TOKENS", "256"))
        self.structured_output_supported = os.getenv(
            "SILICONFLOW_STRUCTURED_OUTPUT", "1"
        ).lower() in ("1", "true", "yes", "on")
        self.trust_env = os.getenv("SILICONFLOW_TRUST_ENV", "0").lower() in ("1", "true", "yes", "on")
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=15.0),
            trust_env=self.trust_env,
        )
        try:
            self.token_encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.token_encoder = None
        self.llm = ChatOpenAI(
            temperature=0,
            base_url=self.base_url,
            openai_api_key=self.api_key,
            max_tokens=self.max_tokens_cap,
            model=self.model,
            response_format={"type": "json_object"},
            http_client=self.http_client,
        )
        logger.info(
            f"SiliconFlow 初始化: provider={self.provider}, base_url={self.base_url}, model={self.model}, "
            f"trust_env={self.trust_env}, structured_output_supported={self.structured_output_supported}"
        )

    def sendText(
        self,
        text,
        promot: str = "",
        structured_output: bool = True,
        response_mode: str = "single",
    ):
        if not self.__is_accessable():
            return None, TranslatorStatus.WAITING

        data = [
            SystemMessage(content=promot),
            HumanMessage(content=text),
        ]
        source_text = self.__extract_source_text(text)
        max_tokens = self.__get_adaptive_max_tokens(source_text)
        logger.info(
            f"llm发送数据：source_len={len(source_text)}, "
            f"source_tokens={self.__estimate_text_tokens(source_text)}, "
            f"max_tokens={max_tokens}, payload={text}"
        )
        return self.__send(
            data,
            max_tokens,
            structured_output=structured_output,
            response_mode=response_mode,
        )

    def __post(
        self,
        data,
        max_tokens: int,
        structured_output: bool = True,
        response_mode: str = "single",
    ):
        retry_tokens = min(self.max_tokens_cap, max(max_tokens * 2, max_tokens + self.min_tokens))
        token_budgets = [max_tokens]
        if retry_tokens > max_tokens:
            token_budgets.append(retry_tokens)

        for index, token_budget in enumerate(token_budgets):
            current_data = data
            for validation_retry in range(2):
                try:
                    response_text, need_validation = self.__invoke_once(
                        current_data,
                        token_budget,
                        structured_output,
                        response_mode=response_mode,
                    )
                    if response_text is None:
                        self.__wait(60)
                        return None, TranslatorStatus.WAITING

                    if need_validation:
                        normalized_text = self.__normalize_response_text(
                            response_text, response_mode=response_mode
                        )
                        if normalized_text is not None:
                            logger.info("DeepSeek回答：" + normalized_text)
                            return normalized_text, TranslatorStatus.SUCCESS
                    else:
                        logger.info("DeepSeek回答：" + response_text)
                        return response_text, TranslatorStatus.SUCCESS

                    if validation_retry == 0:
                        logger.warning("SiliconFlow 本地校验失败，追加严格 JSON 提示后重试一次")
                        current_data = self.__add_validation_retry_instruction(
                            data,
                            response_mode=response_mode,
                        )
                        continue

                    logger.warning(f"SiliconFlow 本地校验失败，原始返回：{response_text}")
                    return None, TranslatorStatus.FAILURE
                except LengthFinishReasonError as e:
                    if index == len(token_budgets) - 1:
                        logger.warning(f"SiliconFlow 输出因长度被截断，且已达到本次最大预算: {e}")
                        return None, TranslatorStatus.FAILURE
                    logger.warning(
                        f"SiliconFlow 输出因长度被截断，提升 max_tokens 后重试: "
                        f"{token_budget} -> {token_budgets[index + 1]}"
                    )
                    break
                except RateLimitError as e:
                    logger.warning(f"SiliconFlow 请求限流: {e}")
                    self.__wait(60)
                    return None, TranslatorStatus.WAITING
                except APITimeoutError as e:
                    logger.warning(f"SiliconFlow 请求超时: {e}")
                    self.__wait(30)
                    return None, TranslatorStatus.WAITING
                except (AuthenticationError, PermissionDeniedError) as e:
                    logger.exception(
                        f"SiliconFlow 鉴权或额度错误: provider={self.provider}, "
                        f"base_url={self.base_url}, model={self.model}, "
                        f"key_hint={self.__api_key_hint()}, error={e}"
                    )
                    return None, TranslatorStatus.FATAL
                except APIConnectionError as e:
                    logger.exception(f"SiliconFlow 连接失败: {e}")
                    return None, TranslatorStatus.FAILURE
                except httpx.ConnectError as e:
                    logger.exception(f"HTTP 连接失败: {e}")
                    return None, TranslatorStatus.FAILURE
                except RuntimeError as e:
                    if "cannot schedule new futures after interpreter shutdown" in str(e):
                        logger.warning(f"SiliconFlow 线程池关闭，等待重试: {e}")
                        time.sleep(5)
                        return None, TranslatorStatus.WAITING
                    logger.exception(f"SiliconFlow 运行时异常: {e}")
                    return None, TranslatorStatus.FAILURE
                except Exception as e:
                    logger.exception(f"SiliconFlow 未知异常: {e}")
                    return None, TranslatorStatus.FAILURE
        return None, TranslatorStatus.FAILURE

    def __invoke_once(
        self,
        data,
        token_budget: int,
        structured_output: bool = True,
        response_mode: str = "single",
    ):
        if structured_output and self.structured_output_supported:
            try:
                structured_text = self.__invoke_structured_output(
                    data,
                    token_budget,
                    response_mode=response_mode,
                )
                if structured_text is not None:
                    return structured_text, False
                logger.warning("SiliconFlow 结构化输出未返回可用结果，回退到 json_object")
            except Exception as e:
                if self.__is_structured_output_unsupported_error(e):
                    logger.warning(f"SiliconFlow 不支持 structured output，回退到 json_object: {e}")
                    self.structured_output_supported = False
                elif self.__should_fallback_to_json_object_on_parse_error(e):
                    logger.warning(f"SiliconFlow structured output 解析失败，回退到 json_object: {e}")
                else:
                    raise
        return self.__invoke_json_object(data, token_budget), True

    def __invoke_structured_output(
        self,
        data,
        token_budget: int,
        response_mode: str = "single",
    ):
        # 创建独立的 LLM 实例避免多线程共享状态导致的 RuntimeError
        local_llm = ChatOpenAI(
            temperature=0,
            base_url=self.base_url,
            openai_api_key=self.api_key,
            max_tokens=self.max_tokens_cap,
            model=self.model,
            response_format={"type": "json_object"},
            http_client=httpx.Client(
                timeout=httpx.Timeout(90.0, connect=15.0),
                trust_env=self.trust_env,
            ),
        )
        schema = (
            StructuredBatchTranslateResponse
            if response_mode == "batch"
            else StructuredTranslateResponse
        )
        structured_llm = local_llm.with_structured_output(
            schema,
            method="json_schema",
            strict=True,
            include_raw=True,
            max_tokens=token_budget,
        )
        result = structured_llm.invoke(data)

        if not isinstance(result, dict):
            return None

        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            logger.warning(f"SiliconFlow 结构化输出解析失败: {parsing_error}")
            return None

        if response_mode == "batch":
            normalized = self.__normalize_batch_response_payload(result.get("parsed"))
        else:
            normalized = self.__normalize_response_payload(result.get("parsed"))
        if normalized is None:
            return None
        return json.dumps(normalized, ensure_ascii=False)

    def __invoke_json_object(self, data, token_budget: int):
        local_llm = ChatOpenAI(
            temperature=0,
            base_url=self.base_url,
            openai_api_key=self.api_key,
            max_tokens=self.max_tokens_cap,
            model=self.model,
            response_format={"type": "json_object"},
            http_client=httpx.Client(
                timeout=httpx.Timeout(90.0, connect=15.0),
                trust_env=self.trust_env,
            ),
        )
        message = local_llm.bind(max_tokens=token_budget).invoke(data)
        return message.content

    def __wait(self, second):
        logger.info(f"已到达使用限制，{second / 60}分钟后重试")
        self.access_time = int(time.time()) + second

    def __is_accessable(self):
        return int(time.time()) > self.access_time

    def __api_key_hint(self) -> str:
        if not self.api_key:
            if self.provider == "mimo":
                return "missing MIMO_API_KEY"
            return "missing SILICONFLOW_API_KEY/DS_KEY"
        masked = self.api_key[:6]
        return f"{masked}***"

    def __check_res(self, message_content: str):
        logger.debug("msg: " + message_content)

        if message_content == "":
            logger.info("返回为空")
            self.remove_conversation()
            return TranslatorStatus.FAILURE
        if "内容由于不合规被停止生成，我们换个话题吧" in message_content:
            logger.info(f"提示：{message_content}")
            self.remove_conversation()
            self.__wait(1200)
            return TranslatorStatus.WAITING

        return TranslatorStatus.SUCCESS

    def __send(
        self,
        data,
        max_tokens: int,
        structured_output: bool = True,
        response_mode: str = "single",
    ):
        message_content, kimi_status = self.__post(
            data,
            max_tokens,
            structured_output=structured_output,
            response_mode=response_mode,
        )
        if kimi_status != TranslatorStatus.SUCCESS:
            return None, kimi_status
        kimi_status = self.__check_res(message_content)
        if kimi_status != TranslatorStatus.SUCCESS:
            return None, kimi_status
        return message_content, TranslatorStatus.SUCCESS

    def __extract_source_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        try:
            payload = json.loads(text)
        except Exception:
            return text
        if not isinstance(payload, dict):
            return text
        for key in ("trans_str", "en_str"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                values = [item for item in value if isinstance(item, str) and item.strip()]
                if values:
                    return "\n".join(values)
        return text

    def __estimate_text_tokens(self, text: str) -> int:
        if not isinstance(text, str) or text == "":
            return 0
        if self.token_encoder is not None:
            try:
                return len(self.token_encoder.encode(text))
            except Exception:
                pass
        return max(1, len(text) // 4)

    def __get_adaptive_max_tokens(self, source_text: str) -> int:
        source_tokens = self.__estimate_text_tokens(source_text)
        estimated_output_tokens = int(source_tokens * 2.5) + 192
        tag_bonus = min(256, source_text.count("{@") * 16)
        budget = estimated_output_tokens + tag_bonus
        budget = max(self.min_tokens, budget)
        budget = min(self.max_tokens_cap, budget)
        budget = ((budget + 127) // 128) * 128
        return min(self.max_tokens_cap, budget)

    def __normalize_response_text(self, message_content: str, response_mode: str = "single"):
        if not isinstance(message_content, str):
            return None
        payload, ok = format_llm_msg(message_content)
        if not ok:
            return None
        if response_mode == "batch":
            normalized = self.__normalize_batch_response_payload(payload)
            if normalized is None:
                return None
            return json.dumps(normalized, ensure_ascii=False)
        normalized = self.__normalize_response_payload(payload)
        if normalized is None:
            return None
        return json.dumps(normalized, ensure_ascii=False)

    def __normalize_batch_response_payload(self, payload):
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        if not isinstance(payload, dict):
            return None

        batch_id = payload.get("batch_id")
        source_hash = payload.get("source_hash")
        items = payload.get("items")
        add_terms = payload.get("add_terms", {})

        if not isinstance(batch_id, str) or not isinstance(source_hash, str):
            return None
        if not isinstance(items, list):
            return None
        if add_terms in ("", None):
            add_terms = {}
        if not isinstance(add_terms, dict):
            return None

        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                return None
            uid = item.get("uid")
            trans_str = item.get("trans_str")
            if not isinstance(uid, str) or not isinstance(trans_str, str):
                return None
            normalized_items.append({"uid": uid, "trans_str": trans_str})

        normalized_terms = {}
        for key, value in add_terms.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return None
            normalized_terms[key] = value

        return {
            "batch_id": batch_id,
            "source_hash": source_hash,
            "items": normalized_items,
            "add_terms": normalized_terms,
        }

    def __normalize_response_payload(self, payload):
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        if not isinstance(payload, dict):
            return None

        trans_str = payload.get("trans_str")
        if isinstance(trans_str, list) and len(trans_str) == 1 and isinstance(trans_str[0], str):
            trans_str = trans_str[0]
        if not isinstance(trans_str, str):
            return None

        add_terms = payload.get("add_terms", {})
        if add_terms in ("", None):
            add_terms = {}
        if not isinstance(add_terms, dict):
            return None

        normalized_terms = {}
        for key, value in add_terms.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return None
            normalized_terms[key] = value

        return {
            "trans_str": trans_str,
            "add_terms": normalized_terms,
        }

    def __add_validation_retry_instruction(self, data, response_mode: str = "single"):
        if response_mode == "batch":
            retry_instruction = (
                "\n额外要求：只返回一个合法 JSON 对象，格式必须为"
                '{"batch_id":"","source_hash":"","items":[{"uid":"","trans_str":""}],"add_terms":{}}。'
                "items 中只能包含输入里已有的 uid，且每个 uid 只能出现一次。"
                "add_terms 必须是对象；没有术语时返回 {}。"
                "禁止输出解释、代码块或任何额外文本。"
            )
        else:
            retry_instruction = (
                "\n额外要求：只返回一个合法 JSON 对象，格式必须为"
                '{"trans_str":"...","add_terms":{}}。'
                "add_terms 必须是对象；没有术语时返回 {}。"
                "禁止输出解释、代码块或任何额外文本。"
            )
        updated_data = list(data)
        if len(updated_data) == 0:
            return updated_data
        first_message = updated_data[0]
        first_content = first_message.content if isinstance(first_message.content, str) else str(first_message.content)
        updated_data[0] = SystemMessage(content=first_content + retry_instruction)
        return updated_data

    def __is_structured_output_unsupported_error(self, error: Exception):
        if isinstance(error, NotImplementedError):
            return True
        if not isinstance(error, (BadRequestError, TypeError, ValueError)):
            return False
        error_msg = str(error).lower()
        patterns = (
            "json_schema",
            "structured output",
            "response_format",
            "strict",
            "unsupported",
            "not support",
            "not supported",
            "schema",
        )
        return any(pattern in error_msg for pattern in patterns)

    def __should_fallback_to_json_object_on_parse_error(self, error: Exception):
        if isinstance(error, ValidationError):
            error_msg = str(error).lower()
            return "json_invalid" in error_msg or "trailing characters" in error_msg
        error_msg = str(error).lower()
        return "trailing characters" in error_msg or "invalid json" in error_msg

    @staticmethod
    def parse_translate_str(message_content: str):
        if not isinstance(message_content, str):
            return None
        try:
            obj = json.loads(message_content)
            if isinstance(obj, dict) and "translate_str" in obj:
                return obj.get("translate_str")
        except Exception:
            return None
        return None

    def remove_conversation(self):
        return
        if self.id != "None":
            response = requests.post(
                REMOVE_URL,
                json={"conversation_id": self.id},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.api_key,
                },
            )
            logger.debug("会话已清除" + response.text)
            self.id = "None"
