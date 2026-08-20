"""Importing the package must not build an app or touch a database.

Once DATABASE_URL is honoured, an import-time create_app() would connect to
the deployed Postgres and run schema DDL against it — during tests, on every
developer machine, on every import.
"""

import importlib


def test_importing_the_package_does_not_build_an_app():
    flaskapp = importlib.import_module("flaskapp")
    assert not hasattr(flaskapp, "app"), (
        "flaskapp.app builds an application at import time; use create_app()"
    )


def test_create_app_is_still_exported():
    flaskapp = importlib.import_module("flaskapp")
    assert callable(flaskapp.create_app)
