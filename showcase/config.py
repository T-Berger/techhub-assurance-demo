"""Load explicit demo configuration without copying or logging credentials."""
import os
from pathlib import Path


def configure_environment():
    from dotenv import dotenv_values, load_dotenv
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    if not os.environ.get("AIP_TOKEN") and not os.environ.get("AIP_DEMO_PASSWORD"):
        # Convenience for this workspace's existing local demo only. An explicit
        # path or environment value always takes precedence; no secrets are copied.
        configured = os.environ.get("AIP_LOCAL_ENV_FILE")
        local = Path(configured).expanduser() if configured else root.parent / "aip-v2-demo/services/aip-api/.env"
        if local.is_file():
            values = dotenv_values(local)
            password = values.get("AIP_ADMIN_PASSWORD")
            if password:
                os.environ["AIP_DEMO_PASSWORD"] = password
    os.environ.setdefault("AIP_DEMO_USER", "admin")
