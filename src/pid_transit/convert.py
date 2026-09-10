"""One-call conversions between the feed formats the adapters support.

Import into a database, then export from it, is the general path: it leaves a
database behind, which is what you want when the feed is also going to be
validated, merged or queried. Converting a *file into another file* is the
common special case of that pipeline, and this module is that case spelled out
-- the intermediate database lives in memory unless a path is asked for.

    from pid_transit import gtfs_to_netex
    result = gtfs_to_netex("feed.zip")          # writes feed.netex.xml

Nothing here re-implements a conversion: it wires the existing importers and
exporters together and reports what each of them said it lost.
"""

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Union

PathLike = Union[str, Path]

#: Formats that can be read into the model.
READ_FORMATS = ("gtfs", "netex", "csv", "xlsx")
#: Formats that can be written out of it.
WRITE_FORMATS = ("gtfs", "netex")

# What a derived target file is called. The format is part of the name so that
# converting `feed.zip` (a NeTEx archive) to GTFS cannot land on `feed.zip`.
_TARGET_SUFFIX = {"gtfs": ".gtfs.zip", "netex": ".netex.xml"}

# Files no GTFS archive is without. Used to tell a GTFS .zip from a NeTEx .zip
# by what the archive contains, rather than by what it is called.
_GTFS_MARKERS = frozenset({
    "agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt",
})


@dataclass(frozen=True)
class ConversionResult:
    """What a conversion produced, and what did not survive it.

    ``not_imported`` is what the source file held and the model could not read
    (the importers' ``skipped_`` / ``dropped_`` counts); ``not_representable``
    is what the model held and the target profile has no way to say (the NeTEx
    exporter's ``unsupported_`` counts). Both are empty when nothing was lost,
    so ``has_losses`` is the one thing a caller has to check.
    """

    source: Path
    target: Path
    source_format: str
    target_format: str
    imported: Dict[str, int] = field(default_factory=dict)
    not_imported: Dict[str, int] = field(default_factory=dict)
    not_representable: Dict[str, int] = field(default_factory=dict)
    db: Optional[Path] = None

    @property
    def has_losses(self) -> bool:
        """True if the source or the target profile could not carry something."""
        return bool(self.not_imported or self.not_representable)


# -- Format resolution -------------------------------------------------------

def detect_source_format(source: PathLike) -> str:
    """Name the format of ``source``, or raise ``ValueError`` if it is unclear.

    A ``.zip`` is decided by opening it and looking at its members -- both GTFS
    and NeTEx ship inside one, and the extension says nothing about which.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"No such feed: {path}")

    if path.is_dir():
        if any(path.rglob("*.xml")):
            return "netex"
        if any((path / marker).exists() for marker in _GTFS_MARKERS):
            raise ValueError(
                f"{path} looks like an unpacked GTFS feed; the GTFS importer "
                f"reads the .zip archive, so zip it first"
            )
        if any(path.glob("*.csv")):
            return "csv"
        raise ValueError(
            f"Cannot tell what format {path} is: no .xml and no .csv in it"
        )

    name = path.name.lower()
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".xml") or name.endswith(".xml.gz"):
        return "netex"
    if name.endswith(".zip"):
        return _detect_zip_format(path)
    if name.endswith(".csv"):
        raise ValueError(
            f"The csv importer reads a directory of tables, not a single file "
            f"({path}); pass its parent directory"
        )
    raise ValueError(f"Cannot tell what format {path} is; pass source_format=")


def _detect_zip_format(path: Path) -> str:
    """Decide between a GTFS and a NeTEx archive by what it contains."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as e:
        raise ValueError(f"{path} is not a readable zip archive: {e}") from e

    # Counted, not first-match: real archives carry strays -- a NeTEx zip with
    # a stray notes.txt is still NeTEx, a GTFS zip with a stray README.xml is
    # still GTFS.
    gtfs = sum(1 for n in names if Path(n).name.lower() in _GTFS_MARKERS)
    netex = sum(1 for n in names if n.lower().endswith(".xml"))
    if gtfs > netex:
        return "gtfs"
    if netex > gtfs:
        return "netex"
    raise ValueError(
        f"Cannot tell whether {path} is GTFS or NeTEx from its contents; "
        f"pass source_format="
    )


def detect_target_format(target: PathLike) -> str:
    """Name the format to write into ``target``, from its extension.

    Unlike a source, a target does not exist yet: the name is all there is.
    """
    name = Path(target).name.lower()
    if name.endswith(".zip"):
        return "gtfs"
    if name.endswith(".xml") or name.endswith(".xml.gz"):
        return "netex"
    raise ValueError(
        f"Cannot tell what format to write into {target}; pass target_format="
    )


def _base_name(path: Path) -> str:
    """``feed.xml.gz`` -> ``feed``; a directory keeps its own name."""
    name = path.name
    if name.lower().endswith(".gz"):
        name = name[: -len(".gz")]
    stripped = Path(name)
    return stripped.stem if stripped.suffix else stripped.name


def _derive_target(source: Path, target_format: str) -> Path:
    return source.parent / (_base_name(source) + _TARGET_SUFFIX[target_format])


# -- Adapter construction ----------------------------------------------------

def make_importer(fmt: str, *, fallback_timezone: str = "UTC",
                  profile: Optional[str] = None):
    """Build the importer for ``fmt``. ``profile`` forces a NeTEx dialect."""
    if fmt == "gtfs":
        from .adapters.gtfs_importer import GtfsImporter
        return GtfsImporter(fallback_timezone=fallback_timezone)
    if fmt == "netex":
        from .adapters.netex_importer import NetexImporter
        return NetexImporter(fallback_timezone=fallback_timezone, profile=profile)
    if fmt in ("csv", "xlsx"):
        from .adapters.spreadsheet_importer import SpreadsheetImporter
        return SpreadsheetImporter(format=fmt)
    raise ValueError(
        f"Unknown import format: {fmt!r} (expected {', '.join(READ_FORMATS)})"
    )


def make_exporter(fmt: str, *, profile: Optional[str] = None,
                  include_shapes: bool = True,
                  deduplicate_patterns: bool = True,
                  compress: bool = False):
    """Build the exporter for ``fmt``. ``profile`` picks the NeTEx dialect."""
    if fmt == "gtfs":
        from .adapters.gtfs_exporter import GtfsExporter
        return GtfsExporter(include_shapes=include_shapes)
    if fmt == "netex":
        from .adapters.netex_exporter import NetexExporter
        # profile=None already means EPIP to make_export_profile.
        return NetexExporter(profile=profile,
                             deduplicate_patterns=deduplicate_patterns,
                             compress=compress)
    raise ValueError(
        f"Unknown export format: {fmt!r} (expected {', '.join(WRITE_FORMATS)})"
    )


# -- The conversion itself ---------------------------------------------------

def convert(
    source: PathLike,
    target: Optional[PathLike] = None,
    *,
    source_format: Optional[str] = None,
    target_format: Optional[str] = None,
    db: PathLike = ":memory:",
    fallback_timezone: str = "UTC",
    read_profile: Optional[str] = None,
    write_profile: Optional[str] = None,
    include_shapes: bool = True,
    deduplicate_patterns: bool = True,
    compress: Optional[bool] = None,
) -> ConversionResult:
    """Convert one feed into another, through the Transmodel model.

    Args:
        source: The feed to read (file, archive or directory).
        target: Where to write. Defaults to ``<source>.<format>`` beside the
            source, which needs ``target_format`` to be known.
        source_format / target_format: Named, or detected from the file when
            omitted -- see :func:`detect_source_format`.
        db: Path for the intermediate database; in memory by default, so
            nothing is left on disk. Pass a path to keep it.
        fallback_timezone: Timezone for feeds that declare none.
        read_profile: Force a NeTEx dialect for reading instead of detecting
            one from the document's shape.
        write_profile: NeTEx profile to write (``"epip"`` default, ``"pt"``).
        include_shapes: Write ``shapes.txt`` into a GTFS target.
        deduplicate_patterns: Collapse identical journey patterns in NeTEx.
        compress: Gzip the NeTEx output. ``None`` (the default) means "compress
            if the target is named ``.gz``".

    Returns:
        A :class:`ConversionResult`. What was lost on the way in and on the way
        out travels in it, counted, rather than only in the log.
    """
    from .core.dataset import TransitDataset
    from .adapters.netex_importer import LOSS_PREFIXES

    src = Path(source)
    source_format = source_format or detect_source_format(src)
    if source_format not in READ_FORMATS:
        raise ValueError(f"Unknown import format: {source_format!r} "
                         f"(expected {', '.join(READ_FORMATS)})")

    if target is None:
        if target_format is None:
            raise ValueError("Pass a target path, a target_format, or both")
        dst = _derive_target(src, target_format)
    else:
        dst = Path(target)
        target_format = target_format or detect_target_format(dst)
    if target_format not in WRITE_FORMATS:
        raise ValueError(f"Unknown export format: {target_format!r} "
                         f"(expected {', '.join(WRITE_FORMATS)})")

    if dst.exists() and src.resolve() == dst.resolve():
        raise ValueError(f"Target would overwrite the source ({src})")

    # The NeTEx exporter appends .gz itself, so a target already named .gz is
    # handed over without it -- otherwise the file ends up feed.xml.gz.gz.
    if compress is None:
        compress = target_format == "netex" and dst.name.lower().endswith(".gz")
    write_to = dst
    if target_format == "netex" and compress:
        if dst.name.lower().endswith(".gz"):
            write_to = dst.with_name(dst.name[: -len(".gz")])
        else:
            dst = dst.with_name(dst.name + ".gz")

    importer = make_importer(source_format, fallback_timezone=fallback_timezone,
                             profile=read_profile)
    exporter = make_exporter(target_format, profile=write_profile,
                             include_shapes=include_shapes,
                             deduplicate_patterns=deduplicate_patterns,
                             compress=compress)

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Context-managed: on Windows an open connection keeps the .db file locked.
    with TransitDataset(db) as dataset:
        stats = dataset.import_data(importer, src)
        losses = dataset.export_data(exporter, write_to) or {}

    return ConversionResult(
        source=src,
        target=dst,
        source_format=source_format,
        target_format=target_format,
        imported={k: v for k, v in stats.items()
                  if not k.startswith(LOSS_PREFIXES)},
        not_imported={k: v for k, v in stats.items()
                      if k.startswith(LOSS_PREFIXES)},
        not_representable=dict(losses),
        db=None if str(db) == ":memory:" else Path(db),
    )


def gtfs_to_netex(
    source: PathLike,
    target: Optional[PathLike] = None,
    *,
    db: PathLike = ":memory:",
    profile: Optional[str] = None,
    fallback_timezone: str = "UTC",
    deduplicate_patterns: bool = True,
    compress: Optional[bool] = None,
) -> ConversionResult:
    """Convert a GTFS archive into a NeTEx document.

    ``target`` defaults to ``<source>.netex.xml`` beside the source.
    ``profile="pt"`` writes the Portuguese national profile instead of EPIP;
    what that profile cannot carry comes back in ``not_representable``.
    """
    return convert(
        source, target,
        source_format="gtfs", target_format="netex",
        db=db, fallback_timezone=fallback_timezone,
        write_profile=profile, deduplicate_patterns=deduplicate_patterns,
        compress=compress,
    )


def netex_to_gtfs(
    source: PathLike,
    target: Optional[PathLike] = None,
    *,
    db: PathLike = ":memory:",
    profile: Optional[str] = None,
    fallback_timezone: str = "UTC",
    include_shapes: bool = True,
) -> ConversionResult:
    """Convert a NeTEx document (file, directory or archive) into a GTFS zip.

    ``target`` defaults to ``<source>.gtfs.zip`` beside the source. ``profile``
    forces a NeTEx dialect for reading instead of detecting one from the
    document's shape; what the importer could not read comes back in
    ``not_imported``.
    """
    return convert(
        source, target,
        source_format="netex", target_format="gtfs",
        db=db, fallback_timezone=fallback_timezone,
        read_profile=profile, include_shapes=include_shapes,
    )
