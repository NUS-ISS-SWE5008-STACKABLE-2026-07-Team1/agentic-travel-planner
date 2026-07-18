"""Development entry point. Use a production WSGI server outside local development."""

from flaskapp import create_app

app = create_app()


if __name__ == "__main__":
    app.run(port=5000, debug=False)
