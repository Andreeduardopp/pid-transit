"""Tests for pid_transit.convert — one-call file-to-file conversions."""

import gzip
import zipfile

import pytest

from pid_transit.convert import (
    ConversionResult,
    convert,
    detect_source_format,
    detect_target_format,
    gtfs_to_netex,
    make_exporter,
    make_importer,
    netex_to_gtfs,
)
from pid_transit.core.dataset import TransitDataset
from tests.conftest import make_gtfs_zip


@pytest.fixture
def gtfs_zip(minimal_gtfs_records, tmp_path):
    path = tmp_path / "feed.zip"
    path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
    return path


@pytest.fixture
def netex_xml(gtfs_zip, tmp_path):
    return gtfs_to_netex(gtfs_zip, tmp_path / "source.xml").target


class TestGtfsToNetex:
    def test_derives_a_target_beside_the_source(self, gtfs_zip):
        result = gtfs_to_netex(gtfs_zip)

        assert result.target == gtfs_zip.parent / "feed.netex.xml"
        assert result.target.exists()
        assert "<PublicationDelivery" in result.target.read_text(encoding="utf-8")

    def test_reports_what_it_carried(self, gtfs_zip, tmp_path):
        result = gtfs_to_netex(gtfs_zip, tmp_path / "out.xml")

        assert isinstance(result, ConversionResult)
        assert (result.source_format, result.target_format) == ("gtfs", "netex")
        assert result.imported["line"] == 1
        assert result.imported["scheduled_stop_point"] == 2
        assert not result.has_losses

    def test_leaves_no_database_behind(self, gtfs_zip, tmp_path):
        result = gtfs_to_netex(gtfs_zip, tmp_path / "out.xml")

        assert result.db is None
        assert list(tmp_path.glob("*.db")) == []

    def test_db_argument_keeps_the_intermediate_database(self, gtfs_zip, tmp_path):
        db_path = tmp_path / "kept.db"
        result = gtfs_to_netex(gtfs_zip, tmp_path / "out.xml", db=db_path)

        assert result.db == db_path
        with TransitDataset(db_path) as ds:
            assert ds.db.count("line") == 1

    def test_pt_profile_reports_what_it_could_not_carry(self, gtfs_zip, tmp_path):
        # GTFS synthesises one JourneyPattern per trip and the PT profile has
        # nowhere to put them; the loss must reach the caller as a number.
        result = gtfs_to_netex(gtfs_zip, tmp_path / "pt.xml", profile="pt",
                               deduplicate_patterns=False)

        assert result.not_representable["unsupported_journey_pattern"] == 1
        assert result.has_losses

    def test_gz_target_is_written_once(self, gtfs_zip, tmp_path):
        # The exporter appends .gz itself; a target already named .gz must not
        # come out as out.xml.gz.gz.
        result = gtfs_to_netex(gtfs_zip, tmp_path / "out.xml.gz")

        assert result.target == tmp_path / "out.xml.gz"
        assert result.target.exists()
        assert not (tmp_path / "out.xml.gz.gz").exists()
        with gzip.open(result.target, "rt", encoding="utf-8") as f:
            assert "<PublicationDelivery" in f.read()

    def test_compress_flag_names_the_file_it_actually_wrote(self, gtfs_zip, tmp_path):
        result = gtfs_to_netex(gtfs_zip, tmp_path / "out.xml", compress=True)

        assert result.target == tmp_path / "out.xml.gz"
        assert result.target.exists()


class TestNetexToGtfs:
    def test_derives_a_target_beside_the_source(self, netex_xml):
        result = netex_to_gtfs(netex_xml)

        assert result.target == netex_xml.parent / "source.gtfs.zip"
        with zipfile.ZipFile(result.target) as zf:
            assert "stops.txt" in zf.namelist()

    def test_round_trip_through_both_helpers_keeps_the_line(self, gtfs_zip, tmp_path):
        gtfs_to_netex(gtfs_zip, tmp_path / "mid.xml")
        result = netex_to_gtfs(tmp_path / "mid.xml", tmp_path / "back.zip")

        with zipfile.ZipFile(result.target) as zf:
            routes = zf.read("routes.txt").decode("utf-8")
        assert "R1" in routes
        assert result.imported["service_journey"] == 1

    def test_no_shapes_omits_the_file(self, netex_xml, tmp_path):
        result = netex_to_gtfs(netex_xml, tmp_path / "out.zip", include_shapes=False)

        with zipfile.ZipFile(result.target) as zf:
            assert "shapes.txt" not in zf.namelist()


class TestConvert:
    def test_detects_both_ends_when_neither_is_named(self, gtfs_zip, tmp_path):
        result = convert(gtfs_zip, tmp_path / "out.xml")

        assert (result.source_format, result.target_format) == ("gtfs", "netex")

    def test_target_format_alone_derives_the_path(self, gtfs_zip):
        result = convert(gtfs_zip, target_format="netex")

        assert result.target == gtfs_zip.parent / "feed.netex.xml"

    def test_netex_to_netex_rewrites_the_profile(self, netex_xml, tmp_path):
        result = convert(netex_xml, tmp_path / "pt.xml", write_profile="pt",
                         deduplicate_patterns=False)

        assert "<Authority" in result.target.read_text(encoding="utf-8")
        assert result.not_representable["unsupported_journey_pattern"] == 1

    def test_creates_a_missing_output_directory(self, gtfs_zip, tmp_path):
        result = convert(gtfs_zip, tmp_path / "nested" / "deep" / "out.xml")

        assert result.target.exists()

    def test_a_target_with_no_path_and_no_format_is_refused(self, gtfs_zip):
        with pytest.raises(ValueError, match="target path, a target_format"):
            convert(gtfs_zip)

    def test_refuses_to_overwrite_the_source(self, netex_xml):
        with pytest.raises(ValueError, match="overwrite the source"):
            convert(netex_xml, netex_xml, target_format="netex")

    def test_unknown_formats_are_named_in_the_error(self, gtfs_zip, tmp_path):
        with pytest.raises(ValueError, match="Unknown import format"):
            convert(gtfs_zip, tmp_path / "o.xml", source_format="siri")
        with pytest.raises(ValueError, match="Unknown export format"):
            convert(gtfs_zip, tmp_path / "o.xml", target_format="xlsx")


class TestFormatDetection:
    def test_gtfs_and_netex_archives_are_told_apart_by_content(
            self, gtfs_zip, netex_xml, tmp_path):
        # Both formats ship inside a .zip, so the extension decides nothing.
        netex_zip = tmp_path / "same_name.zip"
        with zipfile.ZipFile(netex_zip, "w") as zf:
            zf.write(netex_xml, "publication.xml")

        assert detect_source_format(gtfs_zip) == "gtfs"
        assert detect_source_format(netex_zip) == "netex"

    def test_a_stray_member_does_not_flip_the_verdict(self, minimal_gtfs_records,
                                                      tmp_path):
        path = tmp_path / "with_stray.zip"
        path.write_bytes(make_gtfs_zip(minimal_gtfs_records))
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("README.xml", "<notes/>")

        assert detect_source_format(path) == "gtfs"

    def test_xml_and_xlsx_are_read_from_the_extension(self, netex_xml, tmp_path):
        xlsx = tmp_path / "tables.xlsx"
        xlsx.write_bytes(b"")

        assert detect_source_format(netex_xml) == "netex"
        assert detect_source_format(xlsx) == "xlsx"

    def test_a_directory_of_xml_is_netex(self, netex_xml, tmp_path):
        folder = tmp_path / "netex_dir"
        folder.mkdir()
        (folder / "part.xml").write_text(netex_xml.read_text(encoding="utf-8"),
                                         encoding="utf-8")

        assert detect_source_format(folder) == "netex"

    def test_an_unpacked_gtfs_directory_says_to_zip_it(self, tmp_path):
        folder = tmp_path / "unpacked"
        folder.mkdir()
        (folder / "agency.txt").write_text("agency_id\n", encoding="utf-8")
        (folder / "stops.txt").write_text("stop_id\n", encoding="utf-8")

        with pytest.raises(ValueError, match="zip it first"):
            detect_source_format(folder)

    def test_a_single_csv_points_at_its_directory(self, tmp_path):
        path = tmp_path / "line.csv"
        path.write_text("id\n", encoding="utf-8")

        with pytest.raises(ValueError, match="directory of tables"):
            detect_source_format(path)

    def test_a_missing_source_is_a_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_source_format(tmp_path / "absent.zip")

    def test_an_undecidable_archive_asks_to_be_told(self, tmp_path):
        path = tmp_path / "empty.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("notes.md", "nothing here")

        with pytest.raises(ValueError, match="pass source_format="):
            detect_source_format(path)

    @pytest.mark.parametrize("name,expected", [
        ("out.zip", "gtfs"),
        ("out.xml", "netex"),
        ("out.xml.gz", "netex"),
        ("OUT.XML", "netex"),
    ])
    def test_target_format_comes_from_the_name(self, name, expected):
        assert detect_target_format(name) == expected

    def test_an_unknown_target_name_is_refused(self):
        with pytest.raises(ValueError, match="pass target_format="):
            detect_target_format("out.dat")


class TestAdapterFactories:
    """The factories the CLI's import/export share with convert."""

    @pytest.mark.parametrize("fmt,expected", [
        ("gtfs", "GtfsImporter"),
        ("netex", "NetexImporter"),
        ("csv", "SpreadsheetImporter"),
        ("xlsx", "SpreadsheetImporter"),
    ])
    def test_every_read_format_builds_its_importer(self, fmt, expected):
        assert type(make_importer(fmt)).__name__ == expected

    @pytest.mark.parametrize("fmt,expected", [
        ("gtfs", "GtfsExporter"),
        ("netex", "NetexExporter"),
    ])
    def test_every_write_format_builds_its_exporter(self, fmt, expected):
        assert type(make_exporter(fmt)).__name__ == expected

    def test_no_profile_still_means_epip(self):
        # The CLI passes profile=None for a GTFS export; the NeTEx default must
        # not shift because of it.
        assert make_exporter("netex", profile=None).profile.name == "epip"

    def test_unknown_formats_raise(self):
        with pytest.raises(ValueError, match="Unknown import format"):
            make_importer("siri")
        with pytest.raises(ValueError, match="Unknown export format"):
            make_exporter("csv")
