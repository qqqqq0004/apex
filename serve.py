"""Tiny static server for the dashboard. Serves the project root so the page
can fetch ../data/state.json. Run:  py serve.py  then open the printed URL.
"""
import http.server, socketserver, webbrowser, os, time
from pathlib import Path

PORT = 8765
os.chdir(Path(__file__).resolve().parent)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", f"/dashboard/index.html?v={time.time()}")
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    url = f"http://localhost:{PORT}/dashboard/index.html"
    print(f"Apex dashboard: {url}\nCtrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
