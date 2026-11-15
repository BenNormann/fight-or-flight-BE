from . import admin, buildings, cells, features, health, incidents, observations, pois, roads


def register_blueprints(api):
    for module in (health, cells, observations, incidents, features, buildings, roads, pois, admin):
        api.register_blueprint(module.blp)
