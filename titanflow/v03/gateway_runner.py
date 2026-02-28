"""Run the v0.3 HTTP gateway."""

from __future__ import annotations

import logging

from titanflow.v03.config import load_config
from titanflow.v03.gateway_http import GatewayHTTPServer

logging.basicConfig(level=logging.INFO)


def main() -> None:
    config = load_config()
    server = GatewayHTTPServer(
        host=config.gateway_host,
        port=config.gateway_port,
        core_socket=config.core_socket,
    )
    logging.getLogger("titanflow.v03.gateway").info(
        "Gateway listening on %s:%s", config.gateway_host, config.gateway_port
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
