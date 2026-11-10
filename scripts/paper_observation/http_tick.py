"""Internal Cloud Run entry point for one configured observation role."""

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from .__main__ import main as run_observation


class TickHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        status = 404
        if self.path == "/tick":
            status = 204 if run_observation([self.server.observation_mode]) == 0 else 500
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *args):
        # The runner emits a fixed result. Do not log caller-controlled paths.
        pass


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("collect", "watchdog"):
        raise SystemExit("Expected collect or watchdog")
    # The collector uses a signal-based deadline, which needs the main thread.
    with HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), TickHandler) as server:
        server.observation_mode = sys.argv[1]
        server.serve_forever()


if __name__ == "__main__":
    main()
