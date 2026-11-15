from . import admin, buildings, cells, features, health, incidents, observations, roads


def register_blueprints(api):
    for module in (health, cells, observations, incidents, features, buildings, roads, admin):
        api.register_blueprint(module.blp)
