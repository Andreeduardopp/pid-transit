"""
Spreadsheet Importer Adapter.

Parses Excel (.xlsx) files or a directory of .csv files that follow the
Transmodel relational schema and imports them into the TransmodelDatabase.
"""

import csv
import logging
from pathlib import Path
from typing import Dict, Union

from ..core.database import TransmodelDatabase
from ..core.schemas import TRANSMODEL_ENTITIES
from ..core.exceptions import ImportFailedError

logger = logging.getLogger(__name__)


def _load_openpyxl():
    """Import openpyxl on demand.

    Only the xlsx path needs it, so importing this module - or running a CSV
    import - must not pay for it.
    """
    try:
        import openpyxl
    except ImportError as e:
        raise ImportFailedError(
            "openpyxl is required to import Excel files. Install via 'pip install openpyxl'"
        ) from e
    return openpyxl

class SpreadsheetImporter:
    """
    Adapter class for importing data from raw CSV folders or Excel workbooks.
    Expects table structures to map 1:1 with the underlying Transmodel database.
    """

    def __init__(self, format: str = "csv"):
        """
        Args:
            format: 'csv' (expects a directory path) or 'xlsx' (expects a file path).
        """
        self.format = format.lower()
        if self.format not in ("csv", "xlsx"):
            raise ValueError("format must be 'csv' or 'xlsx'")

    def import_to_db(self, db: TransmodelDatabase, source_path: Union[Path, str]) -> Dict[str, int]:
        """
        Import the spreadsheet data into the database.

        Args:
            db: TransmodelDatabase instance.
            source_path: Path to the .xlsx file or the directory containing .csv files.
        """
        logger.info("Importing spreadsheet from %s (format=%s)", source_path, self.format)
        path = Path(source_path)
        stats: Dict[str, int] = {}

        if self.format == "xlsx":
            openpyxl = _load_openpyxl()
            if not path.is_file() or path.suffix.lower() != ".xlsx":
                raise ImportFailedError(f"Expected a valid .xlsx file, got {path}")

            try:
                wb = openpyxl.load_workbook(path, data_only=True)
                with db.bulk_session():
                    self._import_workbook(wb, db, stats)
            except Exception as e:
                raise ImportFailedError(f"Failed to read Excel workbook: {e}") from e

        elif self.format == "csv":
            if not path.is_dir():
                raise ImportFailedError(f"For 'csv' format, expected a directory, got {path}")

            with db.bulk_session():
                self._import_csv_dir(path, db, stats)

        return stats

    def _import_workbook(self, wb, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        by_entity = {}
        for sheet_name in wb.sheetnames:
            entity_name = sheet_name.lower().strip()
            if entity_name not in TRANSMODEL_ENTITIES:
                logger.warning("Skipping sheet '%s', not a recognized Transmodel entity.", sheet_name)
                continue
            by_entity[entity_name] = sheet_name

        # Registry order is foreign-key safe (operator before line, and so on);
        # the order of the sheets in the workbook is whatever the author chose.
        for entity_name in TRANSMODEL_ENTITIES:
            if entity_name not in by_entity:
                continue
            sheet = wb[by_entity[entity_name]]
            rows = list(sheet.iter_rows(values_only=True))
            if len(rows) < 2:
                continue

            headers = [str(h).strip() for h in rows[0] if h is not None]
            records = []
            for row in rows[1:]:
                if all(v is None or str(v).strip() == "" for v in row):  # skip empty rows
                    continue
                record = {}
                for i, val in enumerate(row):
                    # An empty cell is left out, so the model default applies
                    # (None for optional fields, "" for the key parts of
                    # composite keys such as translation.record_sub_id).
                    if i < len(headers) and val is not None and str(val).strip() != "":
                        record[headers[i]] = str(val).strip()
                records.append(record)

            if records:
                inserted = db.upsert(entity_name, records)
                stats[entity_name] = inserted

    def _import_csv_dir(self, path: Path, db: TransmodelDatabase, stats: Dict[str, int]) -> None:
        by_entity = {}
        for file_path in sorted(path.glob("*.csv")):
            entity_name = file_path.stem.lower().strip()
            if entity_name not in TRANSMODEL_ENTITIES:
                logger.warning("Skipping file '%s', not a recognized Transmodel entity.", file_path.name)
                continue
            by_entity[entity_name] = file_path

        # Registry order is foreign-key safe; glob order is alphabetical at best
        # and would load line.csv before the operator.csv it refers to.
        for entity_name in TRANSMODEL_ENTITIES:
            if entity_name not in by_entity:
                continue
            file_path = by_entity[entity_name]
            try:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    records = []
                    for row in reader:
                        # Clean up keys and values
                        clean_row = {}
                        for k, v in row.items():
                            if k and v is not None and v.strip() != "":
                                clean_row[k.strip()] = v.strip()
                        records.append(clean_row)

                    if records:
                        inserted = db.upsert(entity_name, records)
                        stats[entity_name] = inserted
            except Exception as e:
                raise ImportFailedError(f"Failed to read CSV {file_path.name}: {e}") from e
