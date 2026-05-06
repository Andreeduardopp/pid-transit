"""PID-GTFS — Standalone GTFS schemas, ingestion, and extraction."""

from .database import (
    GtfsDatabase,
    InsertResult,
    IntegrityReport,
    IntegrityViolation,
    SCHEMA_VERSION,
)
from .importer import (
    GtfsImportError,
    GtfsImportResult,
    GtfsZipPreview,
    ImportMode,
    import_gtfs_zip,
    preview_gtfs_zip,
)
from .exporter import (
    ExportResult,
    FeedCompleteness,
    ValidationResult,
    compute_feed_completeness,
    export_gtfs_feed,
    validate_before_export,
)

# Friendlier top-level aliases
preview_zip = preview_gtfs_zip
import_zip = import_gtfs_zip
export_zip = export_gtfs_feed

__version__ = "0.1.0"

__all__ = [
    "GtfsDatabase",
    "InsertResult",
    "IntegrityReport",
    "IntegrityViolation",
    "SCHEMA_VERSION",
    "GtfsImportError",
    "GtfsImportResult",
    "GtfsZipPreview",
    "ImportMode",
    "import_gtfs_zip",
    "import_zip",
    "preview_gtfs_zip",
    "preview_zip",
    "ExportResult",
    "FeedCompleteness",
    "ValidationResult",
    "compute_feed_completeness",
    "export_gtfs_feed",
    "export_zip",
    "validate_before_export",
    "__version__",
]
