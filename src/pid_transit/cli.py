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

    dataset = TransitDataset(args.db)
    report = dataset.validate()

    if report.is_valid:
        print("Validation PASSED - no issues found.")
    else:
        print(f"Validation FAILED - {len(report.issues)} issue(s):")
        for issue in report.issues:
            print(f"  [{issue.entity_type}] {issue.entity_id}: {issue.message}")

    sys.exit(0 if report.is_valid else 1)


def cmd_stats(args):
    from .core.dataset import TransitDataset
    from .analytics.statistics import TransitStatistics

    dataset = TransitDataset(args.db)
    stats = TransitStatistics(dataset)

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
    p_validate.set_defaults(func=cmd_validate)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show operational statistics")
    p_stats.add_argument("db", help="Path to SQLite database")
    p_stats.add_argument("--line", help="Filter by line ID")
    p_stats.add_argument("--day-type", help="Filter by day type ID")
    p_stats.set_defaults(func=cmd_stats)

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
