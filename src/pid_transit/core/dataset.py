"""
Main Facade class for the PID-Transit library.
"""

import logging
from pathlib import Path
from typing import Union, Dict

from .database import TransmodelDatabase
from .repositories import (
    OperatorRepository,
    LineRepository,
    ScheduledStopPointRepository,
    LevelRepository,
    StopAreaRepository,
    PathwayRepository,
    DayTypeRepository,
    OperatingDayExceptionRepository,
    JourneyPatternRepository,
    PointInJourneyPatternRepository,
    ServiceJourneyRepository,
    PassingTimeRepository,
    FeedInfoRepository,
    ShapePointRepository,
    FrequencyRepository,
    TransferRepository,
    FareAttributeRepository,
    FareRuleRepository,
    TranslationRepository,
    AttributionRepository,
)
from .exceptions import ImportFailedError

logger = logging.getLogger(__name__)

class TransitDataset:
    """
    Central entry point for the PID-Transit library.
    
    Provides access to the underlying relational database and exposes
    data via Repository properties.
    """

    def __init__(self, db_path: Union[str, Path]):
        """Initialize the transit dataset linked to a SQLite database."""
        self.db = TransmodelDatabase(db_path)
        
        # Initialize repositories
        self.operators = OperatorRepository(self.db)
        self.lines = LineRepository(self.db)
        self.scheduled_stop_points = ScheduledStopPointRepository(self.db)
        self.levels = LevelRepository(self.db)
        self.stop_areas = StopAreaRepository(self.db)
        self.pathways = PathwayRepository(self.db)
        self.day_types = DayTypeRepository(self.db)
        self.operating_day_exceptions = OperatingDayExceptionRepository(self.db)
        self.journey_patterns = JourneyPatternRepository(self.db)
        self.points_in_journey_pattern = PointInJourneyPatternRepository(self.db)
        self.service_journeys = ServiceJourneyRepository(self.db)
        self.passing_times = PassingTimeRepository(self.db)
        self.feed_info = FeedInfoRepository(self.db)
        self.shape_points = ShapePointRepository(self.db)
        self.frequencies = FrequencyRepository(self.db)
        self.transfers = TransferRepository(self.db)
        self.fare_attributes = FareAttributeRepository(self.db)
        self.fare_rules = FareRuleRepository(self.db)
        self.translations = TranslationRepository(self.db)
        self.attributions = AttributionRepository(self.db)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying database connection."""
        self.db.close()

    def __enter__(self) -> "TransitDataset":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── Convenience constructors / exporters ─────────────────────────────────

    @classmethod
    def from_gtfs(
        cls,
        source: Union[str, Path],
        db: Union[str, Path] = ":memory:",
        **importer_kwargs,
    ) -> "TransitDataset":
        """Create a dataset and import a GTFS feed in one call.

        In-memory by default; pass ``db=`` for a file-backed database. Extra
        keyword arguments go to ``GtfsImporter`` (e.g. ``fallback_timezone``).
        """
        from ..adapters.gtfs_importer import GtfsImporter
        ds = cls(db)
        ds.import_data(GtfsImporter(**importer_kwargs), source)
        return ds

    @classmethod
    def from_netex(
        cls,
        source: Union[str, Path],
        db: Union[str, Path] = ":memory:",
        **importer_kwargs,
    ) -> "TransitDataset":
        """Create a dataset and import a NeTEx feed in one call."""
        from ..adapters.netex_importer import NetexImporter
        ds = cls(db)
        ds.import_data(NetexImporter(**importer_kwargs), source)
        return ds

    def to_gtfs(self, target: Union[str, Path], **exporter_kwargs) -> None:
        """Export this dataset to a GTFS zip."""
        from ..adapters.gtfs_exporter import GtfsExporter
        self.export_data(GtfsExporter(**exporter_kwargs), target)

    def to_netex(self, target: Union[str, Path], **exporter_kwargs):
        """Export this dataset to NeTEx XML.

        ``profile="pt"`` writes the Portuguese national profile instead of the
        default EPIP. Returns what the chosen profile could not represent, as
        ``{"unsupported_<entity>": count}`` — a restriction that cannot carry
        journey patterns says so here rather than dropping them quietly.
        """
        from ..adapters.netex_exporter import NetexExporter
        return self.export_data(NetexExporter(**exporter_kwargs), target)

    def import_data(self, importer, source: Union[str, Path]) -> Dict[str, int]:
        """
        Import data using a provided importer adapter.
        
        Args:
            importer: An instance of an Importer class (e.g., GtfsImporter)
            source: Path or file-like object to import.
            
        Returns:
            Dict mapping table names to number of inserted records.
        """
        try:
            return importer.import_to_db(self.db, source)
        except Exception as e:
            logger.error(f"Import failed: {e}")
            raise ImportFailedError(f"Import failed: {e}") from e

    def export_data(self, exporter, target: Union[str, Path]):
        """
        Export data using a provided exporter adapter.

        Args:
            exporter: An instance of an Exporter class (e.g., GtfsExporter)
            target: Path to save the exported data.

        Returns:
            Whatever the exporter reports it could not represent, as a dict of
            counts, or None for exporters that report nothing. The NeTEx
            exporter uses it for profile losses (``unsupported_`` keys).
        """
        return exporter.export_from_db(self.db, target)

    def validate(self, min_severity=None):
        """Run logical validation on the dataset.

        ``min_severity`` (a ``Severity`` or its string value) filters the
        returned issues; ``report.is_valid`` still reflects errors only.
        """
        from .validator import validate_transmodel, Severity
        if min_severity is None:
            return validate_transmodel(self.db)
        return validate_transmodel(self.db, Severity(min_severity))

    def normalize_patterns(self) -> Dict[str, int]:
        """Collapse journey patterns to the §2 quotient JP/∼ in place.

        Groups patterns by (line, direction, ordered stop sequence), keeps one
        canonical pattern per class, remaps ``service_journey.journey_pattern_id``
        to it, and deletes the redundant patterns and their points. This is the
        same quotient the NeTEx exporter's ``deduplicate_patterns`` applies, made
        an explicit, user-triggered operation on the database itself.

        Normalization is only safe when journeys actually follow their pattern
        (validator rule V17); if any journey violates that, this refuses to run
        and raises ``ValueError`` naming the offending journeys.

        Returns counts: ``{"patterns_before", "patterns_after", "journeys_remapped",
        "patterns_removed", "points_removed"}``.
        """
        from .patterns import build_pattern_dedup_map
        from .validator import validate_transmodel

        violations = [
            i.entity_id for i in validate_transmodel(self.db).issues
            if i.issue_type == "pattern_sequence_mismatch"
        ]
        if violations:
            preview = ", ".join(violations[:5])
            more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
            raise ValueError(
                "Cannot normalize patterns: journeys do not match their pattern "
                f"(V17): {preview}{more}"
            )

        remap, canonical_ids = build_pattern_dedup_map(self.db)
        patterns_before = len(remap)

        journeys_remapped = 0
        for sj in self.db.get_records("service_journey"):
            jp_id = sj.get("journey_pattern_id")
            if jp_id and remap.get(jp_id, jp_id) != jp_id:
                self.service_journeys.update(
                    sj["id"], {"journey_pattern_id": remap[jp_id]}
                )
                journeys_remapped += 1

        redundant = [jp_id for jp_id in remap if jp_id not in canonical_ids]
        points_removed = 0
        for jp_id in redundant:
            points_removed += self.db.delete(
                "point_in_journey_pattern", where={"journey_pattern_id": jp_id}
            )
            self.db.delete("journey_pattern", where={"id": jp_id})

        return {
            "patterns_before": patterns_before,
            "patterns_after": len(canonical_ids),
            "journeys_remapped": journeys_remapped,
            "patterns_removed": len(redundant),
            "points_removed": points_removed,
        }

    def expand_frequencies(self) -> Dict[str, int]:
        """Materialize every ``Frequency`` window into concrete ServiceJourneys.

        Each headway-based template journey becomes one journey per departure in
        ``[start, end)``, so every §3 statistic (vehicle-hours, headways, stop
        coverage) is computed over real departures instead of templates. In-place;
        the inverse of :meth:`compress_to_frequencies`.

        Returns ``{"frequencies_expanded", "journeys_created",
        "passing_times_created"}``.
        """
        from .frequencies import expand_frequencies
        return expand_frequencies(self.db)

    def compress_to_frequencies(self, min_run: int = 3) -> Dict[str, int]:
        """Collapse regular departure progressions back into ``Frequency`` rows.

        Within each ``(journey_pattern, day_type, timing-profile)`` class, maximal
        arithmetic runs of at least ``min_run`` departures are replaced by one
        template journey plus a frequency (``exact_times = 1``). The inverse of
        :meth:`expand_frequencies`: ``expand(compress(feed))`` reproduces the
        original departures exactly.

        Returns ``{"runs_compressed", "journeys_removed", "frequencies_created"}``.
        """
        from .frequencies import compress_to_frequencies
        return compress_to_frequencies(self.db, min_run=min_run)
