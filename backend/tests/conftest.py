import pytest

from app import create_app


@pytest.fixture
def app():
    # No DB connection is made unless a test queries it.
    app = create_app(TESTING=True)
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
