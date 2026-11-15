from . import admin, auth, buildings, cells, features, health, incidents, observations, pois, risk, roads


def register_blueprints(api):
    for module in (health, auth, cells, observations, incidents, features, buildings, roads, pois, risk, admin):
        api.register_blueprint(module.blp)
