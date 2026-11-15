import logging

from flask import Flask

from .config import Config
from .extensions import cors, db, migrate, rest_api


def create_app(config_object=Config, **overrides):
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.config.update(overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    from . import models  # noqa: F401  (register tables on metadata)

    migrate.init_app(app, db)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
    rest_api.init_app(app)

    from .api import register_blueprints

    register_blueprints(rest_api)
    return app
