"""Standard native AIP worker invocation and persisted Parquet result path."""
import os
from . import native
from aip_traces_agent.nuclio_runner import handle as native_handle


def handle(payload, context):
    name = os.environ["OP_NAME"]
    if name not in native.NAMES:
        raise ValueError("Worker refuses undeclared metric")
    return native_handle(payload, context, op_name=name)
