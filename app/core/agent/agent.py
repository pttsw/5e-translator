
import os
import platform
import logging
from threading import Lock
from collections.abc import Iterator
from typing import Any, Union

import httpx
import tiktoken
from pydantic import SecretStr
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, message_chunk_to_message
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from app.core.agent.tools import ToolRegistry
from app.core.agent.prompts import CHAT_SYSTEM

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DNDTranslatorAgent:
    MAX_TOOL_ROUNDS = 8

    def __init__(self, translation_options=None):
        self.translation_options = translation_options
        self.translation_graph = None
        self._translation_graph_lock = Lock()
        self.agent = None
        self.tool_registry = ToolRegistry()
        self.messages = [SystemMessage(content=CHAT_SYSTEM)]
        self.last_token_usage = self._empty_token_usage()
        
        logger.info(f"初始化于{platform.system()}")
        
        base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        model = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2")
        api_key = SecretStr(os.getenv("SILICONFLOW_API_KEY") or os.getenv("DS_KEY",""))
        trust_env = os.getenv("SILICONFLOW_TRUST_ENV", "0").lower() in (
            "1", "true", "yes", "on"
        )
        request_timeout = float(os.getenv("SILICONFLOW_TIMEOUT", "90"))
        connect_timeout = float(os.getenv("SILICONFLOW_CONNECT_TIMEOUT", "15"))
        max_retries = int(os.getenv("SILICONFLOW_MAX_RETRIES", "3"))
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(request_timeout, connect=connect_timeout),
            trust_env=trust_env,
        )
        try:
            self.token_encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.token_encoder = None
        self.agent = ChatOpenAI(
            temperature=0,
            base_url=base_url,
            api_key=api_key,
            model=model,
            http_client=self.http_client,
            max_retries=max_retries,
            stream_usage=True,
            # response_format={"type": "json_object"},
        )
        logger.info(
            "LLM 连接配置: base_url=%s, model=%s, trust_env=%s, "
            "timeout=%ss, connect_timeout=%ss, max_retries=%s",
            base_url,
            model,
            trust_env,
            request_timeout,
            connect_timeout,
            max_retries,
        )
    def chat(
        self,
        message: str,
        use_tools: bool = True,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """
        Send a message to the agent
        
        Args:
            message: User message
            use_tools: Whether to enable tool calling
            stream: Whether to stream the response
            **kwargs: Additional backend-specific parameters
            
        Returns:
            Agent response (or generator if streaming)
        """
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if message.lstrip().startswith('/'):
            response = self.execute_command(message)
            return iter([response]) if stream else response
        if not self.agent:
            raise RuntimeError("Agent not initialized")

        self.last_token_usage = self._empty_token_usage()
        registry = self._get_tool_registry()
        runnable = self.agent
        if use_tools:
            runnable = self.agent.bind_tools(registry.get_tools_schemas())

        messages = [*self._get_message_history(), HumanMessage(content=message)]
        if stream:
            return self._stream_chat(runnable, messages, registry, use_tools, kwargs)

        for _ in range(self.MAX_TOOL_ROUNDS):
            response = runnable.invoke(messages, **kwargs)
            self._record_token_usage(response)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not use_tools or not tool_calls:
                messages.append(response)
                self.messages = messages
                return self._content_to_text(response.content)

            messages.append(response)
            messages.extend(self._execute_tool_calls(tool_calls, registry))

        raise RuntimeError(
            f"Agent exceeded the maximum of {self.MAX_TOOL_ROUNDS} tool-call rounds"
        )

    def _stream_chat(
        self,
        runnable,
        messages: list,
        registry: ToolRegistry,
        use_tools: bool,
        kwargs: dict,
    ) -> Iterator[str]:
        """Stream model text while still completing any requested tool calls."""
        for _ in range(self.MAX_TOOL_ROUNDS):
            response = None
            for chunk in runnable.stream(messages, **kwargs):
                response = chunk if response is None else response + chunk
                text = self._content_to_text(chunk.content)
                if text:
                    yield text

            if response is None:
                return

            response_message = message_chunk_to_message(response)
            self._record_token_usage(response_message)
            tool_calls = getattr(response_message, "tool_calls", None) or []
            if not use_tools or not tool_calls:
                messages.append(response_message)
                self.messages = messages
                return

            messages.append(response_message)
            messages.extend(self._execute_tool_calls(tool_calls, registry))

        raise RuntimeError(
            f"Agent exceeded the maximum of {self.MAX_TOOL_ROUNDS} tool-call rounds"
        )

    def _get_tool_registry(self) -> ToolRegistry:
        # Keep chat usable for instances restored or created without __init__ in tests.
        registry = getattr(self, "tool_registry", None)
        if registry is None:
            registry = ToolRegistry()
            self.tool_registry = registry
        return registry

    def _get_message_history(self) -> list:
        history = getattr(self, "messages", None)
        if history is None:
            history = []
            self.messages = history
        return history

    def clear_history(self) -> None:
        """Clear all messages from the current conversation."""
        self.messages = [SystemMessage(content=CHAT_SYSTEM)]
        self.last_token_usage = self._empty_token_usage()

    def execute_command(self, message, *, on_progress=None):
        from .commands import CommandError, parse_command
        from .graph import build_graph
        import json
        self.last_token_usage = self._empty_token_usage()
        try:
            command = parse_command(message)
            if command.name == '/clear':
                self.clear_history()
                return '已清空对话；文件运行记录与会话参考资料保留。'
            if getattr(self, 'translation_graph', None) is None:
                lock = getattr(self, '_translation_graph_lock', None)
                if lock is None:
                    lock = self._translation_graph_lock = Lock()
                with lock:
                    if getattr(self, 'translation_graph', None) is None:
                        self.translation_graph = build_graph(getattr(self, 'translation_options', None),
                                                             references=self._get_tool_registry().references)
            state = self.translation_graph.invoke(message, on_progress=on_progress)
            return json.dumps(state, ensure_ascii=False, indent=2)
        except (CommandError, OSError) as exc:
            return f'命令失败：{exc}'

    @staticmethod
    def _empty_token_usage() -> dict[str, Union[int, bool]]:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_available": False,
            "cache_creation_available": False,
        }

    @staticmethod
    def _first_token_value(*values) -> Union[int, None]:
        for value in values:
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return None

    def _record_token_usage(self, response) -> None:
        usage = getattr(response, "usage_metadata", None) or {}
        response_metadata = getattr(response, "response_metadata", None) or {}
        raw_usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        if not isinstance(raw_usage, dict):
            raw_usage = {}

        input_details = usage.get("input_token_details") or {}
        raw_input_details = raw_usage.get("prompt_tokens_details") or {}
        if not isinstance(input_details, dict):
            input_details = {}
        if not isinstance(raw_input_details, dict):
            raw_input_details = {}

        input_tokens = self._first_token_value(
            usage.get("input_tokens"), raw_usage.get("prompt_tokens")
        ) or 0
        output_tokens = self._first_token_value(
            usage.get("output_tokens"), raw_usage.get("completion_tokens")
        ) or 0
        total_tokens = self._first_token_value(
            usage.get("total_tokens"), raw_usage.get("total_tokens")
        )
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens

        cache_read = self._first_token_value(
            input_details.get("cache_read"),
            raw_input_details.get("cached_tokens"),
            raw_usage.get("prompt_cache_hit_tokens"),
            raw_usage.get("cache_read_input_tokens"),
        )
        cache_creation = self._first_token_value(
            input_details.get("cache_creation"),
            raw_usage.get("prompt_cache_miss_tokens"),
            raw_usage.get("cache_creation_input_tokens"),
        )

        current = getattr(self, "last_token_usage", None)
        if not isinstance(current, dict):
            current = self._empty_token_usage()
            self.last_token_usage = current
        current["input_tokens"] += input_tokens
        current["output_tokens"] += output_tokens
        current["total_tokens"] += total_tokens
        if cache_read is not None:
            current["cache_read_tokens"] += cache_read
            current["cache_read_available"] = True
        if cache_creation is not None:
            current["cache_creation_tokens"] += cache_creation
            current["cache_creation_available"] = True

    def format_last_token_usage(self) -> str:
        usage = getattr(self, "last_token_usage", None) or self._empty_token_usage()
        cache_read = (
            str(usage["cache_read_tokens"])
            if usage["cache_read_available"]
            else "未提供"
        )
        cache_creation = (
            str(usage["cache_creation_tokens"])
            if usage["cache_creation_available"]
            else "未提供"
        )
        return (
            f"📊 Token：输入={usage['input_tokens']} | 输出={usage['output_tokens']} | "
            f"缓存命中={cache_read} | 缓存未命中/写入={cache_creation} | "
            f"总计={usage['total_tokens']}"
        )

    def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]], registry: ToolRegistry
    ) -> list[ToolMessage]:
        results = []
        for tool_call in tool_calls:
            name = tool_call.get("name", "")
            call_id = tool_call.get("id") or name or "unknown-tool-call"
            arguments = tool_call.get("args", {})
            content = self.tool_registry.execute_tool(name, arguments)
            logger.info(f"工具{name}：{content}")
            results.append(ToolMessage(content=content, tool_call_id=call_id))
        return results

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "".join(parts)
        if content is None:
            return ""
        return str(content)
    
    def interactive_mode(self, stream:bool = True):
        """Run interactive chat mode with optional streaming"""
        print("\n" + "="*60)
        print("💬 交互模式" + (" (STREAMING)" if stream else ""))
        print("="*60)
        print("\n现在你可以与ai助手进行对话:")

        registry = ToolRegistry()
        tools = registry.get_tools_schemas()
        
        print("\n📦 可用工具:")
        for i, tool in enumerate(tools, 1):
            func = tool["function"]
            print(f"  {i}. {func['name']}: {func['description']}")
        
        while True:
            try:
                user_input = input("\n👤 你: ").strip()
                
                if not user_input:
                    continue
                
                # Handle commands
                if user_input.lower() == "quit":
                    print("👋 拜拜！")
                    break
                elif user_input.startswith('/'):
                    print(self.execute_command(user_input, on_progress=self._print_file_progress))
                    continue
                        
                    
                print("\n⏳ 处理中...")
                response = self.chat(user_input, stream=False)
                
                print(f"🤖 助手: {response}")
                print(self.format_last_token_usage())
                
            except KeyboardInterrupt:
                print("\n\n👋 拜拜！")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                logger.exception("Error in interactive mode")

    @staticmethod
    def _print_file_progress(state):
        if state['stage'] == 'analyse_and_reuse':
            item = state['file_results'][-1]
            print(f"文件 {state['progress']['completed'] + 1}/{state['progress']['files']}："
                  f"{state['current_file']}；单元={item.get('total', 0)}，"
                  f"复用={item.get('reused', 0)}，待译={item.get('pending', 0)}，冲突=尚未检测")
            
if __name__ == '__main__':
    agent = DNDTranslatorAgent()
    agent.interactive_mode()
