import json
from typing import Any, Union

from .buquanshu_search import BuquanshuSearcher

class ToolRegistry:
    """翻译智能体工具的注册器"""
    
    def __init__(self):
        self.tools = {}
        self._register_default_tools()
        
    def _register_default_tools(self):
        """注册默认工具"""
        self.register_tool(
            name="search_terms",
            function=self.search_terms,
            description="Get standard translation of search terms",
            parameters={
                "type": "object",
                "properties": {
                    "en_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "English words to search"
                    }
                },
                "required": ["en_list"]
            }
        )
        self.register_tool(
            name="search_buquanshu",
            function=self.search_buquanshu,
            description=(
                "可搜索实体的汉化内容，返回不全书的标题、分类、来源路径、相关度、内容预览和网页链接"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索词；支持空格、竖线和双引号查询语法"
                    },
                    "title_only": {
                        "type": "boolean",
                        "description": "是否只搜索页面标题，默认 false"
                    },
                    "category": {
                        "type": "string",
                        "description": "书籍分类；默认 all，表示全部分类"
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "结果页码，默认 1"
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "每页结果数，默认 10，最大 20"
                    }
                },
                "required": ["keyword"]
            }
        )
        
    def register_tool(self, name: str, function: callable, description:str, parameters: dict):
        """注册新工具"""
        self.tools[name] = {
            "function": function,
            "description": description,
            "parameters": parameters
        }
        
    def get_tools_schemas(self) -> list[dict]:
        schemas = []
        for name, tool in self.tools.items():
            schemas.append({
                "type":"function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                }
            })
        return schemas
    
    def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with given arguments"""
        if name not in self.tools:
            return json.dumps({"error": f"Tool '{name}' not found"})
        
        try:
            result = self.tools[name]["function"](**arguments)
            return json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, (dict, list)) else str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})
        
    @staticmethod
    def search_terms(en_list: list):
        from app.core.database import RedisDB
        term_db = RedisDB(db=1)
        keys = []
        for en in en_list:
            keys.append(en)
            if " " in en:
                keys.extend(en.split())
        return term_db.get_list(keys)

    @staticmethod
    def search_buquanshu(
        keyword: str,
        title_only: bool = False,
        category: str = "all",
        page: int = 1,
        page_size: int = 10,
    ):
        with BuquanshuSearcher() as searcher:
            return searcher.search(
                keyword=keyword,
                title_only=title_only,
                category=category,
                page=page,
                page_size=page_size,
            )
