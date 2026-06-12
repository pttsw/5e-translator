from flask import Blueprint

api_bp = Blueprint('api_bp', __name__)


from .words import api
api.init_app(api_bp)
from .user import api, login_manager
api.init_app(api_bp)
from .proofread import api
api.init_app(api_bp)
from .json import api
api.init_app(api_bp)
from .dashboard import api
api.init_app(api_bp)
from .source import api
api.init_app(api_bp)
from .file import api
api.init_app(api_bp)
from .chm import api
api.init_app(api_bp)
from .term import api
api.init_app(api_bp)
