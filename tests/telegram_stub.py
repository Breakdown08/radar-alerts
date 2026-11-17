"""A fake Telegram Bot API for the CI job `compose`: answers 401 to everything, logs method and path."""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT: int = 8081
TOKEN_RE: re.Pattern[str] = re.compile(r"/bot[^/]+/")
BODY: bytes = json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"}).encode()


class StubHandler(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        print(f"{self.command} {TOKEN_RE.sub('/bot<token>/', self.path)}", flush=True)
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self._answer()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # The default writes the request line, token included, to stderr.
        return


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), StubHandler).serve_forever()
