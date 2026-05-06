"""
Custom exception hierarchy for the PID-Transit library.
"""

class TransitError(Exception):
    """Base exception for all PID-Transit errors."""
    pass

class ImportFailedError(TransitError):
    """Raised when an adapter fails to parse or import a transit feed."""
    pass

class ExportFailedError(TransitError):
    """Raised when an adapter fails to export data to a target format."""
    pass

class ValidationError(TransitError):
    """Raised when transit data violates Transmodel logical consistency rules."""
    pass

class EntityNotFoundError(TransitError):
    """Raised when a queried entity is not found in the repository."""
    pass
