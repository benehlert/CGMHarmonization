from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import os
import re
import textwrap
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import structlog
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field

try:
    from .model_registry import DEFAULT_CGM_MODEL, DEFAULT_GENERAL_MODEL, ModelSpec, resolve_model_name, resolve_model_spec
except ImportError:  # pragma: no cover - script execution path
    from model_registry import DEFAULT_CGM_MODEL, DEFAULT_GENERAL_MODEL, ModelSpec, resolve_model_name, resolve_model_spec


load_dotenv(Path(__file__).parent / ".env")

logger = structlog.get_logger()


class ProviderQuotaError(RuntimeError):
    """Raised when a provider reports an exhausted model quota."""

PREVIEW_LINES = 30
PROFILE_PREVIEW_LINES = 80
REPAIR_PREVIEW_LINES = 100
EXCEL_SUFFIXES = {".xls", ".xlsx"}
TEXT_MIME_PREFIXES = {
    "text/",
    "application/json",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ALLOWED_CGM_ROLES = {"cgm_primary", "cgm_secondary", "overlapping_export"}
PARSE_SPEC_VERSION = "v2"
TIMESTAMP_MISSING_SENTINELS = {"", "nan", "nat", "none", "null"}
GLUCOSE_COLUMN_PRIORITY = [
    "cgm",
    "cgmvalue",
    "glucose",
    "glucemia",
    "sensorglucose",
    "gl",
    "value",
]
TIMESTAMP_COLUMN_PRIORITY = [
    "displaytimeadjusted",
    "displaytime",
    "localdttmadjusted",
    "localdttm",
    "devicedttm",
    "datadtm",
    "corrdatetime",
    "internaltime",
    "startdatetime",
    "startdate",
    "starttime",
    "timestamp",
    "time",
    "hora",
]
RELATIVE_DAY_COLUMN_PRIORITY = [
    "devicedtdaysfromenroll",
    "devicedaysfromenroll",
    "datedaysfromenroll",
    "dayfromenroll",
    "daysfromenroll",
]
TIME_OF_DAY_COLUMN_PRIORITY = [
    "devicetm",
    "navreadtm",
    "readingtm",
    "resulttime",
    "time",
    "hora",
]
SUBJECT_COLUMN_PRIORITY = [
    "subject_id",
    "deidentid",
    "ptid",
    "subject",
    "patientid",
    "patient",
    "pid",
    "id",
]


class FileMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    relative_path: str
    mime: str
    preview: str
    delimiter_hint: Optional[str] = None
    header_columns: list[str] = Field(default_factory=list)
    sibling_examples: list[str] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)
    source_kind: str = "flat_table"
    table_path: Optional[str] = None
    parent_fields: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name


class FileTriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_role: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    schema_fingerprint: str
    candidate_timestamp_columns: list[str] = Field(default_factory=list)
    candidate_glucose_columns: list[str] = Field(default_factory=list)
    candidate_subject_columns: list[str] = Field(default_factory=list)
    likely_units: Optional[str] = None
    is_metadata_only: bool = False
    is_duplicate_export_candidate: bool = False


class RowFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    op: str
    value: Any = None


class ParseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_fingerprint: str
    file_role: str
    read_kind: str
    delimiter: Optional[str] = None
    sheet_name: Optional[str | int] = None
    table_path: Optional[str] = None
    header_row: int = 0
    encoding: Optional[str] = None
    timestamp_groups: list[list[str]] = Field(default_factory=list)
    glucose_column: str
    subject_column: Optional[str] = None
    row_filters: list[RowFilter] = Field(default_factory=list)
    glucose_unit: str = "mg/dL"
    time_only_rollover: bool = False
    subject_strategy: str = "column_or_unknown"
    subject_namespace_with_file: bool = False
    min_rows: int = 1
    timestamp_parse_min_rate: float = 0.5
    glucose_parse_min_rate: float = 0.5


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    clean: Optional[str] = None
    rows_in: Optional[int] = None
    rows_out: Optional[int] = None
    subject_id_strategy: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    llm_reason: Optional[str] = None
    loader_sha: Optional[str] = None
    file_role: Optional[str] = None
    triage_confidence: Optional[float] = None
    schema_fingerprint: Optional[str] = None
    parse_spec_version: Optional[str] = None
    parse_qc: dict[str, Any] = Field(default_factory=dict)
    merge_cluster: Optional[str] = None
    merge_action: Optional[str] = None


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
        for value in schema.values():
            if isinstance(value, dict):
                _strict_json_schema(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _strict_json_schema(item)
    return schema


TRIAGE_SCHEMA = _strict_json_schema(FileTriageDecision.model_json_schema())
PARSE_SPEC_SCHEMA = _strict_json_schema(ParseSpec.model_json_schema())

TRIAGE_PROMPT = """
You are classifying one dataset file inside a CGM harmonization pipeline.

Return strict JSON with:
- file_role: one of cgm_primary, cgm_secondary, overlapping_export, calibration_only,
  pump_only, lab_or_meter, metadata_dictionary, readme_or_doc, non_cgm
- confidence: 0.0-1.0
- reason: short explanation
- schema_fingerprint: stable short identifier for files with the same extraction logic
- candidate_timestamp_columns
- candidate_glucose_columns
- candidate_subject_columns
- likely_units: mg/dL, mmol/L, mixed, or unknown
- is_metadata_only
- is_duplicate_export_candidate

Use file path, siblings, headers, preview, and likely source semantics. A file can be a
legitimate CGM source even if it is not the reference repository file. Reject documentation,
field dictionaries, and lab/meter-only files.

<RELATIVE_PATH>
$relative_path

<MIME_TYPE>
$mime_type

<HEADER_COLUMNS>
$header_columns

<FILE_PROFILE>
$file_profile

<SIBLING_FILES>
$sibling_files

<DIR_TREE>
$dir_tree

<PREVIEW>
$preview
"""

PARSE_SPEC_PROMPT = """
You are building a deterministic extraction spec for one CGM-like dataset file.

Return strict JSON matching this contract:
- read_kind: delimited, excel, json, or json_records
- delimiter: delimiter string when read_kind=delimited
- sheet_name: optional sheet for excel
- table_path: optional dotted path for json_records candidate tables
- header_row: zero-based header row
- timestamp_groups: ordered list of column groups.
  Each inner list is one candidate timestamp source. If a group has multiple columns,
  concatenate them with spaces before parsing. Fallback row-wise across groups.
- glucose_column
- subject_column: optional
- row_filters: optional list of {column, op, value}; supported ops are equals,
  not_equals, in, not_in, contains
- glucose_unit: mg/dL, mmol/L, or unknown
- time_only_rollover: true only when the timestamp source is time-only and must roll
  over past midnight
- subject_strategy: one of column_or_unknown, filename, folder, filename_or_folder
- subject_namespace_with_file: boolean
- min_rows, timestamp_parse_min_rate, glucose_parse_min_rate

Use only columns that exist in the file. Prefer the subject-facing timestamp rather than
internal device time when both exist. Filter out calibration or non-CGM rows when needed.

<RELATIVE_PATH>
$relative_path

<MIME_TYPE>
$mime_type

<TRIAGE_DECISION>
$triage

<HEADER_COLUMNS>
$header_columns

<FILE_PROFILE>
$file_profile

<PREVIEW>
$preview
"""

REPAIR_PARSE_SPEC_PROMPT = """
You are repairing a deterministic extraction spec for one CGM-like dataset file after
execution failed.

The initial failure may stem from incomplete schema detection in the short preview.
Use the expanded preview below, up to 100 lines, to identify the real delimiter,
header row, timestamp columns, glucose column, subject column, units, and row filters.

Return strict JSON matching the same parse-spec contract. Use only columns that exist
in the file schema shown by the expanded preview.

<RELATIVE_PATH>
$relative_path

<MIME_TYPE>
$mime_type

<TRIAGE_DECISION>
$triage

<FAILED_SPEC>
$failed_spec

<FAILURE_ERROR>
$failure_error

<HEADER_COLUMNS_FROM_EXPANDED_PREVIEW>
$header_columns

<FILE_PROFILE_FROM_EXPANDED_PREVIEW>
$file_profile

<EXPANDED_PREVIEW_UP_TO_100_LINES>
$preview
"""


def normalize_column_name(value: str) -> str:
    cleaned = re.sub(r"[\ufeff\u200b\xa0]+", "", str(value or ""))
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned.lower())
    return cleaned


def clean_string_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace("\u00a0", " ", regex=False).str.strip()
    lowered = cleaned.str.lower()
    return cleaned.mask(lowered.isin(TIMESTAMP_MISSING_SENTINELS))


def load_excel_preview(path: Path, max_lines: int = PREVIEW_LINES) -> str:
    lines: list[str] = []
    try:
        workbook = pd.ExcelFile(path)
        for sheet_name in workbook.sheet_names:
            if len(lines) >= max_lines:
                break
            try:
                df = pd.read_excel(workbook, sheet_name=sheet_name, nrows=max_lines, dtype=str)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"# Sheet: {sheet_name}")
                lines.append(f"# Unable to preview sheet: {exc}")
                continue

            lines.append(f"# Sheet: {sheet_name}")
            if df.empty and len(df.columns) == 0:
                lines.append("# Empty sheet")
                continue
            lines.append(",".join(str(column) for column in df.columns))
            for row in df.head(max(0, max_lines - len(lines))).itertuples(index=False, name=None):
                lines.append(",".join("" if pd.isna(value) else str(value) for value in row))
                if len(lines) >= max_lines:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning("excel_preview_fail", file=str(path), error=str(exc))
        return ""
    return "\n".join(lines[:max_lines])


def load_preview(path: Path, max_lines: int = PREVIEW_LINES) -> str:
    if path.suffix.lower() in EXCEL_SUFFIXES:
        return load_excel_preview(path, max_lines=max_lines)
    try:
        with path.open("rb") as fh:
            raw = fh.read(32_768)
        text = raw.decode("utf-8", errors="ignore")
        return "\n".join(text.splitlines()[:max_lines])
    except Exception as exc:  # noqa: BLE001
        logger.warning("preview_fail", file=str(path), error=str(exc))
        return ""


def meta_with_preview_lines(meta: FileMeta, max_lines: int) -> FileMeta:
    preview = load_preview(meta.path, max_lines=max_lines)
    profile = build_file_profile(meta.path, preview)
    delimiter = profile.get("best_delimiter") or sniff_delimiter(preview)
    return meta.model_copy(
        update={
            "preview": preview,
            "delimiter_hint": delimiter,
            "header_columns": profile.get("best_header_columns") or split_preview_columns(preview, delimiter),
            "profile": profile,
        }
    )


def failure_suggests_incomplete_schema_detection(error: Exception | str | None) -> bool:
    if error is None:
        return False
    text = str(error).lower()
    schema_failure_markers = (
        "not found",
        "timestamp parse rate too low",
        "glucose parse rate too low",
        "filtered data produced too few rows",
        "unable to read delimited file",
        "no columns to parse",
        "emptydataerror",
        "header",
        "delimiter",
        "expected",
        "tokenizing data",
    )
    return any(marker in text for marker in schema_failure_markers)


def is_probably_text(path: Path, sample: int = 4096) -> bool:
    with path.open("rb") as fh:
        chunk = fh.read(sample)
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            chunk.decode("utf-16")
            return True
        except UnicodeDecodeError:
            return False


def sniff_delimiter(preview: str) -> Optional[str]:
    lines = [line for line in preview.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return None
    line = lines[0]
    candidates = {
        "|": line.count("|"),
        "\t": line.count("\t"),
        ",": line.count(","),
        ";": line.count(";"),
    }
    delimiter, score = max(candidates.items(), key=lambda item: item[1])
    return delimiter if score > 0 else None


def sniff_line_delimiter(line: str) -> Optional[str]:
    candidates = {
        "|": line.count("|"),
        "\t": line.count("\t"),
        ",": line.count(","),
        ";": line.count(";"),
    }
    delimiter, score = max(candidates.items(), key=lambda item: item[1])
    return delimiter if score > 0 else None


def split_preview_columns(preview: str, delimiter: Optional[str]) -> list[str]:
    lines = [line for line in preview.splitlines() if line.strip()]
    if not lines:
        return []
    header = next((line for line in lines if not line.lstrip().startswith("#")), lines[0])
    if delimiter:
        return [part.strip().strip('"') for part in header.split(delimiter)]
    if header.startswith("{") or header.startswith("["):
        return []
    return [header.strip().strip('"')]


def split_header_line(line: str, delimiter: Optional[str]) -> list[str]:
    if not delimiter:
        return [line.strip().strip('"')]
    return [part.strip().strip('"') for part in line.split(delimiter)]


def build_file_profile(path: Path, preview: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for row_index, line in enumerate(preview.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("{") or stripped.startswith("["):
            continue
        delimiter = sniff_line_delimiter(stripped)
        columns = split_header_line(stripped, delimiter)
        if len(columns) < 2:
            continue
        timestamp_columns = infer_timestamp_candidates(columns)
        glucose_columns = infer_glucose_candidates(columns)
        subject_columns = infer_subject_candidates(columns)
        score = len(timestamp_columns) * 3 + len(glucose_columns) * 3 + len(subject_columns) + min(len(columns), 12) / 12
        if score <= 0 and len(columns) < 3:
            continue
        candidates.append(
            {
                "row": row_index,
                "delimiter": delimiter,
                "columns": columns[:40],
                "timestamp_columns": timestamp_columns[:8],
                "glucose_columns": glucose_columns[:8],
                "subject_columns": subject_columns[:8],
                "score": round(float(score), 3),
            }
        )
    candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)[:5]
    best = candidates[0] if candidates else {}
    best_columns = best.get("columns") or split_preview_columns(preview, sniff_delimiter(preview))
    return {
        "size_bytes": path.stat().st_size if path.exists() else None,
        "line_sample_count": len(preview.splitlines()),
        "best_header_row": best.get("row", 0),
        "best_delimiter": best.get("delimiter") or sniff_delimiter(preview),
        "best_header_columns": best_columns,
        "header_row_candidates": candidates,
        "likely_units": infer_likely_units(preview, best_columns),
    }


def effective_header_columns(meta: FileMeta) -> list[str]:
    profiled = meta.profile.get("best_header_columns") if meta.profile else None
    return list(profiled or meta.header_columns)


def effective_header_row(meta: FileMeta) -> int:
    if not meta.profile:
        return 0
    try:
        return int(meta.profile.get("best_header_row", 0))
    except (TypeError, ValueError):
        return 0


def file_profile_json(meta: FileMeta) -> str:
    return json.dumps(meta.profile or {}, ensure_ascii=False, indent=2)


def flatten_json_mapping(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_json_mapping(item, child_prefix))
        return flattened
    if isinstance(value, list):
        return {}
    return {prefix: value}


def get_json_path(value: Any, path: str | None) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def parent_fields_for_json_path(payload: Any, table_path: str | None) -> dict[str, Any]:
    flattened = flatten_json_mapping(payload)
    excluded_prefix = f"{table_path}." if table_path else ""
    return {
        field: field_value
        for field, field_value in flattened.items()
        if field != table_path and (not excluded_prefix or not field.startswith(excluded_prefix))
    }


def iter_json_record_arrays(value: Any, path: str = "") -> Iterable[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    if isinstance(value, dict):
        parent_fields = flatten_json_mapping(value)
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, list) and item and all(isinstance(row, dict) for row in item[:20]):
                excluded_prefix = f"{child_path}."
                scoped_parent_fields = {
                    field: field_value
                    for field, field_value in parent_fields.items()
                    if field != child_path and not field.startswith(excluded_prefix)
                }
                yield child_path, item, scoped_parent_fields
            yield from iter_json_record_arrays(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            yield from iter_json_record_arrays(item, f"{path}.{index}" if path else str(index))


def json_records_dataframe(path: Path, table_path: str | None, parent_fields: dict[str, Any] | None = None) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = get_json_path(payload, table_path)
    if not isinstance(records, list):
        raise ValueError(f"JSON record path {table_path!r} did not resolve to a list")
    parent = parent_fields or parent_fields_for_json_path(payload, table_path)
    if not parent:
        for candidate_path, _records, candidate_parent in iter_json_record_arrays(payload):
            if candidate_path == table_path:
                parent = candidate_parent
                break
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = dict(parent)
        row.update(flatten_json_mapping(record))
        rows.append(row)
    return pd.DataFrame(rows)


def table_preview_from_rows(columns: list[str], rows: list[dict[str, Any]], max_lines: int = PREVIEW_LINES) -> str:
    lines = [",".join(columns)]
    for row in rows[: max(0, max_lines - 1)]:
        lines.append(",".join("" if row.get(column) is None else str(row.get(column)) for column in columns))
    return "\n".join(lines)


def discover_json_candidate_metas(path: Path, root: Path, mime: str, siblings: list[str]) -> list[FileMeta]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("json_profile_fail", file=str(path), error=str(exc))
        return []

    metas: list[FileMeta] = []
    for table_path, records, parent_fields in iter_json_record_arrays(payload):
        parent_fields = parent_fields or parent_fields_for_json_path(payload, table_path)
        flattened_rows = []
        for record in records[:10]:
            row = dict(parent_fields)
            row.update(flatten_json_mapping(record))
            flattened_rows.append(row)
        columns = sorted({column for row in flattened_rows for column in row})
        if len(columns) < 2:
            continue
        preview = table_preview_from_rows(columns, flattened_rows)
        profile = build_file_profile(path, preview)
        profile.update(
            {
                "source_kind": "json_records",
                "table_path": table_path,
                "record_count_estimate": len(records),
                "parent_fields": parent_fields,
            }
        )
        relative_path = f"{path.relative_to(root).as_posix()}#json={table_path}"
        metas.append(
            FileMeta(
                path=path.resolve(),
                relative_path=relative_path,
                mime=mime,
                preview=preview,
                delimiter_hint=",",
                header_columns=columns,
                sibling_examples=siblings,
                profile=profile,
                source_kind="json_records",
                table_path=table_path,
                parent_fields=parent_fields,
            )
        )
    return metas


def discover_excel_candidate_metas(path: Path, root: Path, mime: str, siblings: list[str]) -> list[FileMeta]:
    try:
        workbook = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("excel_profile_fail", file=str(path), error=str(exc))
        return []

    metas: list[FileMeta] = []
    for sheet_name in workbook.sheet_names:
        try:
            df = pd.read_excel(workbook, sheet_name=sheet_name, nrows=PROFILE_PREVIEW_LINES, dtype=str)
        except Exception as exc:  # noqa: BLE001
            logger.warning("excel_sheet_profile_fail", file=str(path), sheet=sheet_name, error=str(exc))
            continue
        if df.empty and len(df.columns) == 0:
            continue
        columns = [str(column).replace("\ufeff", "").strip() for column in df.columns]
        rows = [
            {column: "" if pd.isna(value) else value for column, value in zip(columns, row)}
            for row in df.head(10).itertuples(index=False, name=None)
        ]
        preview = table_preview_from_rows(columns, rows)
        profile = build_file_profile(path, preview)
        profile.update({"source_kind": "excel_sheet", "table_path": f"sheet:{sheet_name}", "sheet_name": sheet_name})
        relative_path = f"{path.relative_to(root).as_posix()}#sheet={sheet_name}"
        metas.append(
            FileMeta(
                path=path.resolve(),
                relative_path=relative_path,
                mime=mime,
                preview=preview,
                delimiter_hint=",",
                header_columns=columns,
                sibling_examples=siblings,
                profile=profile,
                source_kind="excel_sheet",
                table_path=f"sheet:{sheet_name}",
            )
        )
    return metas


def infer_likely_units(preview: str, columns: Iterable[str]) -> str:
    joined = " ".join(columns).lower() + "\n" + preview.lower()
    if "mmol" in joined:
        return "mmol/L"
    if "mg/dl" in joined or "mgdl" in joined:
        return "mg/dL"
    return "unknown"


def score_columns(columns: Iterable[str], priorities: list[str], extra_patterns: Iterable[str]) -> list[str]:
    resolved: list[tuple[int, str]] = []
    extras = tuple(extra_patterns)
    for column in columns:
        normalized = normalize_column_name(column)
        score = 10_000
        for idx, candidate in enumerate(priorities):
            if candidate == normalized:
                score = min(score, idx)
            elif candidate in normalized:
                score = min(score, idx + 100)
        if any(pattern in normalized for pattern in extras):
            score = min(score, 9_000)
        if score < 10_000:
            resolved.append((score, column))
    return [column for _, column in sorted(resolved)]


def infer_timestamp_candidates(columns: Iterable[str]) -> list[str]:
    return score_columns(
        columns,
        TIMESTAMP_COLUMN_PRIORITY,
        ("time", "date", "dt", "hora", "timestamp"),
    )


def infer_glucose_candidates(columns: Iterable[str]) -> list[str]:
    candidates = score_columns(
        columns,
        GLUCOSE_COLUMN_PRIORITY,
        ("glucose", "glucemia", "cgm", "sensor", "gl"),
    )
    filtered = [
        column
        for column in candidates
        if "meter" not in normalize_column_name(column)
        and "smbg" not in normalize_column_name(column)
        and "ketone" not in normalize_column_name(column)
        and not normalize_column_name(column).endswith("unit")
    ]
    return filtered or candidates


def infer_subject_candidates(columns: Iterable[str]) -> list[str]:
    return score_columns(
        columns,
        SUBJECT_COLUMN_PRIORITY,
        ("subject", "deident", "patient", "pt", "case", "pid", "id"),
    )


def infer_relative_day_candidates(columns: Iterable[str]) -> list[str]:
    return score_columns(
        columns,
        RELATIVE_DAY_COLUMN_PRIORITY,
        ("daysfromenroll", "dayfromenroll"),
    )


def infer_time_of_day_candidates(columns: Iterable[str]) -> list[str]:
    return score_columns(
        columns,
        TIME_OF_DAY_COLUMN_PRIORITY,
        ("time", "tm", "hora"),
    )


def is_relative_day_column(column: str) -> bool:
    normalized = normalize_column_name(column)
    return any(token in normalized for token in ("daysfromenroll", "dayfromenroll"))


def is_time_of_day_column(column: str) -> bool:
    normalized = normalize_column_name(column)
    return (
        normalized in {"hora", "time", "devicetm", "navreadtm", "readingtm", "resulttime"}
        or normalized.endswith("tm")
        or "time" in normalized
    )


def is_timestamp_like_column(column: str) -> bool:
    normalized = normalize_column_name(column)
    return (
        is_relative_day_column(column)
        or is_time_of_day_column(column)
        or "date" in normalized
        or "timestamp" in normalized
        or "dttm" in normalized
        or "datetime" in normalized
    )


def is_high_confidence_cgm_schema(columns: Iterable[str]) -> bool:
    column_list = list(columns)
    normalized_columns = {normalize_column_name(column) for column in column_list}
    strong_glucose_columns = {
        "glucose",
        "cgm",
        "sensorglu",
        "cgmvalue",
        "glucosevalue",
        "resultvalue1",
        "navsg",
        "glucemia",
        "value",
    }
    has_glucose = bool(normalized_columns & strong_glucose_columns)
    has_subject = bool(infer_subject_candidates(column_list))
    has_timestamp = bool(
        normalized_columns
        & {
            "timestamp",
            "datetime",
            "dttm",
            "datadttm",
            "corrdatetime",
            "displaytime",
            "displaytimeadjusted",
            "internaltime",
        }
    )
    has_relative_day_time = bool(infer_relative_day_candidates(column_list)) and bool(
        [
            column
            for column in infer_time_of_day_candidates(column_list)
            if not is_relative_day_column(column)
        ]
    )
    return has_glucose and has_subject and (has_timestamp or has_relative_day_time)


def deterministic_non_cgm_decision(meta: FileMeta, reason: str) -> FileTriageDecision:
    heuristic = heuristic_triage(meta)
    return FileTriageDecision(
        file_role="non_cgm",
        confidence=0.9,
        reason=reason,
        schema_fingerprint=heuristic.schema_fingerprint,
        candidate_timestamp_columns=heuristic.candidate_timestamp_columns,
        candidate_glucose_columns=heuristic.candidate_glucose_columns,
        candidate_subject_columns=heuristic.candidate_subject_columns,
        likely_units=heuristic.likely_units,
        is_metadata_only=False,
    )


def deterministic_schema_fingerprint(meta: FileMeta) -> str:
    payload = json.dumps(
        {
            "suffix": meta.path.suffix.lower(),
            "mime": meta.mime,
            "source_kind": meta.source_kind,
            "table_path": meta.table_path,
            "delimiter": meta.delimiter_hint,
            "header_row": effective_header_row(meta),
            "columns": [normalize_column_name(column) for column in effective_header_columns(meta)],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_dir_tree(root: Path, max_lines: int = 200) -> str:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        indent = "  " * (len(rel.parts) - 1)
        if len(lines) >= max_lines:
            lines.append("  … (truncated)")
            break
        lines.append(f"{indent}{rel.name}/" if path.is_dir() else f"{indent}{rel.name}")
    return "\n".join(lines)


def walk_files(root: Path) -> list[FileMeta]:
    files: list[FileMeta] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        mime, _ = mimetypes.guess_type(path)
        resolved_mime = mime or "application/octet-stream"
        siblings = sorted(
            child.name
            for child in path.parent.iterdir()
            if child.is_file() and child.name != path.name
        )[:12]
        if path.suffix.lower() in EXCEL_SUFFIXES:
            excel_metas = discover_excel_candidate_metas(path, root, resolved_mime, siblings)
            if excel_metas:
                files.extend(excel_metas)
                continue
        if path.suffix.lower() == ".json":
            json_metas = discover_json_candidate_metas(path, root, resolved_mime, siblings)
            if json_metas:
                files.extend(json_metas)
                continue
        profile_preview = load_preview(path, max_lines=PROFILE_PREVIEW_LINES)
        profile = build_file_profile(path, profile_preview)
        preview = "\n".join(profile_preview.splitlines()[:PREVIEW_LINES])
        delimiter = profile.get("best_delimiter") or sniff_delimiter(preview)
        header_columns = profile.get("best_header_columns") or split_preview_columns(preview, delimiter)
        files.append(
            FileMeta(
                path=path.resolve(),
                relative_path=path.relative_to(root).as_posix(),
                mime=resolved_mime,
                preview=preview,
                delimiter_hint=delimiter,
                header_columns=header_columns,
                sibling_examples=siblings,
                profile=profile,
            )
        )
    return files


def is_text_like(meta: FileMeta) -> bool:
    if meta.path.suffix.lower() in EXCEL_SUFFIXES:
        return True
    if any(meta.mime.startswith(prefix) for prefix in TEXT_MIME_PREFIXES):
        return True
    return is_probably_text(meta.path)


def guess_subject_from_path(path: Path, strategy: str) -> str:
    def candidate_tokens(parts: Iterable[str]) -> str:
        for part in parts:
            lowered = part.lower()
            keyed = re.search(r"(?:subject|subj|case|patient|pt|pid|id)[^0-9]*([0-9]+)", lowered)
            if keyed:
                return keyed.group(1)
            any_number = re.search(r"\b([0-9]+)\b", lowered)
            if any_number:
                return any_number.group(1)
        return "UNKNOWN"

    if strategy == "filename":
        return candidate_tokens([path.stem])
    if strategy == "folder":
        return candidate_tokens([parent.name for parent in path.parents])
    if strategy == "filename_or_folder":
        subject = candidate_tokens([path.stem])
        return subject if subject != "UNKNOWN" else candidate_tokens([parent.name for parent in path.parents])
    return "UNKNOWN"


def hard_negative_decision(meta: FileMeta) -> Optional[FileTriageDecision]:
    basename = meta.path.name.lower()
    normalized_path = meta.relative_path.lower()
    fingerprint = deterministic_schema_fingerprint(meta)
    if basename.startswith("readme") or basename.endswith((".md", ".rst", ".pdf", ".doc", ".docx")):
        return FileTriageDecision(
            file_role="readme_or_doc",
            confidence=0.99,
            reason="Documentation or readme file, not a data extract.",
            schema_fingerprint=fingerprint,
            likely_units="unknown",
            is_metadata_only=True,
        )
    if "field key" in basename or "dictionary" in basename:
        return FileTriageDecision(
            file_role="metadata_dictionary",
            confidence=0.99,
            reason="Field key or data dictionary file, not a CGM extract.",
            schema_fingerprint=fingerprint,
            likely_units="unknown",
            is_metadata_only=True,
        )
    if "tblcultrasitebaseline" in basename or "ultrasitebaseline" in normalized_path:
        return FileTriageDecision(
            file_role="lab_or_meter",
            confidence=0.97,
            reason="Baseline laboratory/meter measurements rather than continuous CGM data.",
            schema_fingerprint=fingerprint,
            likely_units="unknown",
            is_metadata_only=False,
        )
    if meta.preview.lower().startswith('"clinical_data.txt" is'):
        return FileTriageDecision(
            file_role="readme_or_doc",
            confidence=0.99,
            reason="Readme text describing the dataset contents rather than CGM rows.",
            schema_fingerprint=fingerprint,
            likely_units="unknown",
            is_metadata_only=True,
        )
    return None


def heuristic_triage(meta: FileMeta) -> FileTriageDecision:
    columns = effective_header_columns(meta)
    timestamp_candidates = infer_timestamp_candidates(columns)
    glucose_candidates = infer_glucose_candidates(columns)
    subject_candidates = infer_subject_candidates(columns)
    fingerprint = deterministic_schema_fingerprint(meta)
    lower_name = meta.path.name.lower()
    lower_path = meta.relative_path.lower()
    likely_units = str((meta.profile or {}).get("likely_units") or infer_likely_units(meta.preview, columns))
    has_relative_day_time = bool(infer_relative_day_candidates(columns)) and bool(
        [
            column
            for column in infer_time_of_day_candidates(columns)
            if not is_relative_day_column(column)
        ]
    )

    if not (timestamp_candidates or has_relative_day_time) or not glucose_candidates:
        file_role = "non_cgm"
        reason = "No plausible timestamp+glucose schema detected from headers/preview."
    else:
        if any(token in lower_name for token in ["clarity", "diasend", "othercgm", "pump_cgm", "ilet", "tandem"]):
            file_role = "overlapping_export"
        elif "monitorcgm" in lower_name or "blinded" in lower_name:
            file_role = "cgm_secondary"
        else:
            file_role = "cgm_primary"
        reason = "Headers and preview contain plausible CGM timestamp and glucose fields."

    is_metadata = file_role in {"metadata_dictionary", "readme_or_doc"}
    is_duplicate = file_role == "overlapping_export"
    if "field key" in lower_name:
        file_role = "metadata_dictionary"
        reason = "Field dictionary detected from filename."
        is_metadata = True
    if "readme" in lower_name:
        file_role = "readme_or_doc"
        reason = "Readme/documentation detected from filename."
        is_metadata = True
    if lower_path.endswith("tblcultrasitebaseline.csv"):
        file_role = "lab_or_meter"
        reason = "Site baseline file is not treated as continuous CGM."
        is_duplicate = False

    return FileTriageDecision(
        file_role=file_role,
        confidence=0.65 if file_role in ALLOWED_CGM_ROLES else 0.9,
        reason=reason,
        schema_fingerprint=fingerprint,
        candidate_timestamp_columns=timestamp_candidates,
        candidate_glucose_columns=glucose_candidates,
        candidate_subject_columns=subject_candidates,
        likely_units=likely_units,
        is_metadata_only=is_metadata,
        is_duplicate_export_candidate=is_duplicate,
    )


def heuristic_parse_spec(meta: FileMeta, triage: FileTriageDecision) -> ParseSpec:
    columns = effective_header_columns(meta)
    normalized = {normalize_column_name(column): column for column in columns}

    def has_column(name: str) -> bool:
        return name in normalized

    timestamp_groups: list[list[str]] = []
    relative_day_candidates = infer_relative_day_candidates(columns)
    time_of_day_candidates = [
        column for column in infer_time_of_day_candidates(columns) if not is_relative_day_column(column)
    ]
    if has_column("navreaddt") and has_column("navreadtm"):
        timestamp_groups.append([normalized["navreaddt"], normalized["navreadtm"]])
    if has_column("readingdt") and has_column("readingtm"):
        timestamp_groups.append([normalized["readingdt"], normalized["readingtm"]])
    if has_column("resultdate") and has_column("resulttime"):
        timestamp_groups.append([normalized["resultdate"], normalized["resulttime"]])
    if has_column("date") and has_column("time"):
        timestamp_groups.append([normalized["date"], normalized["time"]])
    if relative_day_candidates and time_of_day_candidates:
        timestamp_groups.append([relative_day_candidates[0], time_of_day_candidates[0]])
    for candidate in triage.candidate_timestamp_columns:
        if [candidate] not in timestamp_groups:
            timestamp_groups.append([candidate])

    glucose_column = triage.candidate_glucose_columns[0] if triage.candidate_glucose_columns else ""
    subject_column = triage.candidate_subject_columns[0] if triage.candidate_subject_columns else None

    row_filters: list[RowFilter] = []
    if has_column("recordtype"):
        row_filters.append(RowFilter(column=normalized["recordtype"], op="equals", value="CGM"))
    if has_column("iscalibration"):
        row_filters.append(RowFilter(column=normalized["iscalibration"], op="not_in", value=["1", "true", "y", "yes"]))
    if has_column("calibration"):
        row_filters.append(RowFilter(column=normalized["calibration"], op="not_in", value=["1", "true", "y", "yes"]))
    if has_column("type") and re.search(r"(^|[,|\t ])cgm([,|\t ]|$)", meta.preview, flags=re.IGNORECASE | re.MULTILINE):
        row_filters.append(RowFilter(column=normalized["type"], op="equals", value="cgm"))

    lower_columns = {normalize_column_name(column) for column in columns}
    time_only_rollover = bool(timestamp_groups) and len(timestamp_groups[0]) == 1 and all(
        normalize_column_name(column) == "hora" for column in timestamp_groups[0]
    )

    subject_strategy = "column_or_unknown"
    if not subject_column:
        if re.search(r"\bcase\b|\b[0-9]{1,4}_blinded\b|_blinded", meta.path.stem.lower()):
            subject_strategy = "filename"
        else:
            subject_strategy = "filename_or_folder"
    elif any(token in lower_columns for token in {"case", "subject"}):
        subject_strategy = "column_or_unknown"

    delimiter = meta.delimiter_hint or (meta.profile or {}).get("best_delimiter") or "|"
    if meta.source_kind == "json_records":
        read_kind = "json_records"
    elif meta.path.suffix.lower() in {".xls", ".xlsx"}:
        read_kind = "excel"
    elif meta.path.suffix.lower() == ".json":
        read_kind = "json"
    else:
        read_kind = "delimited"
    if read_kind == "delimited" and not delimiter:
        delimiter = ","
    sheet_name = (meta.profile or {}).get("sheet_name")

    return ParseSpec(
        schema_fingerprint=triage.schema_fingerprint,
        file_role=triage.file_role,
        read_kind=read_kind,
        delimiter=delimiter,
        sheet_name=sheet_name,
        table_path=meta.table_path,
        header_row=effective_header_row(meta),
        timestamp_groups=timestamp_groups,
        glucose_column=glucose_column,
        subject_column=subject_column,
        row_filters=row_filters,
        glucose_unit=triage.likely_units or "unknown",
        time_only_rollover=time_only_rollover,
        subject_strategy=subject_strategy,
        subject_namespace_with_file=False,
        min_rows=1,
        timestamp_parse_min_rate=0.5,
        glucose_parse_min_rate=0.5,
    )


def resolve_columns(spec: ParseSpec, available_columns: Iterable[str]) -> ParseSpec:
    mapping = {normalize_column_name(column): column for column in available_columns}

    def resolve(column: Optional[str]) -> Optional[str]:
        if not column:
            return column
        normalized = normalize_column_name(column)
        if normalized in mapping:
            return mapping[normalized]
        for candidate_norm, original in mapping.items():
            if candidate_norm == normalized or candidate_norm.startswith(normalized) or normalized.startswith(candidate_norm):
                return original
        return column

    groups = [[resolve(column) or column for column in group] for group in spec.timestamp_groups]
    filters = [RowFilter(column=resolve(item.column) or item.column, op=item.op, value=item.value) for item in spec.row_filters]
    updated = spec.model_copy(
        update={
            "timestamp_groups": groups,
            "glucose_column": resolve(spec.glucose_column) or spec.glucose_column,
            "subject_column": resolve(spec.subject_column),
            "row_filters": filters,
        }
    )
    return updated


def parse_mixed_timestamps(series: pd.Series) -> pd.Series:
    cleaned = clean_string_series(series)
    parsed = pd.to_datetime(cleaned, errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_convert(None).dt.floor("s")


def parse_clock_time_to_timedelta(series: pd.Series) -> pd.Series:
    cleaned = clean_string_series(series)
    parsed = pd.to_timedelta(cleaned, errors="coerce")
    remaining = cleaned.notna() & parsed.isna()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        if not remaining.any():
            break
        candidate = pd.to_datetime(cleaned[remaining], format=fmt, errors="coerce")
        if candidate.notna().any():
            delta = (
                pd.to_timedelta(candidate.dt.hour, unit="h")
                + pd.to_timedelta(candidate.dt.minute, unit="m")
                + pd.to_timedelta(candidate.dt.second, unit="s")
            )
            parsed.loc[delta.index] = delta
            remaining = cleaned.notna() & parsed.isna()
    return parsed


def parse_relative_day_time(df: pd.DataFrame, group: list[str]) -> pd.Series:
    day_column = next((column for column in group if is_relative_day_column(column) and column in df.columns), None)
    time_column = next((column for column in group if is_time_of_day_column(column) and column in df.columns and column != day_column), None)
    if day_column is None or time_column is None:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")

    days = pd.to_numeric(clean_string_series(df[day_column]), errors="coerce")
    time_delta = parse_clock_time_to_timedelta(df[time_column])
    result = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    valid = days.notna() & time_delta.notna()
    if valid.any():
        base = pd.Timestamp("1970-01-01 00:00:00")
        result.loc[valid] = base + pd.to_timedelta(days.loc[valid], unit="D") + time_delta.loc[valid]
    return result


def parse_time_only_rollover(series: pd.Series, subject_ids: Optional[pd.Series]) -> pd.Series:
    cleaned = clean_string_series(series)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    subject_groups: dict[str, list[int]] = defaultdict(list)
    if subject_ids is None:
        subject_groups["__all__"] = list(series.index)
    else:
        for idx, subject in subject_ids.fillna("UNKNOWN").astype(str).items():
            subject_groups[subject].append(idx)

    base = pd.Timestamp("1970-01-01 00:00:00")
    time_formats = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]

    def parse_seconds(token: str) -> Optional[int]:
        for fmt in time_formats:
            try:
                parsed = pd.to_datetime(token, format=fmt)
                return int(parsed.hour * 3600 + parsed.minute * 60 + parsed.second)
            except (TypeError, ValueError):
                continue
        return None

    for indices in subject_groups.values():
        day_offset = 0
        previous_seconds: Optional[int] = None
        for idx in indices:
            token = cleaned.loc[idx]
            if pd.isna(token):
                continue
            seconds = parse_seconds(str(token))
            if seconds is None:
                continue
            if previous_seconds is not None and seconds < previous_seconds:
                day_offset += 1
            result.loc[idx] = base + pd.Timedelta(days=day_offset, seconds=seconds)
            previous_seconds = seconds
    return result


def combine_group_columns(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    parts = [clean_string_series(df[column]) if column in df.columns else pd.Series(pd.NA, index=df.index, dtype="string") for column in columns]
    if len(parts) == 1:
        return parts[0]
    combined = parts[0].fillna("")
    for part in parts[1:]:
        combined = combined.str.cat(part.fillna(""), sep=" ").str.replace(r"\s+", " ", regex=True).str.strip()
    return combined.replace("", pd.NA)


def apply_row_filters(df: pd.DataFrame, filters: list[RowFilter]) -> pd.DataFrame:
    filtered = df
    for row_filter in filters:
        if row_filter.column not in filtered.columns:
            continue
        series = clean_string_series(filtered[row_filter.column])
        lowered = series.str.lower().fillna("")
        value = row_filter.value
        if row_filter.op == "equals":
            mask = lowered == str(value).lower()
        elif row_filter.op == "not_equals":
            mask = lowered != str(value).lower()
        elif row_filter.op == "contains":
            mask = lowered.str.contains(str(value).lower(), regex=False, na=False)
        elif row_filter.op == "in":
            allowed = {str(item).lower() for item in (value if isinstance(value, list) else [value])}
            mask = lowered.isin(allowed)
        elif row_filter.op == "not_in":
            blocked = {str(item).lower() for item in (value if isinstance(value, list) else [value])}
            mask = ~lowered.isin(blocked)
        else:
            continue
        filtered = filtered.loc[mask].copy()
    return filtered


def build_timestamp_series(df: pd.DataFrame, spec: ParseSpec) -> pd.Series:
    subject_ids = None
    if spec.subject_column and spec.subject_column in df.columns:
        subject_ids = clean_string_series(df[spec.subject_column]).fillna("UNKNOWN")
    parsed_options: list[pd.Series] = []
    for group in spec.timestamp_groups:
        if any(is_relative_day_column(column) for column in group) and any(is_time_of_day_column(column) for column in group):
            parsed = parse_relative_day_time(df, group)
        elif len(group) == 1 and is_relative_day_column(group[0]):
            parsed = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        elif not any(is_timestamp_like_column(column) for column in group):
            parsed = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        else:
            raw_series = combine_group_columns(df, group)
            if spec.time_only_rollover:
                parsed = parse_time_only_rollover(raw_series, subject_ids)
            else:
                parsed = parse_mixed_timestamps(raw_series)
        parsed_options.append(parsed)

    if not parsed_options:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")

    combined = parsed_options[0]
    for option in parsed_options[1:]:
        combined = combined.where(combined.notna(), option)
    return combined


def build_subject_series(df: pd.DataFrame, spec: ParseSpec, path: Path) -> tuple[pd.Series, str]:
    if spec.subject_column and spec.subject_column in df.columns:
        subject = clean_string_series(df[spec.subject_column]).fillna("UNKNOWN").astype(str)
        strategy = "column"
    else:
        guessed = guess_subject_from_path(path, spec.subject_strategy)
        subject = pd.Series([guessed] * len(df), index=df.index, dtype="string").fillna("UNKNOWN").astype(str)
        strategy = spec.subject_strategy
    if spec.subject_namespace_with_file:
        prefix = path.stem
        subject = subject.map(lambda value: f"{prefix}:{value}")
        strategy = f"{strategy}+file_namespace"
    return subject, strategy


def read_table(meta: FileMeta, spec: ParseSpec) -> pd.DataFrame:
    suffix = meta.path.suffix.lower()
    if spec.read_kind == "json_records":
        df = json_records_dataframe(meta.path, spec.table_path or meta.table_path, meta.parent_fields)
    elif spec.read_kind == "excel" or suffix in {".xls", ".xlsx"}:
        sheet_name = spec.sheet_name if spec.sheet_name is not None else (meta.profile or {}).get("sheet_name")
        df = pd.read_excel(meta.path, sheet_name=sheet_name, header=spec.header_row, dtype=str)
        if isinstance(df, dict):
            if sheet_name in df:
                df = df[sheet_name]
            elif len(df) == 1:
                df = next(iter(df.values()))
            else:
                raise ValueError(f"Excel sheet {sheet_name!r} did not resolve to one table")
    elif spec.read_kind == "json" or suffix == ".json":
        df = pd.read_json(meta.path)
        if isinstance(df, dict):
            df = pd.DataFrame(df)
    else:
        encodings = [spec.encoding, "utf-8-sig", "utf-8", "utf-16", "latin1"]
        last_error: Optional[Exception] = None
        for encoding in [item for item in encodings if item]:
            try:
                df = pd.read_csv(
                    meta.path,
                    sep=spec.delimiter or meta.delimiter_hint or ",",
                    header=spec.header_row,
                    dtype=str,
                    encoding=encoding,
                    low_memory=False,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        else:
            raise ValueError(f"Unable to read delimited file {meta.path}: {last_error}") from last_error

    df.columns = [str(column).replace("\ufeff", "").strip() for column in df.columns]
    return df


def build_parse_qc(
    rows_loaded: int,
    rows_after_filters: int,
    timestamps: pd.Series,
    glucose: pd.Series,
    subject: pd.Series,
) -> dict[str, Any]:
    timestamp_rate = float(timestamps.notna().mean()) if len(timestamps) else 0.0
    glucose_rate = float(glucose.notna().mean()) if len(glucose) else 0.0
    unknown_subject_rate = float(subject.eq("UNKNOWN").mean()) if len(subject) else 0.0
    return {
        "rows_loaded": rows_loaded,
        "rows_after_filters": rows_after_filters,
        "timestamp_parse_rate": timestamp_rate,
        "glucose_parse_rate": glucose_rate,
        "unknown_subject_rate": unknown_subject_rate,
    }


def has_cgm_semantic_evidence(meta: FileMeta, spec: ParseSpec) -> bool:
    glucose_name = normalize_column_name(spec.glucose_column)
    blocked = ("calorie", "calories", "cals", "steps", "sleep", "heart", "heartrate", "respiratory")
    if any(token in glucose_name for token in blocked):
        return False
    joined = normalize_column_name(
        " ".join(
            [
                spec.glucose_column,
                " ".join(effective_header_columns(meta)),
                meta.preview,
                str((meta.profile or {}).get("likely_units", "")),
            ]
        )
    )
    evidence = (
        "glucose",
        "glucemia",
        "glucosa",
        "bloodglucose",
        "sensorglucose",
        "cgm",
        "mgdl",
        "mmoll",
        "navsg",
    )
    return any(token in joined for token in evidence) or glucose_name == "gl"


def extract_with_spec(meta: FileMeta, spec: ParseSpec) -> tuple[pd.DataFrame, dict[str, Any], str]:
    df = read_table(meta, spec)
    spec = resolve_columns(spec, df.columns)
    if spec.glucose_column not in df.columns:
        raise ValueError(f"Glucose column {spec.glucose_column!r} not found in {meta.relative_path}")
    if not has_cgm_semantic_evidence(meta, spec):
        raise ValueError(f"Glucose column {spec.glucose_column!r} lacks CGM semantic evidence in {meta.relative_path}")
    filtered = apply_row_filters(df, spec.row_filters)
    timestamps = build_timestamp_series(filtered, spec)
    glucose = pd.to_numeric(clean_string_series(filtered[spec.glucose_column]), errors="coerce").astype(float)
    if (spec.glucose_unit or "").lower() == "mmol/l":
        glucose = glucose * 18.0
    subject, subject_strategy = build_subject_series(filtered, spec, meta.path)
    parse_qc = build_parse_qc(len(df), len(filtered), timestamps, glucose, subject)
    if len(filtered) < spec.min_rows:
        raise ValueError(f"Filtered data produced too few rows: {len(filtered)} < {spec.min_rows}")
    if parse_qc["timestamp_parse_rate"] < spec.timestamp_parse_min_rate:
        raise ValueError(f"Timestamp parse rate too low: {parse_qc['timestamp_parse_rate']:.3f}")
    if parse_qc["glucose_parse_rate"] < spec.glucose_parse_min_rate:
        raise ValueError(f"Glucose parse rate too low: {parse_qc['glucose_parse_rate']:.3f}")
    out = pd.DataFrame({"Timestamp": timestamps, "Glucose": glucose, "Subject_ID": subject.astype(str)})
    return out[["Timestamp", "Glucose", "Subject_ID"]], parse_qc, subject_strategy


def canonicalize_timestamp_strings(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=True)
    parsed = parsed.dt.tz_convert(None).dt.floor("s")
    return parsed.dt.strftime("%Y-%m-%d %H:%M:%S")


def meta_source_label(meta: FileMeta) -> str:
    return meta.relative_path


def clean_output_name(source_path: Path, source_label: str | None = None) -> str:
    label = source_label or str(source_path)
    short = hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]
    return f"{source_path.stem}__{short}_clean.csv"


def clean_extracted_frame(df: pd.DataFrame, source_path: Path, source_label: str | None = None) -> tuple[pd.DataFrame, int, int]:
    rows_in = len(df)
    cleaned = df.copy()
    cleaned["Timestamp"] = pd.to_datetime(cleaned["Timestamp"], errors="coerce", format="mixed", utc=True).dt.tz_convert(None).dt.floor("s")
    cleaned["Glucose"] = pd.to_numeric(cleaned["Glucose"], errors="coerce").astype(float)
    cleaned["Subject_ID"] = clean_string_series(cleaned["Subject_ID"]).fillna("UNKNOWN").astype(str)
    cleaned = cleaned.dropna(subset=["Timestamp", "Glucose"]).copy()
    rows_out = len(cleaned)
    cleaned["Timestamp"] = cleaned["Timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    cleaned["Source_File"] = source_label or source_path.name
    cleaned = cleaned[["Timestamp", "Glucose", "Subject_ID", "Source_File"]]
    return cleaned, rows_in, rows_out


def clean_and_write(df: pd.DataFrame, out_dir: Path, source_path: Path, source_label: str | None = None) -> tuple[str, int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cleaned, rows_in, rows_out = clean_extracted_frame(df, source_path, source_label)
    out_file = out_dir / clean_output_name(source_path, source_label)
    cleaned.to_csv(out_file, index=False, quoting=csv.QUOTE_MINIMAL)
    return out_file.name, rows_in, rows_out


class LLMClient:
    def __init__(self, cgm_model: str = DEFAULT_CGM_MODEL, default_model: str = DEFAULT_GENERAL_MODEL):
        self.cgm_model = resolve_model_spec(cgm_model)
        self.default_model = resolve_model_spec(default_model)
        self.clients: dict[str, Any] = {}
        self.model_cooldowns: dict[str, float] = {}
        self.request_delay_seconds = self._request_delay_seconds()

    def _request_delay_seconds(self) -> float:
        for env_name in (
            "LLM_REQUEST_DELAY_SECONDS",
            "GEMINI_REQUEST_DELAY_SECONDS",
            "ANTHROPIC_REQUEST_DELAY_SECONDS",
        ):
            raw_value = os.environ.get(env_name)
            if raw_value:
                try:
                    return max(0.0, float(raw_value))
                except ValueError:
                    logger.warning("invalid_request_delay", env=env_name, value=raw_value)
        return 0.0

    def _throttle_request(self) -> None:
        if self.request_delay_seconds > 0:
            time.sleep(self.request_delay_seconds)

    def _max_retries(self, provider: str) -> int:
        env_names = [f"{provider.upper()}_OPENAI_MAX_RETRIES", "LLM_OPENAI_MAX_RETRIES"]
        for env_name in env_names:
            raw_value = os.environ.get(env_name)
            if raw_value:
                try:
                    return max(0, int(raw_value))
                except ValueError:
                    logger.warning("invalid_max_retries", env=env_name, value=raw_value)
        return 0 if provider == "google" else 5

    def _ensure_model_available(self, model: ModelSpec) -> None:
        cooldown_until = self.model_cooldowns.get(model.model)
        if not cooldown_until:
            return
        remaining = cooldown_until - time.monotonic()
        if remaining > 0:
            raise ProviderQuotaError(
                f"{model.model} quota is cooling down for {remaining:.0f}s after a provider 429."
            )
        self.model_cooldowns.pop(model.model, None)

    def _extract_retry_delay_seconds(self, exc: Exception) -> Optional[float]:
        body = getattr(exc, "body", None)
        details = body.get("error", {}).get("details", []) if isinstance(body, dict) else []
        for detail in details:
            if isinstance(detail, dict):
                raw_delay = detail.get("retryDelay")
                if isinstance(raw_delay, str):
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", raw_delay)
                    if match:
                        return float(match.group(1))

        message = str(exc)
        match = re.search(r"retryDelay['\"]?:\s*['\"](\d+(?:\.\d+)?)s", message)
        if match:
            return float(match.group(1))
        match = re.search(r"Please retry in (?:(\d+)m)?(\d+(?:\.\d+)?)s", message)
        if match:
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2))
            return minutes * 60 + seconds
        return None

    def _raise_google_quota_error(self, exc: RateLimitError, model: ModelSpec) -> None:
        retry_delay = self._extract_retry_delay_seconds(exc)
        if retry_delay:
            self.model_cooldowns[model.model] = time.monotonic() + retry_delay
            logger.warning(
                "google_quota_cooldown",
                model=model.model,
                retry_delay_seconds=round(retry_delay, 3),
            )
            raise ProviderQuotaError(
                f"{model.model} quota exhausted; provider asked to retry in {retry_delay:.0f}s."
            ) from exc
        raise ProviderQuotaError(f"{model.model} quota exhausted.") from exc

    def _openai_client(self, provider: str) -> OpenAI:
        if provider == "google":
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set; falling back to deterministic heuristics.")
            cache_key = "google"
            if cache_key not in self.clients:
                self.clients[cache_key] = OpenAI(
                    api_key=key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    max_retries=self._max_retries("google"),
                    timeout=100,
                )
            return self.clients[cache_key]

        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set; falling back to deterministic heuristics.")
        cache_key = "openai"
        if cache_key not in self.clients:
            self.clients[cache_key] = OpenAI(max_retries=self._max_retries("openai"), timeout=100)
        return self.clients[cache_key]

    def _anthropic_client(self) -> Any:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set; falling back to deterministic heuristics.")
        if "anthropic" not in self.clients:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "The anthropic package is not installed; run pip install -r harmony/requirements.txt."
                ) from exc
            self.clients["anthropic"] = anthropic.Anthropic(max_retries=5, timeout=100)
        return self.clients["anthropic"]

    def _system_message(self) -> str:
        return (
            "Return only valid JSON. The JSON must match the provided schema exactly. "
            "Do not include markdown fences or commentary."
        )

    def _user_message(self, prompt: str, schema: dict[str, Any]) -> str:
        schema_text = json.dumps(schema, indent=2, sort_keys=True)
        return f"{prompt}\n\n<JSON_SCHEMA>\n{schema_text}\n</JSON_SCHEMA>"

    def _parse_json_payload(self, payload: str) -> dict[str, Any]:
        stripped = payload.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL).strip()
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                start = stripped.find("{")
                end = stripped.rfind("}")
                if start == -1 or end == -1 or end <= start:
                    raise
                return json.loads(stripped[start : end + 1])

    def _chat_json_openai(self, prompt: str, schema: dict[str, Any], model: ModelSpec) -> dict[str, Any]:
        self._throttle_request()
        response = self._openai_client("openai").chat.completions.create(
            model=model.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._system_message()},
                {"role": "user", "content": self._user_message(prompt, schema)},
            ],
        )
        payload = response.choices[0].message.content or "{}"
        return self._parse_json_payload(payload)

    def _chat_json_google(self, prompt: str, schema: dict[str, Any], model: ModelSpec) -> dict[str, Any]:
        self._ensure_model_available(model)
        self._throttle_request()
        try:
            response = self._openai_client("google").chat.completions.create(
                model=model.model,
                messages=[
                    {"role": "system", "content": self._system_message()},
                    {"role": "user", "content": self._user_message(prompt, schema)},
                ],
            )
        except RateLimitError as exc:
            self._raise_google_quota_error(exc, model)
        payload = response.choices[0].message.content or "{}"
        return self._parse_json_payload(payload)

    def _chat_json_anthropic(self, prompt: str, schema: dict[str, Any], model: ModelSpec) -> dict[str, Any]:
        self._throttle_request()
        response = self._anthropic_client().messages.create(
            model=model.model,
            max_tokens=4096,
            system=self._system_message(),
            messages=[{"role": "user", "content": self._user_message(prompt, schema)}],
        )
        parts: list[str] = []
        for block in getattr(response, "content", []):
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        return self._parse_json_payload("\n".join(parts) or "{}")

    def _chat_json(self, prompt: str, schema: dict[str, Any], model: str | ModelSpec) -> dict[str, Any]:
        model_spec = model if isinstance(model, ModelSpec) else resolve_model_spec(model)
        if model_spec.provider == "anthropic":
            return self._chat_json_anthropic(prompt, schema, model_spec)
        if model_spec.provider == "google":
            return self._chat_json_google(prompt, schema, model_spec)
        return self._chat_json_openai(prompt, schema, model_spec)

    def triage_file(self, meta: FileMeta, dir_tree: str) -> FileTriageDecision:
        hard = hard_negative_decision(meta)
        if hard:
            return hard
        heuristic = heuristic_triage(meta)
        if heuristic.file_role in ALLOWED_CGM_ROLES and is_high_confidence_cgm_schema(meta.header_columns):
            return heuristic
        prompt = textwrap.dedent(TRIAGE_PROMPT).replace("$relative_path", meta.relative_path).replace(
            "$mime_type", meta.mime
        ).replace("$header_columns", json.dumps(meta.header_columns)).replace(
            "$file_profile", file_profile_json(meta)
        ).replace(
            "$sibling_files", json.dumps(meta.sibling_examples)
        ).replace("$dir_tree", dir_tree).replace("$preview", meta.preview)
        try:
            data = self._chat_json(prompt, TRIAGE_SCHEMA, self.cgm_model)
            triage = FileTriageDecision.model_validate(data)
            if not triage.schema_fingerprint:
                triage.schema_fingerprint = heuristic.schema_fingerprint
            if not triage.candidate_timestamp_columns:
                triage.candidate_timestamp_columns = heuristic.candidate_timestamp_columns
            if not triage.candidate_glucose_columns:
                triage.candidate_glucose_columns = heuristic.candidate_glucose_columns
            if not triage.candidate_subject_columns:
                triage.candidate_subject_columns = heuristic.candidate_subject_columns
            if triage.file_role in ALLOWED_CGM_ROLES and (
                not triage.candidate_timestamp_columns or not triage.candidate_glucose_columns
            ):
                return heuristic
            return triage
        except Exception as exc:  # noqa: BLE001
            logger.warning("triage_fallback", file=meta.relative_path, error=str(exc))
            if heuristic.file_role in ALLOWED_CGM_ROLES and not is_high_confidence_cgm_schema(meta.header_columns):
                return deterministic_non_cgm_decision(
                    meta,
                    "LLM triage unavailable and deterministic schema evidence is insufficient for CGM.",
                )
            return heuristic

    def request_parse_spec(self, meta: FileMeta, triage: FileTriageDecision) -> ParseSpec:
        heuristic = heuristic_parse_spec(meta, triage)
        prompt = textwrap.dedent(PARSE_SPEC_PROMPT).replace("$relative_path", meta.relative_path).replace(
            "$mime_type", meta.mime
        ).replace("$triage", triage.model_dump_json(indent=2)).replace(
            "$header_columns", json.dumps(meta.header_columns)
        ).replace("$file_profile", file_profile_json(meta)).replace("$preview", meta.preview)
        try:
            data = self._chat_json(prompt, PARSE_SPEC_SCHEMA, self.default_model)
            spec = ParseSpec.model_validate(data)
            if not spec.schema_fingerprint:
                spec = spec.model_copy(update={"schema_fingerprint": triage.schema_fingerprint})
            if not spec.timestamp_groups:
                spec = spec.model_copy(update={"timestamp_groups": heuristic.timestamp_groups})
            if any(is_relative_day_column(column) for group in spec.timestamp_groups for column in group):
                spec = spec.model_copy(update={"time_only_rollover": False})
            return spec
        except Exception as exc:  # noqa: BLE001
            logger.warning("parse_spec_fallback", file=meta.relative_path, error=str(exc))
            return heuristic

    def request_parse_spec_repair(
        self,
        meta: FileMeta,
        triage: FileTriageDecision,
        failed_spec: ParseSpec,
        failure_error: Exception | str,
    ) -> ParseSpec:
        prompt = textwrap.dedent(REPAIR_PARSE_SPEC_PROMPT).replace("$relative_path", meta.relative_path).replace(
            "$mime_type", meta.mime
        ).replace("$triage", triage.model_dump_json(indent=2)).replace(
            "$failed_spec", failed_spec.model_dump_json(indent=2)
        ).replace("$failure_error", str(failure_error)).replace(
            "$header_columns", json.dumps(meta.header_columns)
        ).replace("$file_profile", file_profile_json(meta)).replace("$preview", meta.preview)
        data = self._chat_json(prompt, PARSE_SPEC_SCHEMA, self.default_model)
        spec = ParseSpec.model_validate(data)
        if not spec.schema_fingerprint:
            spec = spec.model_copy(update={"schema_fingerprint": triage.schema_fingerprint})
        if any(is_relative_day_column(column) for group in spec.timestamp_groups for column in group):
            spec = spec.model_copy(update={"time_only_rollover": False})
        return spec


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def sample_key_hashes(clean_path: Path, approx_rows: int, modulo: int) -> set[int]:
    sample: set[int] = set()
    for chunk in pd.read_csv(clean_path, usecols=["Subject_ID", "Timestamp"], chunksize=200_000):
        keys = chunk["Subject_ID"].astype(str).str.strip() + "|" + chunk["Timestamp"].astype(str)
        hashes = pd.util.hash_pandas_object(keys, index=False).astype("uint64")
        kept = hashes[hashes % modulo == 0]
        sample.update(int(value) for value in np.unique(kept.to_numpy()))
    return sample


def sample_key_hashes_frame(df: pd.DataFrame, modulo: int) -> set[int]:
    if df.empty:
        return set()
    keys = df["Subject_ID"].astype(str).str.strip() + "|" + df["Timestamp"].astype(str)
    hashes = pd.util.hash_pandas_object(keys, index=False).astype("uint64")
    kept = hashes[hashes % modulo == 0]
    return {int(value) for value in np.unique(kept.to_numpy())}


def build_source_overlap_report_from_frames(
    processed_entries: list[ManifestEntry],
    clean_frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    processed = [entry for entry in processed_entries if entry.clean and entry.clean in clean_frames]
    if not processed:
        return {"files": [], "pairs": []}

    max_rows = max(entry.rows_out or 0 for entry in processed)
    modulo = max(1, max_rows // 200_000)
    samples: dict[str, set[int]] = {}
    for entry in processed:
        clean_name = entry.clean or ""
        df = clean_frames[clean_name]
        files.append(
            {
                "clean": entry.clean,
                "source": entry.source,
                "rows_out": entry.rows_out,
                "unique_subjects": int(df["Subject_ID"].nunique()),
                "timestamp_min": df["Timestamp"].min() if not df.empty else None,
                "timestamp_max": df["Timestamp"].max() if not df.empty else None,
                "file_role": entry.file_role,
            }
        )
        samples[clean_name] = sample_key_hashes_frame(df, modulo)

    pairs: list[dict[str, Any]] = []
    names = [entry.clean or "" for entry in processed]
    for idx, left in enumerate(names):
        for right in names[idx + 1 :]:
            left_sample = samples[left]
            right_sample = samples[right]
            if not left_sample or not right_sample:
                overlap_left = overlap_right = 0.0
            else:
                intersection = len(left_sample & right_sample)
                overlap_left = float(intersection / len(left_sample))
                overlap_right = float(intersection / len(right_sample))
            pairs.append(
                {
                    "left_clean": left,
                    "right_clean": right,
                    "overlap_left": overlap_left,
                    "overlap_right": overlap_right,
                }
            )
    return {"sample_modulo": modulo, "files": files, "pairs": pairs}


def build_source_overlap_report(out_dir: Path, processed_entries: list[ManifestEntry]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    processed = [entry for entry in processed_entries if entry.clean]
    if not processed:
        return {"files": [], "pairs": []}
    max_rows = max(entry.rows_out or 0 for entry in processed)
    modulo = max(1, max_rows // 200_000)
    samples: dict[str, set[int]] = {}
    for entry in processed:
        clean_path = out_dir / (entry.clean or "")
        df = pd.read_csv(clean_path, usecols=["Timestamp", "Subject_ID", "Source_File"])
        files.append(
            {
                "clean": entry.clean,
                "source": entry.source,
                "rows_out": entry.rows_out,
                "unique_subjects": int(df["Subject_ID"].nunique()),
                "timestamp_min": df["Timestamp"].min() if not df.empty else None,
                "timestamp_max": df["Timestamp"].max() if not df.empty else None,
                "file_role": entry.file_role,
            }
        )
        samples[entry.clean or ""] = sample_key_hashes(clean_path, entry.rows_out or 0, modulo)

    pairs: list[dict[str, Any]] = []
    names = [entry.clean or "" for entry in processed]
    for idx, left in enumerate(names):
        for right in names[idx + 1 :]:
            left_sample = samples[left]
            right_sample = samples[right]
            if not left_sample or not right_sample:
                overlap_left = overlap_right = 0.0
            else:
                intersection = len(left_sample & right_sample)
                overlap_left = float(intersection / len(left_sample))
                overlap_right = float(intersection / len(right_sample))
            pairs.append(
                {
                    "left_clean": left,
                    "right_clean": right,
                    "overlap_left": overlap_left,
                    "overlap_right": overlap_right,
                }
            )
    return {"sample_modulo": modulo, "files": files, "pairs": pairs}


def default_merge_plan(dataset_name: str, entries: list[ManifestEntry], overlap_report: dict[str, Any]) -> dict[str, dict[str, str]]:
    by_clean = {entry.clean: entry for entry in entries if entry.clean}
    plan: dict[str, dict[str, str]] = {}
    dataset_cluster_prefix = normalize_column_name(dataset_name) or "dataset"

    def assign(clean_name: str, cluster: str, action: str) -> None:
        if clean_name:
            plan[clean_name] = {"merge_cluster": cluster, "merge_action": action}

    for clean_name, entry in by_clean.items():
        source_stem = normalize_column_name(Path(entry.source).stem) or Path(entry.clean or clean_name).stem
        assign(clean_name, f"{dataset_cluster_prefix}::{source_stem}", "union_distinct_sources")

    pairs = sorted(
        overlap_report.get("pairs", []),
        key=lambda pair: max(float(pair.get("overlap_left", 0.0)), float(pair.get("overlap_right", 0.0))),
        reverse=True,
    )
    for pair in pairs:
        left = pair.get("left_clean")
        right = pair.get("right_clean")
        if left not in plan or right not in plan:
            continue

        overlap_left = float(pair.get("overlap_left", 0.0))
        overlap_right = float(pair.get("overlap_right", 0.0))
        if overlap_left >= 0.98 and overlap_right >= 0.98:
            plan[right]["merge_action"] = "drop_full_duplicate_export"
            plan[right]["merge_cluster"] = plan[left]["merge_cluster"]
        elif overlap_left >= 0.6 or overlap_right >= 0.6:
            cluster = min(plan[left]["merge_cluster"], plan[right]["merge_cluster"])
            plan[left]["merge_cluster"] = cluster
            plan[right]["merge_cluster"] = cluster
            if plan[right]["merge_action"] == "union_distinct_sources":
                plan[right]["merge_action"] = "prefer_canonical_export"
    return plan


ACTION_PRIORITY = {
    "union_distinct_sources": 0,
    "needs_review": 1,
    "prefer_canonical_export": 2,
    "drop_full_duplicate_export": 3,
}


def combine_clean_frames(entries: list[ManifestEntry], clean_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    processed = [entry for entry in entries if entry.clean]
    if not processed:
        return pd.DataFrame(columns=["Timestamp", "Glucose", "Subject_ID", "Source_File"])

    cluster_groups: dict[str, list[ManifestEntry]] = defaultdict(list)
    for entry in processed:
        cluster_groups[entry.merge_cluster or entry.clean or "default"].append(entry)

    merged_frames: list[pd.DataFrame] = []
    for cluster_name in sorted(cluster_groups):
        cluster_entries = sorted(
            cluster_groups[cluster_name],
            key=lambda entry: ACTION_PRIORITY.get(entry.merge_action or "needs_review", 99),
        )
        cluster_frames: list[pd.DataFrame] = []
        for entry in cluster_entries:
            if entry.merge_action == "drop_full_duplicate_export":
                continue
            df = clean_frames.get(entry.clean or "")
            if df is None:
                continue
            df = df.copy()
            df["__merge_action"] = entry.merge_action or "needs_review"
            cluster_frames.append(df)
        if not cluster_frames:
            continue
        cluster_df = pd.concat(cluster_frames, ignore_index=True)
        dedupe_actions = {"prefer_canonical_export"}
        if (cluster_df["__merge_action"].isin(dedupe_actions)).any():
            priority = cluster_df["__merge_action"].map(ACTION_PRIORITY).fillna(99)
            cluster_df = cluster_df.assign(__priority=priority)
            cluster_df = cluster_df.sort_values(["__priority"]).drop_duplicates(
                subset=["Subject_ID", "Timestamp"],
                keep="first",
            )
        cluster_df = cluster_df.drop(columns=["__merge_action"], errors="ignore")
        cluster_df = cluster_df.drop(columns=["__priority"], errors="ignore")
        merged_frames.append(cluster_df)

    combined = pd.concat(merged_frames, ignore_index=True) if merged_frames else pd.DataFrame(
        columns=["Timestamp", "Glucose", "Subject_ID", "Source_File"]
    )
    combined["Timestamp"] = canonicalize_timestamp_strings(combined["Timestamp"])
    return combined


def combine_clean_files(out_dir: Path, combined_csv: Path, entries: list[ManifestEntry]) -> None:
    processed = [entry for entry in entries if entry.clean]
    if not processed:
        empty = pd.DataFrame(columns=["Timestamp", "Glucose", "Subject_ID", "Source_File"])
        empty.to_csv(combined_csv, index=False)
        logger.warning("no_clean_files_found", dir=str(out_dir))
        return

    clean_frames = {
        entry.clean or "": pd.read_csv(out_dir / (entry.clean or ""))
        for entry in processed
        if entry.clean and (out_dir / entry.clean).exists()
    }
    combined = combine_clean_frames(entries, clean_frames)
    combined.to_csv(combined_csv, index=False)
    logger.info("combined_written", rows=len(combined), out=str(combined_csv))


def configure_logging(log_path: Path, level: int = logging.INFO) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"),
        ],
        format="%(message)s",
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _models_payload(llm: LLMClient) -> dict[str, str]:
    return {
        "cgm_model": llm.cgm_model.model,
        "default_model": llm.default_model.model,
        "cgm_provider": llm.cgm_model.provider,
        "default_provider": llm.default_model.provider,
    }


def _extract_dataset_entries(
    root: Path,
    out: Path,
    cgm_model: str,
    default_model: str,
    *,
    write_clean_files: bool,
) -> tuple[LLMClient, Counter[str], list[ManifestEntry], list[str], dict[str, pd.DataFrame]]:
    debug_root = out / "debug"
    triage_root = debug_root / "triage"
    spec_root = debug_root / "specs"
    llm = LLMClient(cgm_model=cgm_model, default_model=default_model)
    dir_tree = build_dir_tree(root)

    all_files = walk_files(root)
    text_files = [meta for meta in all_files if is_text_like(meta)]
    role_counts: Counter[str] = Counter()
    triage_cache: dict[str, FileTriageDecision] = {}
    parse_spec_cache: dict[str, ParseSpec] = {}
    manifest_entries: list[ManifestEntry] = []
    dataset_warnings: list[str] = []
    clean_frames: dict[str, pd.DataFrame] = {}

    logger.info("files_found", total_files=len(all_files), text_like_files=len(text_files))

    for meta in text_files:
        triage_key = deterministic_schema_fingerprint(meta)
        triage = triage_cache.get(triage_key)
        if triage is None:
            triage = llm.triage_file(meta, dir_tree)
            triage_cache[triage_key] = triage
        role_counts[triage.file_role] += 1
        triage_path = triage_root / f"{meta.path.stem}__{deterministic_schema_fingerprint(meta)}.json"
        write_json(triage_path, triage.model_dump())
        if triage.file_role not in ALLOWED_CGM_ROLES:
            continue

        entry = ManifestEntry(
            source=str(meta.path) if not meta.table_path else f"{meta.path}#{meta.table_path}",
            llm_reason=triage.reason,
            file_role=triage.file_role,
            triage_confidence=triage.confidence,
            schema_fingerprint=triage.schema_fingerprint,
            parse_spec_version=PARSE_SPEC_VERSION,
        )

        spec = parse_spec_cache.get(triage.schema_fingerprint)
        if spec is None:
            spec = llm.request_parse_spec(meta, triage)
            parse_spec_cache[triage.schema_fingerprint] = spec
            spec_path = spec_root / f"{triage.schema_fingerprint}.json"
            write_json(spec_path, spec.model_dump())

        entry.loader_sha = triage.schema_fingerprint
        heuristic_spec = heuristic_parse_spec(meta, triage)
        try:
            specs_to_try: list[ParseSpec] = [spec]
            if json.dumps(heuristic_spec.model_dump(), sort_keys=True) != json.dumps(spec.model_dump(), sort_keys=True):
                specs_to_try.append(heuristic_spec)

            last_error: Optional[Exception] = None
            failed_spec_for_repair: Optional[ParseSpec] = None
            best_result: Optional[tuple[pd.DataFrame, dict[str, Any], str]] = None
            best_score: tuple[float, float, float, int] = (-1.0, -1.0, -1.0, -1)
            for candidate_spec in specs_to_try:
                try:
                    extracted, parse_qc, subject_strategy = extract_with_spec(meta, candidate_spec)
                    score = (
                        1.0 - float(parse_qc.get("unknown_subject_rate", 1.0)),
                        float(parse_qc.get("timestamp_parse_rate", 0.0)),
                        float(parse_qc.get("glucose_parse_rate", 0.0)),
                        int(parse_qc.get("rows_after_filters", 0)),
                    )
                    if score > best_score:
                        best_score = score
                        best_result = (extracted, parse_qc, subject_strategy)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    failed_spec_for_repair = candidate_spec
            if best_result is None and failure_suggests_incomplete_schema_detection(last_error):
                repair_meta = meta_with_preview_lines(meta, REPAIR_PREVIEW_LINES)
                if repair_meta.preview != meta.preview:
                    try:
                        repair_spec = llm.request_parse_spec_repair(
                            repair_meta,
                            triage,
                            failed_spec_for_repair or spec,
                            last_error or "",
                        )
                        repair_spec_path = spec_root / f"{triage.schema_fingerprint}__repair.json"
                        write_json(repair_spec_path, repair_spec.model_dump())
                        repair_specs_to_try = [repair_spec]
                        repair_heuristic = heuristic_parse_spec(repair_meta, triage)
                        if json.dumps(repair_heuristic.model_dump(), sort_keys=True) != json.dumps(
                            repair_spec.model_dump(), sort_keys=True
                        ):
                            repair_specs_to_try.append(repair_heuristic)
                        for candidate_spec in repair_specs_to_try:
                            try:
                                extracted, parse_qc, subject_strategy = extract_with_spec(repair_meta, candidate_spec)
                                score = (
                                    1.0 - float(parse_qc.get("unknown_subject_rate", 1.0)),
                                    float(parse_qc.get("timestamp_parse_rate", 0.0)),
                                    float(parse_qc.get("glucose_parse_rate", 0.0)),
                                    int(parse_qc.get("rows_after_filters", 0)),
                                )
                                if score > best_score:
                                    best_score = score
                                    best_result = (extracted, parse_qc, subject_strategy)
                                    parse_spec_cache[triage.schema_fingerprint] = candidate_spec
                                    entry.warnings.append("parse_spec_repaired_with_expanded_preview")
                            except Exception as exc:  # noqa: BLE001
                                last_error = exc
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("parse_spec_repair_failed", file=meta.relative_path, error=str(exc))
                        last_error = exc
            if best_result is None:
                raise ValueError(str(last_error)) from last_error
            extracted, parse_qc, subject_strategy = best_result
            entry.parse_qc = parse_qc
            entry.subject_id_strategy = subject_strategy
            source_label = meta_source_label(meta)
            if write_clean_files:
                clean_name, rows_in, rows_out = clean_and_write(extracted, out, meta.path, source_label)
            else:
                clean_name = clean_output_name(meta.path, source_label)
                cleaned, rows_in, rows_out = clean_extracted_frame(extracted, meta.path, source_label)
                clean_frames[clean_name] = cleaned
            entry.clean = clean_name
            entry.rows_in = rows_in
            entry.rows_out = rows_out
        except Exception as exc:  # noqa: BLE001
            entry.warnings.append("parse_spec_failed")
            entry.parse_qc = {"error": str(exc)}
            dataset_warnings.append(f"{meta.relative_path}: {exc}")
        manifest_entries.append(entry)

    return llm, role_counts, manifest_entries, dataset_warnings, clean_frames


def _dataset_qc_payload(
    root: Path,
    processed_on: str,
    llm: LLMClient,
    role_counts: Counter[str],
    manifest_entries: list[ManifestEntry],
    dataset_warnings: list[str],
) -> dict[str, Any]:
    return {
        "dataset_root": str(root),
        "processed_on": processed_on,
        "models": _models_payload(llm),
        "triage_role_counts": dict(sorted(role_counts.items())),
        "warnings": dataset_warnings,
        "files_processed": sum(1 for entry in manifest_entries if entry.clean),
        "files_flagged": sum(1 for entry in manifest_entries if entry.warnings),
    }


def _manifest_payload(root: Path, processed_on: str, llm: LLMClient, manifest_entries: list[ManifestEntry]) -> dict[str, Any]:
    return {
        "dataset_root": str(root),
        "processed_on": processed_on,
        "models": _models_payload(llm),
        "files": [entry.model_dump() for entry in manifest_entries],
    }


def process_dataset(
    root: Path,
    out: Path,
    cgm_model: str = DEFAULT_CGM_MODEL,
    default_model: str = DEFAULT_GENERAL_MODEL,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    llm, role_counts, manifest_entries, dataset_warnings, _ = _extract_dataset_entries(
        root,
        out,
        cgm_model,
        default_model,
        write_clean_files=True,
    )

    overlap_report = build_source_overlap_report(out, manifest_entries)
    merge_plan = default_merge_plan(root.name, manifest_entries, overlap_report)
    for entry in manifest_entries:
        if entry.clean and entry.clean in merge_plan:
            entry.merge_cluster = merge_plan[entry.clean]["merge_cluster"]
            entry.merge_action = merge_plan[entry.clean]["merge_action"]
        elif entry.clean:
            entry.merge_cluster = entry.clean
            entry.merge_action = "needs_review"

    combine_clean_files(out, out / "combined_cgm.csv", manifest_entries)

    processed_on = pd.Timestamp.utcnow().isoformat()
    dataset_qc = _dataset_qc_payload(root, processed_on, llm, role_counts, manifest_entries, dataset_warnings)
    manifest = _manifest_payload(root, processed_on, llm, manifest_entries)
    write_json(out / "manifest.json", manifest)
    write_json(out / "dataset_qc.json", dataset_qc)
    write_json(out / "source_overlap.json", overlap_report)
    logger.info("processing_complete", out=str(out), n_files=len(manifest_entries))


def build_stats_summary(
    root: Path,
    processed_on: str,
    llm: LLMClient,
    manifest_entries: list[ManifestEntry],
    dataset_warnings: list[str],
    role_counts: Counter[str],
    combined: pd.DataFrame,
) -> dict[str, Any]:
    if combined.empty:
        subject_ids = pd.Series(dtype="string")
    else:
        subject_ids = clean_string_series(combined["Subject_ID"]).fillna("UNKNOWN").astype(str)
    known_subjects = subject_ids[subject_ids.str.upper() != "UNKNOWN"]
    source_files = [entry for entry in manifest_entries if entry.clean and (entry.rows_out or 0) > 0]
    source_files_used = [
        entry
        for entry in source_files
        if entry.merge_action != "drop_full_duplicate_export"
    ]

    return {
        "dataset": root.name,
        "dataset_root": str(root),
        "processed_on": processed_on,
        "models": _models_payload(llm),
        "participants": int(known_subjects.nunique()),
        "participants_including_unknown": int(subject_ids.nunique()),
        "unknown_subject_rows": int(subject_ids.str.upper().eq("UNKNOWN").sum()),
        "glucose_measurements": int(len(combined)),
        "cgm_source_files": int(len(source_files)),
        "cgm_source_files_used_after_merge": int(len(source_files_used)),
        "warnings": dataset_warnings,
        "triage_role_counts": dict(sorted(role_counts.items())),
        "per_file": [
            {
                "source": entry.source,
                "clean": entry.clean,
                "rows_in": entry.rows_in,
                "rows_out": entry.rows_out,
                "file_role": entry.file_role,
                "triage_confidence": entry.triage_confidence,
                "merge_cluster": entry.merge_cluster,
                "merge_action": entry.merge_action,
                "warnings": entry.warnings,
                "parse_qc": entry.parse_qc,
            }
            for entry in manifest_entries
        ],
    }


def process_dataset_stats_only(
    root: Path,
    out: Path,
    cgm_model: str = DEFAULT_GENERAL_MODEL,
    default_model: str = DEFAULT_GENERAL_MODEL,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    llm, role_counts, manifest_entries, dataset_warnings, clean_frames = _extract_dataset_entries(
        root,
        out,
        cgm_model,
        default_model,
        write_clean_files=False,
    )

    overlap_report = build_source_overlap_report_from_frames(manifest_entries, clean_frames)
    merge_plan = default_merge_plan(root.name, manifest_entries, overlap_report)
    for entry in manifest_entries:
        if entry.clean and entry.clean in merge_plan:
            entry.merge_cluster = merge_plan[entry.clean]["merge_cluster"]
            entry.merge_action = merge_plan[entry.clean]["merge_action"]
        elif entry.clean:
            entry.merge_cluster = entry.clean
            entry.merge_action = "needs_review"

    combined = combine_clean_frames(manifest_entries, clean_frames)
    processed_on = pd.Timestamp.utcnow().isoformat()
    dataset_qc = _dataset_qc_payload(root, processed_on, llm, role_counts, manifest_entries, dataset_warnings)
    manifest = _manifest_payload(root, processed_on, llm, manifest_entries)
    stats_summary = build_stats_summary(
        root,
        processed_on,
        llm,
        manifest_entries,
        dataset_warnings,
        role_counts,
        combined,
    )
    write_json(out / "manifest.json", manifest)
    write_json(out / "dataset_qc.json", dataset_qc)
    write_json(out / "source_overlap.json", overlap_report)
    write_json(out / "stats_summary.json", stats_summary)
    logger.info("stats_processing_complete", out=str(out), n_files=len(manifest_entries), rows=len(combined))
    return stats_summary
