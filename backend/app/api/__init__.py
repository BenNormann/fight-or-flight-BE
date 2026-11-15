from . import admin, auth, buildings, cells, features, health, incidents, observations, pois, risk, roads, route


def register_blueprints(api):
    for module in (health, auth, cells, observations, incidents, features, buildings, roads, pois, risk, route, admin):
        api.register_blueprint(module.blp)
