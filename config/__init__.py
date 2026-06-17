from .settings import *
from .transform_settings import *
from .transform_category import *
from .splited_dir_map import *
from .para_settings import *


class _LazySwagger:
    def _get(self):
        from importlib import import_module

        return import_module(".swagger", __name__).swagger

    def init_app(self, *args, **kwargs):
        return self._get().init_app(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._get(), name)


swagger = _LazySwagger()


def __getattr__(name):
    if name in {
        "Swagger",
        "SWAGGER_TITLE",
        "SWAGGER_DESC",
        "SWAGGER_HOST",
        "WEB_RESULT_URL",
        "swagger_config",
    }:
        from importlib import import_module

        swagger_module = import_module(".swagger", __name__)
        return getattr(swagger_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
