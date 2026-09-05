"""Production ASGI entry point for the combined web and A2A application."""

from flaskapp.combined import create_application


application = create_application()
