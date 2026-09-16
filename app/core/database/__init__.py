"""Database adapters exposed without importing every optional backend eagerly."""

from importlib import import_module


_ADAPTER_MODULES = {
    "DBDictionary": ".db_dictionary",
    "RedisDB": ".redis_db",
    "DatabaseAdapter": ".dictionary",
    "ChromaAdapter": ".chroma_db",
    "MySQLDatabase": ".mysql_db",
}

__all__ = list(_ADAPTER_MODULES)


def __getattr__(name):
    module_name = _ADAPTER_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    adapter = getattr(import_module(module_name, __name__), name)
    globals()[name] = adapter
    return adapter
