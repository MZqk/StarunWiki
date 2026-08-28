class StarunWikiError(RuntimeError):
    """Base error for configuration and policy failures (exit code 2)."""


class IntegrityError(StarunWikiError):
    """An immutable release or checksum contract is invalid (exit code 3)."""


class ExternalServiceError(StarunWikiError):
    """An external command or service failed (exit code 4)."""
