import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

target_db = current_app.extensions["migrate"].db
config.set_main_option(
    "sqlalchemy.url", target_db.engine.url.render_as_string(hide_password=False).replace("%", "%%")
)

# Objects managed outside the ORM (PostGIS/Timescale internals, caggs, views)
IGNORED = {"spatial_ref_sys", "obs_10min", "obs_hourly", "crime_hourly", "ml_features"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and (name in IGNORED or name.startswith("_")):
        return False
    return True


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_db.metadata,
                      literal_binds=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with target_db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_db.metadata,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
