from flask import Blueprint

# Single app-wide blueprint to preserve all existing URL paths and endpoint names
main_bp = Blueprint('main', __name__)
