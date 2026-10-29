"""Local orchestration. Every published result comes from a completed native run."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".showcase"
STATUS = STATE / "status.json"
LATEST = STATE / "latest.json"
PORT = int(os.environ.get("SHOWCASE_PORT", "28145"))


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def reachable(url):
    try:
        with urlopen(url, timeout=3) as response:
            return response.status < 500
    except Exception:
        return False


def doctor(model=None):
    services = {
        "AIP": os.environ.get("AIP_BASE_URL", "http://localhost:8010") + "/openapi.json",
        "ASQI Hub": os.environ.get("ASQI_BASE_URL", "http://localhost:8001") + "/openapi.json",
        "Ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags",
    }
    checks = {name: reachable(url) for name, url in services.items()}
    if checks["Ollama"]:
        try:
            with urlopen(services["Ollama"], timeout=3) as response:
                models = json.load(response)["models"]
            selected = model or os.environ.get("SHOWCASE_MODEL", "qwen2.5:7b")
            checks["Modell " + selected] = any(item.get("name") == selected or item.get("model") == selected for item in models)
        except Exception:
            checks["Lokales Modell"] = False
    for name, available in checks.items():
        print(f"{name}: {'bereit' if available else 'nicht erreichbar'}", flush=True)
    return checks


def bootstrap_local_stack():
    """Reuse the user's existing local demo launcher when explicitly available."""
    sibling = ROOT.parent / "demo"
    aip = os.environ.get("AIP_BASE_URL", "http://localhost:8010").rstrip("/")
    hub = os.environ.get("ASQI_BASE_URL", "http://localhost:8001").rstrip("/")
    if aip != "http://localhost:8010" or hub != "http://localhost:8001":
        return
    if not reachable(aip + "/openapi.json") or not reachable(hub + "/openapi.json"):
        if sibling.is_file() and (ROOT.parent / "aip-v2-demo").is_dir():
            subprocess.run([str(sibling), "start"], cwd=ROOT.parent, check=True)


def progress(event):
    previous = read(STATUS, {})
    event = event if isinstance(event, dict) else {"message": str(event)}
    previous.update({"updated_at": now(), "event": event})
    events = previous.setdefault("events", [])
    events.append({"at": now(), **event})
    previous["events"] = events[-80:]
    save(STATUS, previous)
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_pipeline(repeats=3, model=None, run_dir=None):
    STATE.mkdir(exist_ok=True)
    with (STATE / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Eine Prüfung läuft bereits. Fortschritt im Demo-Fenster ansehen.") from None
        save(STATUS, {"status": "running", "pid": os.getpid(), "started_at": now(), "events": []})
        try:
            if run_dir is None:
                progress({"stage": "preflight", "message": "Verbindungen prüfen"})
                checks = doctor(model)
                if not all(checks.values()):
                    raise RuntimeError("AIP, ASQI Hub und Ollama müssen erreichbar sein. ./demo doctor zeigt den Status.")
                from showcase.runtime import run_showcase
                progress({"stage": "agent", "message": "Beide Agentenversionen mit denselben Fragen prüfen"})
                actual = run_showcase(STATE / "runs", repeats=repeats,
                                      model=model or os.environ.get("SHOWCASE_MODEL", "qwen2.5:7b"), progress=progress)
                run_dir = Path(actual)
            else:
                run_dir = Path(run_dir).resolve()
                if not run_dir.is_relative_to(ROOT):
                    raise ValueError("Use a run directory inside this checkout.")
            status = read(STATUS, {})
            status["run_dir"] = str(run_dir.relative_to(ROOT))
            save(STATUS, status)
            progress({"stage": "aip", "message": "Ausführungen in AIP unabhängig bewerten"})
            from showcase.platform import integrate_run
            receipt = integrate_run(run_dir, progress=progress)
            if receipt.get("status") != "completed":
                raise RuntimeError("Die Plattformprüfung ist noch nicht vollständig bestätigt.")
            published = STATE / "published" / (run_dir.name + "-platform.json")
            save(published, receipt)
            record = {"run_dir": str(run_dir.relative_to(ROOT)), "completed_at": now(),
                      "platform_receipt": str(published.relative_to(ROOT)),
                      "urls": receipt.get("urls", {}), "platform_status": receipt["status"]}
            save(LATEST, record)
            progress({"stage": "completed", "message": "Vergleich und ASQI-Bericht sind bereit"})
            status = read(STATUS, {})
            status.update(status="completed", completed_at=now(), latest=record)
            save(STATUS, status)
            print(f"\nDemo: http://localhost:{PORT}/\nBericht: {receipt.get('urls', {}).get('hub_report', '')}")
            return run_dir
        except BaseException as error:
            status = read(STATUS, {})
            status.update(status="failed", updated_at=now(), error=str(error))
            save(STATUS, status)
            raise


def launch_run(repeats=3):
    STATE.mkdir(exist_ok=True)
    with (STATE / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read(STATUS, {})
        if current.get("status") == "running" and alive(current.get("pid")):
            raise RuntimeError("Eine Prüfung läuft bereits.")
        # The child also takes a kernel lock, covering command-line launches.
        log = (STATE / "run.log").open("ab")
        child = subprocess.Popen([sys.executable, "-m", "showcase.coordinator", "run", "--repeats", str(repeats)],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        # Only the process that actually obtains run.lock may publish STATUS.
        # In particular, a losing UI launch must not overwrite a CLI run's PID.
        for _ in range(50):
            status = read(STATUS, {})
            if status.get("pid") == child.pid or child.poll() is not None:
                break
            if status.get("status") == "running" and alive(status.get("pid")):
                raise RuntimeError("Eine Prüfung läuft bereits.")
            time.sleep(0.05)
        return child.pid


def start(open_browser=True):
    bootstrap_local_stack()
    STATE.mkdir(exist_ok=True)
    identity = read(STATE / "server.json", {})
    if not (alive(identity.get("pid")) and reachable(f"http://localhost:{PORT}/api/health")):
        log = (STATE / "server.log").open("ab")
        child = subprocess.Popen([sys.executable, "-m", "showcase.server", "--port", str(PORT)],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        save(STATE / "server.json", {"pid": child.pid, "port": PORT, "started_at": now()})
        for _ in range(30):
            if reachable(f"http://localhost:{PORT}/api/health"):
                break
            if child.poll() is not None:
                raise RuntimeError("Demo-Fenster konnte nicht starten. Siehe .showcase/server.log.")
            time.sleep(0.2)
        else:
            raise RuntimeError("Das Demo-Fenster antwortet noch nicht.")
    print(f"Demo bereit: http://localhost:{PORT}/", flush=True)
    if open_browser:
        webbrowser.open(f"http://localhost:{PORT}/")


def verify():
    latest = read(LATEST)
    if not latest:
        raise RuntimeError("Noch kein abgeschlossener Vergleich vorhanden. ./demo run starten.")
    path = ROOT / latest["run_dir"]
    published = ROOT / latest["platform_receipt"] if latest.get("platform_receipt") else path / "platform.json"
    receipt = read(published, {})
    if receipt.get("status") != "completed" or receipt.get("client_scores_supplied") is not False:
        raise RuntimeError("Unvollständiger oder nicht nativer Plattformnachweis.")
    print(json.dumps({"run_dir": latest["run_dir"], "status": receipt["status"],
                      "coverage": receipt.get("coverage"), "urls": receipt.get("urls")}, indent=2, ensure_ascii=False))


def main():
    from showcase.config import configure_environment
    configure_environment()
    parser = argparse.ArgumentParser(description="TechHub: Prüfung, AIP-Vergleich und ASQI-Bericht")
    parser.add_argument("action", nargs="?", choices=["start", "showcase", "run", "resume", "stop", "status", "doctor", "verify"], default="start")
    parser.add_argument("run_dir", nargs="?")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    if args.action in ("start", "showcase"):
        start(not args.no_open)
    elif args.action == "run":
        run_pipeline(args.repeats, args.model)
    elif args.action == "resume":
        if not args.run_dir:
            parser.error("resume requires the existing run directory")
        run_pipeline(run_dir=args.run_dir)
    elif args.action == "doctor":
        if not all(doctor().values()):
            sys.exit(1)
    elif args.action == "verify":
        verify()
    elif args.action == "status":
        doctor()
        print(json.dumps(read(STATUS, {"status": "ready"}), indent=2, ensure_ascii=False))
    elif args.action == "stop":
        record = read(STATE / "server.json", {})
        if alive(record.get("pid")):
            command = subprocess.check_output(["ps", "-p", str(record["pid"]), "-o", "command="], text=True)
            if "-m showcase.server" not in command:
                raise RuntimeError("Die gespeicherte Prozess-ID gehört nicht mehr zum Demo-Fenster.")
            os.kill(record["pid"], signal.SIGTERM)
        (STATE / "server.json").unlink(missing_ok=True)
        print("Demo-Fenster gestoppt. AIP, Hub und gespeicherte Ergebnisse bleiben verfügbar.")


if __name__ == "__main__":
    main()
