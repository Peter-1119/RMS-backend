from flask import Flask
from flask_cors import CORS
import os
from config import TEMP_ROOT_DIR
from data_store import load_all

def create_app():
    app = Flask(__name__)
    CORS(app)
    os.makedirs(TEMP_ROOT_DIR, exist_ok=True)

    # blueprints
    from modules.auth_bp import bp as auth_bp
    from modules.docs import bp as docs_bp
    from modules.media import bp as media_bp
    from modules.mes import bp as mes_bp
    from modules.conditions import bp as cond_bp

    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(docs_bp, url_prefix="/docs")
    app.register_blueprint(media_bp, url_prefix="/uploads")   # serves under /uploads/*
    app.register_blueprint(mes_bp, url_prefix="/mes")
    app.register_blueprint(cond_bp, url_prefix="/conditions")

    # preload CSV caches
    load_all()

    return app
