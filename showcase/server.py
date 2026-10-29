"""Loopback-only showcase UI. No credentials or arbitrary commands are exposed."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse
from showcase.coordinator import ROOT, STATE, STATUS, LATEST, read, alive, launch_run

WEB = Path(__file__).parent / "web"


def snapshot():
    status = read(STATUS, {"status": "ready"})
    if status.get("status") == "running" and not alive(status.get("pid")):
        status = {**status, "status": "failed", "error": "Die Prüfung wurde unterbrochen. Vorhandene Ausführungen bleiben erhalten."}
    latest = read(LATEST)
    data = {"status": status, "latest": latest, "platform": None, "manifest": None, "results": []}
    if latest:
        path = (ROOT / latest["run_dir"]).resolve()
        if path.is_relative_to(STATE / "runs"):
            published = (ROOT / latest["platform_receipt"]).resolve() if latest.get("platform_receipt") else path / "platform.json"
            if published.is_relative_to(STATE):
                data["platform"] = read(published)
            data["manifest"] = read(path / "manifest.json")
            results = path / "results.jsonl"
            if results.exists():
                data["results"] = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
            data["scenarios"] = read(path / "scenarios.json", [])
    return data


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"service": "techhub-privacy-showcase", "status": "ok"})
        if path == "/api/state":
            return self.send_json(200, snapshot())
        destinations = {"/open/aip-baseline": "aip_baseline", "/open/aip-candidate": "aip_candidate",
                        "/open/hub-rule": "hub_profile", "/open/hub-report": "hub_report"}
        if path in destinations:
            latest = read(LATEST, {})
            destination = latest.get("urls", {}).get(destinations[path])
            if not destination or urlparse(destination).scheme not in ("http", "https"):
                destination = "/#vergleich"
            self.send_response(303)
            self.send_header("Location", destination)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path in ("/", "/index.html", "/app.js", "/style.css"):
            name = "index.html" if path == "/" else path.lstrip("/")
            content = (WEB / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}[name.rsplit(".", 1)[1]])
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if self.headers.get("X-Demo-Action") != "run" or (origin and urlparse(origin).netloc != self.headers.get("Host")):
            return self.send_json(403, {"error": "Start the comparison from the local demo page."})
        if urlparse(self.path).path != "/api/run":
            return self.send_json(404, {"error": "Unknown action."})
        try:
            pid = launch_run()
            return self.send_json(202, {"status": "running", "pid": pid})
        except RuntimeError as error:
            return self.send_json(409, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=28145)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
