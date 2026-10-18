"""Start the local website and A2A server with one command."""

from __future__ import annotations

import argparse

import uvicorn

from flaskapp.combined import create_application


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the web application and A2A agents")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    advertised_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    # Plain HTTP is correct here: this is the local development server, which
    # uvicorn serves without TLS, and the URL is advertised to agents on the
    # same machine. Deployed traffic is HTTPS at the load balancer.
    application = create_application(base_url=f"http://{advertised_host}:{args.port}")  # NOSONAR
    uvicorn.run(application, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
