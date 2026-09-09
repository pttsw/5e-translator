import json
from unittest.mock import Mock

from .tools import ToolRegistry


class FakeTermDB:
    values = {
        "light": [
            {"cn": "光照", "category": "normal"},
            {"cn": "光亮术", "category": "spell"},
        ],
        "shield": [
            {"cn": "盾牌", "category": "item"},
            {"cn": "护盾术", "category": "spell"},
        ],
    }
    queried_keys = None

    def __init__(self, db=0):
        assert db == 1

    def get_list(self, keys):
        type(self).queried_keys = keys
        return {
            key: self.values.get(key, "null")
            for key in dict.fromkeys(keys)
        }


def test_default_tool_is_registered_with_valid_schema():
    registry = ToolRegistry()

    assert "search_terms" in registry.tools
    assert "search_buquanshu" in registry.tools

    schemas = registry.get_tools_schemas()
    assert len(schemas) == 2
    assert all(schema["type"] == "function" for schema in schemas)

    function_schema = next(
        schema["function"]
        for schema in schemas
        if schema["function"]["name"] == "search_terms"
    )
    assert function_schema["name"] == "search_terms"
    assert function_schema["description"]
    assert function_schema["parameters"] == {
        "type": "object",
        "properties": {
            "en_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "English words to search",
            }
        },
        "required": ["en_list"],
    }


def test_register_tool_adds_schema_and_callable():
    registry = ToolRegistry()

    def echo(value):
        return value

    registry.register_tool(
        name="echo",
        function=echo,
        description="Echo a value",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    assert registry.tools["echo"]["function"] is echo
    assert registry.get_tools_schemas()[-1]["function"]["name"] == "echo"


def test_execute_tool_serializes_mapping_and_preserves_unicode():
    registry = ToolRegistry()
    registry.register_tool(
        name="translate",
        function=lambda text: {"translation": f"译文：{text}"},
        description="Translate text",
        parameters={"type": "object"},
    )

    result = registry.execute_tool("translate", {"text": "light"})

    assert result == '{"translation": "译文：light"}'


def test_execute_tool_converts_scalar_result_to_string():
    registry = ToolRegistry()
    registry.register_tool(
        name="count",
        function=lambda: 3,
        description="Return a count",
        parameters={"type": "object"},
    )

    assert registry.execute_tool("count", {}) == "3"


def test_execute_unknown_tool_returns_error_payload():
    result = ToolRegistry().execute_tool("missing", {})

    assert json.loads(result) == {"error": "Tool 'missing' not found"}


def test_execute_tool_failure_returns_error_payload():
    registry = ToolRegistry()

    def fail():
        raise ValueError("invalid input")

    registry.register_tool(
        name="fail",
        function=fail,
        description="Always fail",
        parameters={"type": "object"},
    )

    assert json.loads(registry.execute_tool("fail", {})) == {
        "error": "invalid input"
    }


def test_search_terms_returns_known_unknown_and_phrase_terms(monkeypatch):
    monkeypatch.setattr("app.core.database.RedisDB", FakeTermDB)

    result = ToolRegistry.search_terms(["light shield", "apple"])

    assert result["light shield"] == "null"
    assert result["light"] == [
        {"cn": "光照", "category": "normal"},
        {"cn": "光亮术", "category": "spell"},
    ]
    assert result["shield"] == [
        {"cn": "盾牌", "category": "item"},
        {"cn": "护盾术", "category": "spell"},
    ]
    assert result["apple"] == "null"
    assert FakeTermDB.queried_keys == [
        "light shield", "light", "shield", "apple"
    ]


def test_search_terms_can_be_called_through_registry(monkeypatch):
    monkeypatch.setattr("app.core.database.RedisDB", FakeTermDB)

    result = ToolRegistry().execute_tool("search_terms", {"en_list": ["light"]})

    assert json.loads(result) == {
        "light": [
            {"cn": "光照", "category": "normal"},
            {"cn": "光亮术", "category": "spell"},
        ]
    }


def test_search_buquanshu_can_be_called_through_registry(monkeypatch):
    search = Mock(return_value={"total": 1, "results": [{"title": "火球术"}]})
    searcher = Mock()
    searcher.search = search
    searcher.__enter__ = Mock(return_value=searcher)
    searcher.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        "app.core.agent.tools.BuquanshuSearcher",
        Mock(return_value=searcher),
    )

    result = ToolRegistry().execute_tool(
        "search_buquanshu",
        {"keyword": "火球术", "title_only": True, "page_size": 5},
    )

    assert json.loads(result) == {
        "total": 1,
        "results": [{"title": "火球术"}],
    }
    search.assert_called_once_with(
        keyword="火球术",
        title_only=True,
        category="all",
        page=1,
        page_size=5,
    )
