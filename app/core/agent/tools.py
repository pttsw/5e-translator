import json
from typing import Any, Union

from .buquanshu_search import BuquanshuSearcher

class ToolRegistry:
    """翻译智能体工具的注册器"""
    
    def __init__(self, references=None):
        from .reference_tools import ReferenceTools
        self.references = references if references is not None else ReferenceTools()
        self.tools = {}
        self._register_default_tools()
        
    def _register_default_tools(self):
        """注册默认工具"""
        for name, query_key, filter_key, limit_key, description in [
            ('lookup_translations', 'en_list', 'category', 'limit_per_term',
             '按完整英文短语查译文候选，依次优先 term 表、会话参考、不全书、运行临时术语；返回置信度、冲突和证据。'),
            ('lookup_entities', 'names', 'entity_type', 'limit_per_name',
             '按中文或英文名称查询完整实体说明，优先会话资料，其次不全书全文；不返回搜索预览作为完整说明。'),
        ]:
            properties = {
                query_key: {'type': 'array', 'items': {'type': 'string', 'maxLength': 200}, 'minItems': 1, 'maxItems': 10},
                filter_key: {'type': 'string'},
                limit_key: {'type': 'integer', 'minimum': 1, 'maximum': 10},
            }
            if name == 'lookup_translations':
                properties['entity_id'] = {'type': 'string'}
            self.register_tool(name, getattr(self.references, name), description,
                               {'type': 'object', 'properties': properties, 'required': [query_key], 'additionalProperties': False})

        
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
            if name in {'lookup_translations', 'lookup_entities'}:
                return json.dumps({'query': [], 'results': [], 'truncated': False,
                                   'errors': [{'code': type(e).__name__}]})
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
