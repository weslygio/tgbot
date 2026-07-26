import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health server listening on port {port}")
    server.serve_forever()


def main():
    port = int(os.getenv("PORT", 8080))

    thread = threading.Thread(
        target=start_health_server, args=(port,), daemon=True
    )
    thread.start()

    from bot import main as bot_main

    bot_main()


if __name__ == "__main__":
    main()
