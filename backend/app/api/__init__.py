from . import admin, buildings, cells, features, health, incidents, observations, pois, risk, roads


def register_blueprints(api):
    for module in (health, cells, observations, incidents, features, buildings, roads, pois, risk, admin):
        api.register_blueprint(module.blp)
