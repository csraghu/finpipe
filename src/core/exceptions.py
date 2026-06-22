class FinpipeError(Exception):
    """Base exception for all finpipe errors."""


class FinpipeRateLimitExceededError(FinpipeError):
    """Raised when rate limits are exhausted after retries."""


class FinpipeDataNotFoundError(FinpipeError):
    """Raised when a requested resource is not found by the provider."""


class FinpipeProviderDownError(FinpipeError):
    """Raised when a provider is unresponsive or returning server errors."""


class FinpipeConfigError(FinpipeError):
    """Raised when configuration is missing or invalid."""


class FinpipeParseError(FinpipeError):
    """Raised when a provider payload cannot be parsed."""
