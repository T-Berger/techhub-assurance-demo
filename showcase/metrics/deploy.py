"""Build and deploy only the three owned TechHub metric workers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .checks import NAMES, VERSION, digest


def settings():
    repo = Path(os.environ.get("AIP_REPO", Path(__file__).resolve().parents[3] / "aip-v2-demo")).expanduser().resolve()
    python = Path(os.environ.get("AIP_API_PYTHON", repo / "services/aip-api/.venv/bin/python"))
    if not python.is_file() or not repo.is_dir():
        raise RuntimeError("Set AIP_REPO and AIP_API_PYTHON to an installed AIP SDK environment before deploying native metrics")
    return repo, python


def command(args, **kwargs):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {(result.stderr or result.stdout)[-3000:]}")
    return result.stdout


def _kube(*args, **kwargs):
    return command(["kubectl", "--context", os.environ.get("TECHHUB_KUBE_CONTEXT", "orbstack"),
                    "-n", os.environ.get("TECHHUB_NUCLIO_NAMESPACE", "nuclio"), *args], **kwargs)


def _get(kind, name):
    data = _kube("get", kind, name, "-o", "json", "--ignore-not-found")
    return json.loads(data) if data.strip() else None


def prepare_workers(output: Path, progress):
    """Create reproducible bundles and verify genuine Nuclio readiness.

    Never patches existing non-owned resources. The source hash in the image
    tag prevents an edited worker from silently replacing an old image.
    """
    repo, python = settings()
    source = Path(__file__).resolve().parent
    source_hash = digest({name: (source / name).read_text() for name in ("__init__.py", "checks.py", "native.py", "handler.py", "worker.py")})
    owner = "techhub-privacy"
    image = os.environ.get("TECHHUB_METRIC_IMAGE", f"techhub-privacy-metrics:{VERSION}-{source_hash[:12]}")
    output.mkdir(parents=True, exist_ok=True)
    context = output / "image"
    package = context / "techhub_privacy_metrics"
    package.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "checks.py", "native.py", "handler.py"):
        shutil.copy2(source / name, package / name)
    shutil.copy2(source / "worker.py", context / "techhub_privacy_worker.py")
    base = os.environ.get("TECHHUB_METRIC_BASE_IMAGE", "rs-behaviour-metric:local")
    (context / "Dockerfile").write_text(f"FROM {base}\nCOPY techhub_privacy_metrics /opt/nuclio/techhub_privacy_metrics\nCOPY techhub_privacy_worker.py /opt/nuclio/techhub_privacy_worker.py\nENV HOME=/tmp XDG_CACHE_HOME=/tmp/cache\nUSER 10001:10001\n")
    progress({"stage": "platform_workers", "message": "Building the isolated deterministic AIP workers"})
    docker = ["docker", "--context", os.environ.get("TECHHUB_DOCKER_CONTEXT", "orbstack")]
    command([*docker, "build", "-t", image, context])
    image_id = command([*docker, "image", "inspect", image, "--format", "{{.Id}}"]).strip()
    namespace = os.environ.get("TECHHUB_NUCLIO_NAMESPACE", "nuclio")
    project = {"apiVersion": "nuclio.io/v1beta1", "kind": "NuclioProject", "metadata": {
        "name": owner, "namespace": namespace, "labels": {"ai.demo.owner": owner}},
        "spec": {"description": "TechHub order privacy, usefulness and observed completion"}}
    if _get("nuclioproject", owner) is None:
        _kube("create", "-f", "-", input=json.dumps(project))
    config_schema = {"type": "object", "additionalProperties": False, "properties": {
        "references_by_trace": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "object", "additionalProperties": True}},
        "references_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "source_manifest_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "annotations": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}], "default": None}},
        "required": ["references_by_trace", "references_sha256", "source_manifest_sha256"]}
    receipts = {}
    for name, display in NAMES.items():
        function_name = name.replace(".", "-").replace("_", "-") + "-v" + VERSION.replace(".", "-")
        function = {"apiVersion": "nuclio.io/v1beta1", "kind": "NuclioFunction", "metadata": {
            "name": function_name, "namespace": namespace, "labels": {
                "ai.demo.owner": owner, "nuclio.io/project-name": owner, "aip.resaro.ai/op_kind": "trace_metric"}},
            "spec": {"runtime": "python:3.12", "handler": "bootstrap:handler", "image": image,
                "imagePullPolicy": "Never", "minReplicas": 1, "maxReplicas": 1, "eventTimeout": "180s",
                "env": [{"name": "HANDLER_MODULE", "value": "techhub_privacy_worker"}, {"name": "OP_NAME", "value": name},
                        {"name": "TECHHUB_MINIO_HOST", "value": os.environ.get("TECHHUB_MINIO_HOST", "host.docker.internal")},
                        {"name": "TECHHUB_MINIO_PORT", "value": os.environ.get("TECHHUB_MINIO_PORT", "19010")}],
                "triggers": {"http": {"kind": "http", "numWorkers": 1}},
                "resources": {"requests": {"memory": "256Mi", "cpu": "100m"}, "limits": {"memory": "2Gi", "cpu": "1"}}}}
        exists = _get("nucliofunction", function_name)
        if exists:
            if exists["metadata"].get("labels", {}).get("ai.demo.owner") != owner or exists["spec"]["image"] != image:
                raise RuntimeError(f"Worker {function_name} already exists with another source image; increment the metric version and use an explicit deployment migration")
        else:
            function["status"] = {"state": "waitingForResourceConfiguration"}
            _kube("create", "-f", "-", input=json.dumps(function))
        folder = output / "bundles" / name.replace(".", "-")
        folder.mkdir(parents=True, exist_ok=True)
        pkg = folder / "techhub_privacy_metrics"
        shutil.copytree(package, pkg, dirs_exist_ok=True)
        manifest = {"manifest_version": "1", "name": name, "version": VERSION, "kind": "trace_metric",
            "description": display + "; actual tool and final-response evidence, not a general security certification.",
            "display_name": display, "scorer_contract": "per_row", "direction": "higher_is_better",
            "accepts": [{"name": "canonical_partition_v1"}], "partition_types": ["trace"],
            "required_kinds": [], "required_columns": [], "execution": {"processing_kind": "cpu", "timeout_seconds": 180, "function_name": function_name},
            "config_schema": config_schema, "entrypoint": "handler:handle",
            "dependencies": ["pyproject.toml", "uv.lock"]}
        # JSON is valid YAML, avoiding a dependency just for emitting manifests.
        (folder / "manifest.yaml").write_text(json.dumps(manifest, indent=2) + "\n")
        (folder / "function.yaml").write_text(json.dumps(function, indent=2) + "\n")
        (folder / "handler.py").write_text("from techhub_privacy_metrics.handler import handle\n")
        sources = {"aip-core": repo / "libs/aip-core", "aip-traces-core": repo / "libs/aip-traces/aip-traces-core",
                   "aip-traces-api": repo / "libs/aip-traces/aip-traces-api", "aip-traces-agent": repo / "libs/aip-traces/aip-traces-agent"}
        pyproject = '[project]\nname = "techhub-privacy-metrics"\nversion = "' + VERSION + '"\nrequires-python = ">=3.12"\ndependencies = ["aip-core", "aip-traces-api", "aip-traces-agent"]\n[tool.uv.sources]\n'
        pyproject += "\n".join(f'{key} = {{ path = {json.dumps(str(path))}, editable = true }}' for key, path in sources.items()) + "\n"
        (folder / "pyproject.toml").write_text(pyproject)
        command(["uv", "lock", "--offline", "--project", folder])
        script = "from aip_sdk._op_packaging import build_op_metadata_from_manifest,bundle_folder; import pathlib,json,sys; p=pathlib.Path(sys.argv[1]); [(p/n).unlink(missing_ok=True) for n in ('metadata.json','bundle.tar.gz')]; e,f=build_op_metadata_from_manifest(p/'manifest.yaml'); b=bundle_folder(f); (p/'metadata.json').write_text(e.model_dump_json()); (p/'bundle.tar.gz').write_bytes(b)"
        command([python, "-c", script, folder])
        receipts[name] = {"function_name": function_name, "folder": str(folder), "image": image, "image_id": image_id,
                          "source_sha256": source_hash, "version": VERSION}
    deadline = time.monotonic() + 180
    while True:
        states = {name: _get("nucliofunction", r["function_name"]).get("status", {}).get("state") for name, r in receipts.items()}
        if all(state == "ready" for state in states.values()):
            break
        if any(state == "error" for state in states.values()) or time.monotonic() > deadline:
            raise RuntimeError("Isolated native workers failed readiness: " + json.dumps(states))
        progress({"stage": "platform_workers", "message": "Waiting for native metric worker readiness", "states": states})
        time.sleep(3)
    for name, record in receipts.items():
        service = _get("service", "nuclio-" + record["function_name"])
        record["direct_url"] = "http://" + service["spec"]["clusterIP"] + ":8080"
        record["state"] = "ready"
    return receipts


def ensure_trace_quality_worker(output: Path, progress):
    """Add a genuinely missing stock trace quality check; never replace one."""
    name = "quality-check-trace-dimension-coverage"
    exists = _get("nucliofunction", name)
    if exists and exists["metadata"].get("labels", {}).get("ai.demo.owner") != "techhub-privacy":
        if exists.get("status", {}).get("state") != "ready":
            raise RuntimeError("The existing trace-dimension quality worker is not ready; restore it before continuing")
        return {"function_name": name, "state": "ready", "created_by_showcase": exists["metadata"].get("labels", {}).get("ai.demo.owner") == "techhub-privacy",
                "image": exists["spec"]["image"]}
    progress({"stage": "platform_quality_worker", "message": "Die erforderliche native Qualitätsprüfung für Agenten-Traces wird bereitgestellt"})
    source = Path(__file__).resolve().parent
    repo, _ = settings()
    output.mkdir(parents=True, exist_ok=True)
    worker = (source / "worker.py").read_text().replace("from techhub_privacy_metrics.handler import handle", "from aip_data.nuclio_runner import handle")
    (output / "techhub_trace_quality_worker.py").write_text(worker)
    libraries = {}
    for library in ("aip-core", "aip-data"):
        origin = repo / "libs" / library
        shutil.copytree(origin, output / library, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", ".pytest_cache", "*.egg-info"))
        libraries[library] = digest({str(p.relative_to(origin)): p.read_text() for p in sorted((origin / "src").rglob("*.py"))})
    source_hash = digest({"worker": worker, "libraries": libraries})
    image = "techhub-trace-quality:" + source_hash[:12]
    base = os.environ.get("TECHHUB_QUALITY_BASE_IMAGE", "rs-manufacturing-quality:local")
    (output / "Dockerfile").write_text(f"FROM {base}\nUSER 0:0\nCOPY aip-core /opt/nuclio/techhub-stock/aip-core\nCOPY aip-data /opt/nuclio/techhub-stock/aip-data\nRUN uv build --wheel --out-dir /tmp/techhub-wheels /opt/nuclio/techhub-stock/aip-core && uv build --wheel --out-dir /tmp/techhub-wheels /opt/nuclio/techhub-stock/aip-data && uv pip install --system --no-deps /tmp/techhub-wheels/*.whl && rm -rf /tmp/techhub-wheels\nCOPY techhub_trace_quality_worker.py /opt/nuclio/techhub_trace_quality_worker.py\nENV HOME=/tmp XDG_CACHE_HOME=/tmp/cache\nUSER 10001:10001\n")
    docker = ["docker", "--context", os.environ.get("TECHHUB_DOCKER_CONTEXT", "orbstack")]
    command([*docker, "build", "-t", image, output])
    namespace = os.environ.get("TECHHUB_NUCLIO_NAMESPACE", "nuclio")
    function = {"apiVersion": "nuclio.io/v1beta1", "kind": "NuclioFunction", "metadata": {"name": name, "namespace": namespace,
        "labels": {"ai.demo.owner": "techhub-privacy", "nuclio.io/project-name": "techhub-privacy", "aip.resaro.ai/op_kind": "quality_check"}},
        "spec": {"runtime": "python:3.12", "handler": "bootstrap:handler", "image": image, "imagePullPolicy": "Never",
            "minReplicas": 1, "maxReplicas": 1, "eventTimeout": "180s", "env": [
                {"name": "HANDLER_MODULE", "value": "techhub_trace_quality_worker"}, {"name": "OP_NAME", "value": "quality_check.trace_dimension_coverage"},
                {"name": "TECHHUB_MINIO_HOST", "value": os.environ.get("TECHHUB_MINIO_HOST", "host.docker.internal")},
                {"name": "TECHHUB_MINIO_PORT", "value": os.environ.get("TECHHUB_MINIO_PORT", "19010")}],
            "triggers": {"http": {"kind": "http", "numWorkers": 1}},
            "resources": {"requests": {"memory": "256Mi", "cpu": "100m"}, "limits": {"memory": "2Gi", "cpu": "1"}}},
        "status": {"state": "waitingForResourceConfiguration"}}
    if exists:
        if exists["spec"]["image"] != image:
            # Only our newly supplied stock-check bootstrap is repairable here;
            # a pre-existing deployment from another owner was returned above.
            _kube("patch", "nucliofunction", name, "--type=merge", "-p", json.dumps({"spec": {"image": image}, "status": {"state": "waitingForResourceConfiguration"}}))
    else:
        _kube("create", "-f", "-", input=json.dumps(function))
    deadline = time.monotonic() + 240
    while True:
        record = _get("nucliofunction", name)
        state = record.get("status", {}).get("state")
        if state == "ready":
            return {"function_name": name, "state": state, "created_by_showcase": True, "image": image,
                    "source_sha256": source_hash, "libraries": libraries, "stock_runner": "aip_data.nuclio_runner.handle"}
        if state == "error" or time.monotonic() > deadline:
            raise RuntimeError("The required stock trace quality worker did not become ready: " + str(state))
        time.sleep(3)
