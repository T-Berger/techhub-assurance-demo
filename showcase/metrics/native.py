"""AIP native TRACE metric registrations; scoring runs in the metric worker."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator
from aip_core.schemas.capabilities import MetricDirection, ScorerContract
from aip_core.schemas.contracts import PartitionType, ReductionName, ResultOutcome
from aip_core.schemas.ops_registry import GdiSchemaRef
from aip_traces_core.base import TraceMetric, TraceMetricResult, TraceValidationError, handle_trace_metric_errors
from aip_traces_core.registry import FrozenMapping, TraceMetricSpec, register_trace_metric
from aip_traces_agent.schemas import CanonicalTrace, CanonicalSession, CanonicalSpan
from .checks import NAMES, VERSION, digest, evaluate, observation


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references_by_trace: dict[str, dict[str, Any]] = Field(min_length=1)
    references_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    annotations: dict[str, Any] | None = None

    @model_validator(mode="after")
    def verify_reference_hash(self):
        if digest(self.references_by_trace) != self.references_sha256:
            raise ValueError("References differ from the pinned source digest")
        return self


class PrivacyMetric(TraceMetric[CanonicalTrace, CanonicalSession, CanonicalSpan]):
    def __init__(self, name, config):
        self.name, self.config = name, config

    @handle_trace_metric_errors
    async def score_async(self, trace):
        reference = self.config.references_by_trace.get(trace.trace_id)
        if reference is None:
            raise TraceValidationError("No independent reference for trace", trace_id=trace.trace_id)
        try:
            result = evaluate(self.name, observation(trace), reference)
        except ValueError as error:
            raise TraceValidationError(str(error), trace_id=trace.trace_id) from error
        metadata = {**result, "source_manifest_sha256": self.config.source_manifest_sha256,
                    "references_sha256": self.config.references_sha256, "metric_version": VERSION}
        if result["status"] == "not_applicable":
            return TraceMetricResult(score=None, outcome=ResultOutcome.NOT_APPLICABLE, rationale=result["reason"], metadata=metadata)
        return TraceMetricResult(score=result["score"], rationale=result["reason"], metadata=metadata)


def factory(name):
    def get_metric(test_params=None, systems_params=None):
        params = (test_params or {}).get("metric_params", test_params or {})
        return PrivacyMetric(name, Config.model_validate(params))
    return get_metric


for name, display in NAMES.items():
    register_trace_metric(TraceMetricSpec(name=name, version=VERSION, display_name=display,
        description=display + "; deterministic actual response and tool evidence with independent order fixtures.",
        scorer_contract=ScorerContract.PER_ROW, direction=MetricDirection.HIGHER_IS_BETTER,
        accepts=(GdiSchemaRef(name="canonical_partition_v1"),), partition_types=(PartitionType.TRACE,),
        required_columns=(), required_kinds=(),
        admissible_reductions=FrozenMapping({PartitionType.TRACE: frozenset({ReductionName.MEAN})})), factory(name))
