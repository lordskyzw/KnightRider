"""Tiny static-file server for the KnightRider landing page on Railway.

Serves the current directory (docs/) on the port Railway injects via $PORT,
defaulting to 8000 locally.
"""
from __future__ import annotations

import http.server
import os
import socketserver
import sys


PORT = int(os.environ.get("PORT", "8000"))


class _Handler(http.server.SimpleHTTPRequestHandler):
    # Map common extensions to sane MIME types — Python's defaults miss a few.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js":   "application/javascript",
        ".mjs":  "application/javascript",
        ".css":  "text/css",
        ".html": "text/html",
        ".svg":  "image/svg+xml",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".ico":  "image/x-icon",
        ".txt":  "text/plain",
        ".json": "application/json",
    }

    def end_headers(self) -> None:
        # Modest cache for static assets in production. The HTML stays fresh.
        if self.path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".svg")):
            self.send_header("Cache-Control", "public, max-age=300")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Compact log line on stdout so Railway logs are readable.
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def main() -> None:
    # Always serve from the directory this script lives in, regardless of cwd.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with socketserver.TCPServer(("0.0.0.0", PORT), _Handler) as httpd:
        sys.stdout.write(f"KnightRider landing page on :{PORT}\n")
        sys.stdout.flush()
        httpd.serve_forever()


if __name__ == "__main__":
    main()
