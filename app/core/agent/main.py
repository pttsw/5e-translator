
import os
import platform
import logging
from collections.abc import Iterator
from typing import Any, Union

import httpx
import tiktoken
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, message_chunk_to_message
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from app.core.agent.tools import ToolRegistry

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DNDTranslatorAgent:
    MAX_TOOL_ROUNDS = 8

    def __init__(self):
        self.agent = None
        self.tool_registry = ToolRegistry()
        self.messages = [SystemMessage(content="你是D&D（龙与地下城）数据翻译专家。只输出翻译后的文本，不输出其他说明。")]
        
        logger.info(f"初始化于{platform.system()}")
        
        base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        model = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2")
        api_key = os.getenv("SILICONFLOW_API_KEY") or os.getenv("DS_KEY")
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
            openai_api_key=api_key,
            model=model,
            http_client=self.http_client,
            max_retries=max_retries,
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
        if not self.agent:
            raise RuntimeError("Agent not initialized")

        if not isinstance(message, str):
            raise TypeError("message must be a string")

        registry = self._get_tool_registry()
        runnable = self.agent
        if use_tools:
            runnable = self.agent.bind_tools(registry.get_tools_schemas())

        messages = [*self._get_message_history(), HumanMessage(content=message)]
        if stream:
            return self._stream_chat(runnable, messages, registry, use_tools, kwargs)

        for _ in range(self.MAX_TOOL_ROUNDS):
            response = runnable.invoke(messages, **kwargs)
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
        self.messages = []

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
        print("💬 INTERACTIVE MODE" + (" (STREAMING)" if stream else ""))
        print("="*60)
        print("\nYou can now chat with the AI agent. It has access to various tools:")

        registry = ToolRegistry()
        tools = registry.get_tools_schemas()
        
        print("\n📦 Available Tools:")
        for i, tool in enumerate(tools, 1):
            func = tool["function"]
            print(f"  {i}. {func['name']}: {func['description']}")
        
        while True:
            try:
                user_input = input("\n👤 You: ").strip()
                
                if not user_input:
                    continue
                
                # Handle commands
                if user_input.lower() == "/exit" or user_input.lower() == "quit":
                    print("👋 Goodbye!")
                    break
                elif user_input.lower() == "/clear":
                    self.clear_history()
                    print("🚮 Done")
                    continue
                
                print("\n⏳ Processing...")
                response = self.chat(user_input, stream=False)
                
                print(f"🤖 Assistant: {response}")
                
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                logger.exception("Error in interactive mode")
            
if __name__ == '__main__':
    agent = DNDTranslatorAgent()
    agent.interactive_mode(agent)
