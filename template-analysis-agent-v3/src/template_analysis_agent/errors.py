"""Domain exceptions for the V3 analysis pipeline."""


class AnalysisAgentError(RuntimeError):
    """Base error for controlled analysis failures."""


class ConfigurationError(AnalysisAgentError):
    """Raised when a template, query, metric, or source profile is invalid."""


class RoutingError(AnalysisAgentError):
    """Raised when a request cannot be routed safely."""


class PlanError(AnalysisAgentError):
    """Raised when a routing decision cannot be compiled into a safe plan."""


class QueryError(AnalysisAgentError):
    """Raised when a controlled data query cannot be executed or normalized."""


class ExecutionError(AnalysisAgentError):
    """Raised when a deterministic analysis step is invalid."""


class ExpressionError(AnalysisAgentError):
    """Raised when an expression provider cannot produce structured output."""

