"""
PID-Transit Command-Line Interface.
"""

import argparse
import json
import sys
from pathlib import Path


def cmd_import(args):
    from .core.dataset import TransitDataset

    dataset = TransitDataset(args.db)

    fmt = args.format
    if fmt == "gtfs":
        from .adapters.gtfs_importer import GtfsImporter
        importer = GtfsImporter(fallback_timezone=args.timezone)
    elif fmt == "netex":
        from .adapters.netex_importer import NetexImporter
        importer = NetexImporter(fallback_timezone=args.timezone)
    elif fmt == "csv":
        from .adapters.spreadsheet_importer import SpreadsheetImporter
        importer = SpreadsheetImporter(format="csv")
    elif fmt == "xlsx":
        from .adapters.spreadsheet_importer import SpreadsheetImporter
        importer = SpreadsheetImporter(format="xlsx")
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)

    stats = dataset.import_data(importer, args.source)
    print("Import complete:")
    for table, count in sorted(stats.items()):
        print(f"  {table}: {count:,}")


def cmd_export(args):
    from .core.dataset import TransitDataset

    dataset = TransitDataset(args.db)

    fmt = args.format
    if fmt == "gtfs":
        from .adapters.gtfs_exporter import GtfsExporter
        exporter = GtfsExporter(include_shapes=not args.no_shapes)
    elif fmt == "netex":
        from .adapters.netex_exporter import NetexExporter
        exporter = NetexExporter(
            deduplicate_patterns=not args.no_dedup,
            compress=args.compress,
        )
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)

    dataset.export_data(exporter, args.output)
    print(f"Exported to {args.output}")


def cmd_validate(args):
    from .core.dataset import TransitDataset
    from .core.validator import Severity

    dataset = TransitDataset(args.db)
    report = dataset.validate(min_severity=getattr(args, "min_severity", None))

    # Exit code reflects errors only — a feed with warnings/info is still valid.
    errors = [i for i in report.issues if i.severity == Severity.ERROR]

    if getattr(args, "json", False):
        print(json.dumps({
            "is_valid": len(errors) == 0,
            "issue_count": len(report.issues),
            "issues": [
                {"severity": i.severity.value, "entity_type": i.entity_type,
                 "entity_id": i.entity_id, "issue_type": i.issue_type,
                 "message": i.message}
                for i in report.issues
            ],
        }, indent=2))
    elif not report.issues:
        print("Validation PASSED - no issues found.")
    else:
        status = "PASSED" if not errors else "FAILED"
        print(f"Validation {status} - {len(report.issues)} issue(s) "
              f"({len(errors)} error(s)):")
        for issue in report.issues:
            print(f"  [{issue.severity.value.upper()}] "
                  f"[{issue.entity_type}] {issue.entity_id}: {issue.message}")

    sys.exit(0 if not errors else 1)


def cmd_stats(args):
    from .core.dataset import TransitDataset
    from .analytics.statistics import TransitStatistics

    dataset = TransitDataset(args.db)
    stats = TransitStatistics(dataset)

    if getattr(args, "json", False):
        if args.line:
            out = {"line": args.line, "service_span": stats.service_span(line_id=args.line)}
            if args.day_type:
                out["headways"] = stats.headways(args.line, args.day_type)
        else:
            out = stats.summary()
            if args.day_type:
                out["vehicle_hours"] = stats.vehicle_hours(day_type_id=args.day_type)
        print(json.dumps(out, indent=2))
        return

    if args.line:
        print(f"--- Statistics for line {args.line} ---")
        spans = stats.service_span(line_id=args.line)
        if args.line in spans:
            for dt_id, span in spans[args.line].items():
                print(f"  {dt_id}: {span['first']} - {span['last']}")
                if args.day_type and dt_id == args.day_type:
                    hw = stats.headways(args.line, dt_id)
                    if hw["avg"] is not None:
                        print(f"    Headway avg: {hw['avg']:.0f}s, min: {hw['min']}s, max: {hw['max']}s")
                        if hw["peak_avg"] is not None:
                            print(f"    Peak avg: {hw['peak_avg']:.0f}s")
                        if hw["offpeak_avg"] is not None:
                            print(f"    Off-peak avg: {hw['offpeak_avg']:.0f}s")
        else:
            print(f"  No service journeys found for line {args.line}")
    else:
        s = stats.summary()
        print("--- Dataset Summary ---")
        for key in ("operators", "lines", "stops", "day_types",
                    "journey_patterns", "service_journeys", "passing_times",
                    "shapes", "frequencies", "transfers"):
            print(f"  {key}: {s[key]:,}")
        print()
        print("--- Service Balance ---")
        for dt_id, bal in s["service_balance"].items():
            print(f"  {dt_id}: {bal['journey_count']:,} journeys, {bal['line_count']} lines")

        if args.day_type:
            vh = stats.vehicle_hours(day_type_id=args.day_type)
            print(f"\n  Vehicle-hours ({args.day_type}): {vh:,.1f}")


def cmd_roundtrip(args):
    """Import -> export -> re-import a feed and diff the two databases.

    Exit code 0 means the feed survives the pipeline losslessly (the
    export∘import ≅ id property of docs/formalization.md §1 holds for it).
    """
    import tempfile
    from .core.dataset import TransitDataset
    from .analytics.diff import FeedDiffer

    fmt = args.format
    if fmt == "gtfs":
        from .adapters.gtfs_importer import GtfsImporter
        from .adapters.gtfs_exporter import GtfsExporter
        def make_importer():
            return GtfsImporter(fallback_timezone=args.timezone)
        exporter = GtfsExporter()
        suffix = ".zip"
    elif fmt == "netex":
        from .adapters.netex_importer import NetexImporter
        from .adapters.netex_exporter import NetexExporter
        def make_importer():
            return NetexImporter(fallback_timezone=args.timezone)
        # Dedup rewrites pattern ids; strict identity needs it off.
        exporter = NetexExporter(deduplicate_patterns=False)
        suffix = ".xml"
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)

    first = TransitDataset(":memory:")
    second = TransitDataset(":memory:")

    first.import_data(make_importer(), args.source)
    with tempfile.TemporaryDirectory() as tmp_dir:
        exported = Path(tmp_dir) / f"roundtrip{suffix}"
        first.export_data(exporter, exported)
        second.import_data(make_importer(), exported)

    report = FeedDiffer(first, second).diff()

    if args.output:
        Path(args.output).write_text(report.to_markdown())
        print(f"Diff report written to {args.output}")

    if report.has_changes:
        print("Round trip is LOSSY - differences after import -> export -> import:")
        for ed in report.entity_diffs:
            if ed.has_changes:
                print(f"  {ed.entity_type}: +{len(ed.added)} "
                      f"-{len(ed.removed)} ~{len(ed.modified)}")
        sys.exit(1)

    print("Round trip is LOSSLESS - re-imported database is identical.")
    sys.exit(0)


def cmd_merge(args):
    """Merge one or more source databases into a target with ID namespacing."""
    from .core.dataset import TransitDataset
    from .analytics.merge import FeedMerger

    if len(args.source) != len(args.namespace):
        print("Each --source must be paired with a --namespace.", file=sys.stderr)
        sys.exit(1)

    target = TransitDataset(args.db)
    merger = FeedMerger(target, namespace_separator=args.separator)
    threshold = args.distance if args.dedupe_stops else None

    for src_path, namespace in zip(args.source, args.namespace):
        source = TransitDataset(src_path)
        stats = merger.merge(source, namespace, merge_stops_threshold_m=threshold)
        source.close()
        print(f"Merged {src_path} as '{namespace}':")
        for table, count in sorted(stats.items()):
            print(f"  {table}: {count:,}")
    target.close()


def cmd_rt_readiness(args):
    """Check whether a dataset is ready to back a GTFS-RT feed."""
    from .core.dataset import TransitDataset
    from .analytics.rt_readiness import check_rt_readiness

    dataset = TransitDataset(args.db)
    report = check_rt_readiness(dataset)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_markdown())

    sys.exit(0 if report.is_ready else 1)


def cmd_normalize(args):
    """Collapse journey patterns to the canonical §2 quotient in place."""
    from .core.dataset import TransitDataset

    dataset = TransitDataset(args.db)
    try:
        result = dataset.normalize_patterns()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        pass

    print("Pattern normalization complete:")
    print(f"  patterns: {result['patterns_before']:,} -> {result['patterns_after']:,}")
    print(f"  journeys remapped: {result['journeys_remapped']:,}")
    print(f"  patterns removed: {result['patterns_removed']:,}")
    print(f"  points removed: {result['points_removed']:,}")
    dataset.close()


def cmd_frequencies(args):
    """Expand frequency windows to concrete journeys, or compress them back."""
    from .core.dataset import TransitDataset

    dataset = TransitDataset(args.db)
    try:
        if args.expand:
            r = dataset.expand_frequencies()
            print("Frequency expansion complete:")
            print(f"  frequencies expanded: {r['frequencies_expanded']:,}")
            print(f"  journeys created: {r['journeys_created']:,}")
            print(f"  passing times created: {r['passing_times_created']:,}")
        else:
            r = dataset.compress_to_frequencies(min_run=args.min_run)
            print("Frequency compression complete:")
            print(f"  runs compressed: {r['runs_compressed']:,}")
            print(f"  journeys removed: {r['journeys_removed']:,}")
            print(f"  frequencies created: {r['frequencies_created']:,}")
    finally:
        dataset.close()


def cmd_diff(args):
    from .core.dataset import TransitDataset
    from .analytics.diff import FeedDiffer

    base = TransitDataset(args.base)
    target = TransitDataset(args.target)

    report = FeedDiffer(base, target).diff()

    if args.output:
        output_path = Path(args.output)
        if output_path.suffix == ".json":
            output_path.write_text(json.dumps(report.to_dict(), indent=2))
        else:
            output_path.write_text(report.to_markdown())
        print(f"Diff report written to {args.output}")
    else:
        print(report.to_markdown())


def main():
    parser = argparse.ArgumentParser(
        prog="pid-transit",
        description="PID-Transit: Transit data management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # import
    p_import = subparsers.add_parser("import", help="Import transit data into a database")
    p_import.add_argument("source", help="Path to source file or directory")
    p_import.add_argument("--format", "-f", required=True,
                          choices=["gtfs", "netex", "csv", "xlsx"],
                          help="Input format")
    p_import.add_argument("--db", required=True, help="Path to SQLite database")
    p_import.add_argument("--timezone", default="UTC", help="Fallback timezone")
    p_import.set_defaults(func=cmd_import)

    # export
    p_export = subparsers.add_parser("export", help="Export database to a transit format")
    p_export.add_argument("--db", required=True, help="Path to SQLite database")
    p_export.add_argument("--format", "-f", required=True,
                          choices=["gtfs", "netex"],
                          help="Output format")
    p_export.add_argument("--output", "-o", required=True, help="Output file path")
    p_export.add_argument("--no-shapes", action="store_true",
                          help="Exclude shapes.txt from GTFS export (included by default)")
    p_export.add_argument("--no-dedup", action="store_true",
                          help="Disable journey pattern deduplication in NeTEx")
    p_export.add_argument("--compress", action="store_true",
                          help="Gzip the NeTEx output")
    p_export.set_defaults(func=cmd_export)

    # validate
    p_validate = subparsers.add_parser("validate", help="Run logical validation on a database")
    p_validate.add_argument("db", help="Path to SQLite database")
    p_validate.add_argument("--json", action="store_true",
                            help="Emit the report as JSON")
    p_validate.add_argument("--min-severity", choices=["error", "warning", "info"],
                            dest="min_severity",
                            help="Only report issues at or above this severity")
    p_validate.set_defaults(func=cmd_validate)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show operational statistics")
    p_stats.add_argument("db", help="Path to SQLite database")
    p_stats.add_argument("--line", help="Filter by line ID")
    p_stats.add_argument("--day-type", help="Filter by day type ID")
    p_stats.add_argument("--json", action="store_true", help="Emit stats as JSON")
    p_stats.set_defaults(func=cmd_stats)

    # merge
    p_merge = subparsers.add_parser("merge", help="Merge source databases into a target")
    p_merge.add_argument("db", help="Target database path (created/updated in place)")
    p_merge.add_argument("--source", action="append", default=[], required=True,
                         help="Source database path (repeatable)")
    p_merge.add_argument("--namespace", action="append", default=[], required=True,
                         help="Namespace for the matching --source (repeatable)")
    p_merge.add_argument("--separator", default=":", help="Namespace separator")
    p_merge.add_argument("--dedupe-stops", action="store_true",
                         help="Merge nearby stops instead of duplicating them")
    p_merge.add_argument("--distance", type=float, default=50.0,
                         help="Stop dedup threshold in meters (with --dedupe-stops)")
    p_merge.set_defaults(func=cmd_merge)

    # rt-readiness
    p_rt_ready = subparsers.add_parser("rt-readiness",
                                       help="Check GTFS-RT readiness of a database")
    p_rt_ready.add_argument("db", help="Path to SQLite database")
    p_rt_ready.add_argument("--json", action="store_true", help="Emit the report as JSON")
    p_rt_ready.set_defaults(func=cmd_rt_readiness)

    # normalize
    p_norm = subparsers.add_parser(
        "normalize", help="Collapse duplicate journey patterns in place")
    p_norm.add_argument("db", help="Path to SQLite database")
    p_norm.add_argument("--patterns", action="store_true",
                        help="Normalize journey patterns (default operation)")
    p_norm.set_defaults(func=cmd_normalize)

    # frequencies
    p_freq = subparsers.add_parser(
        "frequencies", help="Expand or compress frequency-based service")
    p_freq.add_argument("db", help="Path to SQLite database")
    g_freq = p_freq.add_mutually_exclusive_group(required=True)
    g_freq.add_argument("--expand", action="store_true",
                        help="Materialize frequency windows into concrete journeys")
    g_freq.add_argument("--compress", action="store_true",
                        help="Collapse regular departure runs into frequencies")
    p_freq.add_argument("--min-run", type=int, default=3, dest="min_run",
                        help="Min departures in an arithmetic run to compress (default 3)")
    p_freq.set_defaults(func=cmd_frequencies)

    # roundtrip
    p_rt = subparsers.add_parser(
        "roundtrip",
        help="Check that a feed survives import -> export -> import losslessly")
    p_rt.add_argument("source", help="Path to the feed to check")
    p_rt.add_argument("--format", "-f", required=True,
                      choices=["gtfs", "netex"], help="Feed format")
    p_rt.add_argument("--timezone", default="UTC", help="Fallback timezone")
    p_rt.add_argument("--output", "-o", help="Write the diff report to a file")
    p_rt.set_defaults(func=cmd_roundtrip)

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two databases")
    p_diff.add_argument("base", help="Base database path")
    p_diff.add_argument("target", help="Target database path")
    p_diff.add_argument("--output", "-o", help="Output file (.md or .json)")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
