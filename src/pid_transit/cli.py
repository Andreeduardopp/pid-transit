"""
PID-Transit Command-Line Interface.
"""

import argparse
import json
import sys
from pathlib import Path


def cmd_import(args):
    from .core.dataset import TransitDataset
    from .convert import make_importer

    try:
        importer = make_importer(args.format, fallback_timezone=args.timezone,
                                 profile=getattr(args, "profile", None))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    dataset = TransitDataset(args.db)
    stats = dataset.import_data(importer, args.source)
    from .adapters.netex_importer import LOSS_PREFIXES

    imported = {k: v for k, v in stats.items() if not k.startswith(LOSS_PREFIXES)}
    lost = {k: v for k, v in stats.items() if k.startswith(LOSS_PREFIXES)}
    print("Import complete:")
    for table, count in sorted(imported.items()):
        print(f"  {table}: {count:,}")
    if lost:
        # Printed apart from the table counts because it is the opposite
        # claim: these are things the file contained and the database
        # does not.
        print("Not imported:")
        for reason, count in sorted(lost.items()):
            print(f"  {reason}: {count:,}")


def _print_export_losses(losses: dict) -> None:
    """Print what an export could not say, split by why it could not.

    Two lists, because a reader has two different things to do about them. A
    profile with no slot for something is answered by choosing another profile;
    a datum that would not fit a slot the profile does have is answered by
    fixing the feed. One list would flatten the difference away.
    """
    from .adapters.netex_export_profiles import DROPPED_PREFIX

    dropped = {k: v for k, v in losses.items() if k.startswith(DROPPED_PREFIX)}
    unsupported = {k: v for k, v in losses.items() if k not in dropped}
    for title, group in (
        ("Not representable in this profile:", unsupported),
        ("Dropped (the profile has the slot; this data did not fit it):", dropped),
    ):
        if group:
            print(title)
            for what, count in sorted(group.items()):
                print(f"  {what}: {count:,}")


def cmd_export(args):
    from .core.dataset import TransitDataset
    from .convert import make_exporter

    try:
        exporter = make_exporter(args.format,
                                 profile=getattr(args, "profile", None),
                                 include_shapes=not args.no_shapes,
                                 deduplicate_patterns=not args.no_dedup,
                                 compress=args.compress)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    dataset = TransitDataset(args.db)
    losses = dataset.export_data(exporter, args.output)
    print(f"Exported to {args.output}")
    if losses:
        # Printed apart, like the importer's "Not imported": these are things
        # the database holds and the document does not say.
        _print_export_losses(losses)


def cmd_convert(args):
    """Convert a feed file straight into another format.

    `import` then `export` with the database in memory, so a one-off conversion
    does not leave a .db behind unless --db asks for one.
    """
    from .convert import convert

    if not args.output and not args.target_format:
        # Caught here rather than in convert(), so the message names the flags
        # the user typed instead of the library's keyword arguments.
        print("Pass --output, --to, or both", file=sys.stderr)
        sys.exit(1)

    try:
        result = convert(
            args.source,
            args.output,
            source_format=args.source_format,
            target_format=args.target_format,
            db=args.db or ":memory:",
            fallback_timezone=args.timezone,
            read_profile=args.read_profile,
            write_profile=args.profile,
            include_shapes=not args.no_shapes,
            deduplicate_patterns=not args.no_dedup,
            # None lets the target's own name decide; the flag forces it on.
            compress=True if args.compress else None,
        )
    except (ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"Converted {result.source} ({result.source_format}) -> "
          f"{result.target} ({result.target_format})")
    if result.db is not None:
        print(f"Intermediate database kept at {result.db}")
    for table, count in sorted(result.imported.items()):
        print(f"  {table}: {count:,}")
    if result.not_imported:
        # Two different losses, printed apart: what the source file held and
        # the model could not read...
        print("Not imported:")
        for reason, count in sorted(result.not_imported.items()):
            print(f"  {reason}: {count:,}")
    if result.not_representable:
        # ...and what the model held and the target document does not say.
        _print_export_losses(result.not_representable)


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


def cmd_netex_check(args):
    """Check a NeTEx file against a profile, without importing it."""
    from .adapters.netex_check import check_netex

    try:
        report = check_netex(
            args.source,
            profile=args.profile,
            with_schema=args.xsd,
            with_readability=args.read,
            xsd_path=args.xsd_path,
            xsd_locate=args.xsd_locate,
            check_references=not args.no_refs,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_markdown())

    # Exit code reflects errors only. A file with info-level findings — which
    # is what the readability layer produces — is still conformant.
    sys.exit(0 if report.is_conformant else 1)


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


def cmd_rt_snapshot(args):
    """Fetch a GTFS-RT feed and (optionally) match it against a database."""
    from .adapters.gtfs_rt import fetch_snapshot, match_snapshot, snapshot_to_json
    from .core.exceptions import ImportFailedError

    headers = {}
    for h in args.header:
        if ":" not in h:
            print(f"Invalid --header (expected 'Name: value'): {h}", file=sys.stderr)
            sys.exit(1)
        name, _, value = h.partition(":")
        headers[name.strip()] = value.strip()

    try:
        snapshot = fetch_snapshot(args.source, headers=headers or None,
                                  timeout=args.timeout)
    except ImportFailedError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not args.db:
        # No database: just parse and describe the feed.
        if args.json:
            print(snapshot_to_json(snapshot))
        else:
            print(f"GTFS-RT feed parsed (version {snapshot.gtfs_realtime_version}, "
                  f"timestamp {snapshot.feed_timestamp}):")
            print(f"  trip updates: {len(snapshot.trip_updates):,}")
            print(f"  vehicle positions: {len(snapshot.vehicle_positions):,}")
            print(f"  alerts: {len(snapshot.alerts):,}")
        return

    from .core.dataset import TransitDataset
    with TransitDataset(args.db) as dataset:
        report = match_snapshot(snapshot, dataset)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_markdown())

    rate = report.match_rate
    sys.exit(0 if rate is None or rate >= args.min_match_rate else 1)


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
    # Local, like every other adapter import here: it keeps `--help` from
    # pulling the adapters in. This one is a plain enum module with no deps.
    from .adapters.netex_profiles import NetexProfile

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
    p_import.add_argument("--profile", default=None,
                          choices=[p.value for p in NetexProfile],
                          help="Force a NeTEx dialect instead of detecting one "
                               "from the document's shape (netex only)")
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
    p_export.add_argument("--profile", default="epip",
                          choices=["epip", "epip-stations", "pt"],
                          help="NeTEx profile to write (netex only): 'epip' "
                               "(default), 'epip-stations' to also write the "
                               "station structure, or 'pt' for the Portuguese "
                               "national profile")
    p_export.add_argument("--no-dedup", action="store_true",
                          help="Disable journey pattern deduplication in NeTEx")
    p_export.add_argument("--compress", action="store_true",
                          help="Gzip the NeTEx output")
    p_export.set_defaults(func=cmd_export)

    # convert
    p_convert = subparsers.add_parser(
        "convert",
        help="Convert a feed file directly into another format "
             "(import + export, without keeping a database)")
    p_convert.add_argument("source", help="Path to the feed to convert")
    p_convert.add_argument("--to", dest="target_format",
                           choices=["gtfs", "netex"],
                           help="Output format. Read from --output when "
                                "omitted; required if --output is too")
    p_convert.add_argument("--from", dest="source_format",
                           choices=["gtfs", "netex", "csv", "xlsx"],
                           help="Input format. Detected from the source when "
                                "omitted (a .zip by what it contains)")
    p_convert.add_argument("--output", "-o",
                           help="Output path (default: <source>.gtfs.zip or "
                                "<source>.netex.xml, beside the source)")
    p_convert.add_argument("--db",
                           help="Keep the intermediate database at this path "
                                "(it lives in memory by default)")
    p_convert.add_argument("--timezone", default="UTC", help="Fallback timezone")
    p_convert.add_argument("--read-profile", dest="read_profile", default=None,
                           choices=[p.value for p in NetexProfile],
                           help="Force a NeTEx dialect for reading instead of "
                                "detecting one from the document's shape")
    p_convert.add_argument("--profile", default=None,
                           choices=["epip", "epip-stations", "pt"],
                           help="NeTEx profile to write (netex output only): "
                                "'epip' (default), 'epip-stations' or 'pt'")
    p_convert.add_argument("--no-shapes", action="store_true",
                           help="Exclude shapes.txt from a GTFS output")
    p_convert.add_argument("--no-dedup", action="store_true",
                           help="Disable journey pattern deduplication in NeTEx")
    p_convert.add_argument("--compress", action="store_true",
                           help="Gzip the NeTEx output (implied by an output "
                                "named .gz)")
    p_convert.set_defaults(func=cmd_convert)

    # validate
    p_validate = subparsers.add_parser("validate", help="Run logical validation on a database")
    p_validate.add_argument("db", help="Path to SQLite database")
    p_validate.add_argument("--json", action="store_true",
                            help="Emit the report as JSON")
    p_validate.add_argument("--min-severity", choices=["error", "warning", "info"],
                            dest="min_severity",
                            help="Only report issues at or above this severity")
    p_validate.set_defaults(func=cmd_validate)

    # netex-check
    p_check = subparsers.add_parser(
        "netex-check",
        help="Check a NeTEx file against a profile without importing it")
    p_check.add_argument("source", help="Path to the NeTEx .xml (or .zip)")
    p_check.add_argument("--profile", default="epip", choices=["epip", "pt"],
                         help="Profile to check against (default epip)")
    p_check.add_argument("--xsd", action="store_true",
                         help="Also validate against the CEN XSD. Needs the "
                              "'xsd' extra (lxml) and is slow: 3-9 min for a "
                              "174 MB file")
    p_check.add_argument("--xsd-locate", dest="xsd_locate",
                         action="store_true",
                         help="Report a line number for each XSD finding. "
                              "Requires validating a built tree instead of "
                              "streaming, which is several times slower "
                              "(~56 min for a 174 MB file, measured once)")
    p_check.add_argument("--xsd-path", dest="xsd_path",
                         help="Path to NeTEx_publication.xsd (defaults to the "
                              "vendored CEN 1.3 tree)")
    p_check.add_argument("--read", action="store_true",
                         help="Also report what this library's importer could "
                              "not read out of the file")
    p_check.add_argument("--no-refs", action="store_true",
                         help="Skip dangling-reference checking (it holds every "
                              "declared and referenced id in memory)")
    p_check.add_argument("--json", action="store_true",
                         help="Emit the report as JSON")
    p_check.set_defaults(func=cmd_netex_check)

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

    # rt-snapshot
    p_rt_snap = subparsers.add_parser(
        "rt-snapshot",
        help="Fetch a GTFS-RT feed and match it against a database")
    p_rt_snap.add_argument("source", help="Feed URL or path to a .pb file")
    p_rt_snap.add_argument("--db", help="Database to match against "
                                        "(omit to just parse and summarize)")
    p_rt_snap.add_argument("--header", action="append", default=[],
                           metavar="'Name: value'",
                           help="HTTP header, e.g. an API key (repeatable)")
    p_rt_snap.add_argument("--timeout", type=float, default=30.0,
                           help="HTTP timeout in seconds (default 30)")
    p_rt_snap.add_argument("--json", action="store_true",
                           help="Emit the snapshot/report as JSON")
    p_rt_snap.add_argument("--min-match-rate", type=float, default=0.0,
                           dest="min_match_rate",
                           help="Exit 1 if the trip match rate falls below "
                                "this fraction (default 0 = never fail)")
    p_rt_snap.set_defaults(func=cmd_rt_snapshot)

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
