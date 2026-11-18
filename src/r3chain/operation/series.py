"""Load and verify interval time series referenced by a scenario file.

Like ``geospatial.sources``, every time series file must belong to a registered dataset with
valid permitted-use and origin metadata and an intact SHA-256 checksum before it is read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from r3chain.domain.base import Identifier, Record
from r3chain.domain.hashing import file_checksum
from r3chain.domain.records.study import Dataset
from r3chain.domain.timeseries import IntervalSeries


class SeriesSource(Record):
    series_id: Identifier
    dataset_id: Identifier


class SeriesImportError(Exception):
    """The time series file cannot be read or converted into canonical intervals."""


def _read_series_file(path: Path, series_id: str) -> IntervalSeries:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml", ".json"}:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
        if isinstance(raw, dict) and "series" in raw and isinstance(raw["series"], list):
            for entry in raw["series"]:
                if isinstance(entry, dict) and entry.get("series_id") == series_id:
                    return IntervalSeries.model_validate(entry)
            raise SeriesImportError(f"{path.name} contains no series named {series_id!r}")
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and entry.get("series_id") == series_id:
                    return IntervalSeries.model_validate(entry)
            raise SeriesImportError(f"{path.name} contains no series named {series_id!r}")
        return IntervalSeries.model_validate(raw)
    if suffix == ".parquet":
        try:
            import pandas as pd
        except ModuleNotFoundError as error:
            raise SeriesImportError(
                f"Parquet time series reader requires {error.name!r}. Install r3chain[gis]"
            ) from error
        frame = pd.read_parquet(path)
        missing_cols = {"start_utc", "duration_s", "value"} - set(frame.columns)
        if missing_cols:
            raise SeriesImportError(
                f"{path.name} is missing Parquet columns: {', '.join(sorted(missing_cols))}"
            )
        attrs = dict(getattr(frame, "attrs", {}))
        unit = attrs.get("unit")
        kind = attrs.get("kind")
        if not isinstance(unit, str) or not isinstance(kind, str):
            raise SeriesImportError(
                f"{path.name} Parquet attrs must declare canonical 'unit' and 'kind'"
            )
        return IntervalSeries.model_validate(
            {
                "series_id": series_id,
                "unit": unit,
                "kind": kind,
                "starts_utc": [str(item) for item in frame["start_utc"].tolist()],
                "durations_s": [float(item) for item in frame["duration_s"].tolist()],
                "values": [float(item) for item in frame["value"].tolist()],
            }
        )
    raise SeriesImportError(
        f"{path.suffix!r} is not a supported time series format. Use JSON, YAML or Parquet"
    )


def resolve_series_sources(data: Any, base: Path) -> tuple[Any, list[tuple[str, str]]]:
    """Resolve ``series_sources`` into frozen ``time_series`` entries on scenario data."""
    if not isinstance(data, dict) or "series_sources" not in data:
        return data, []
    problems: list[tuple[str, str]] = []
    datasets = {
        item.get("dataset_id"): item for item in data.get("datasets", []) if isinstance(item, dict)
    }
    stored: list[dict[str, Any]] = list(data.get("time_series", []))
    for index, raw in enumerate(data.get("series_sources") or []):
        try:
            source = SeriesSource.model_validate(raw)
        except ValidationError as error:
            problems.append((f"series_sources[{index}]", str(error.errors()[0]["msg"])))
            continue
        path = f"series_sources[{source.series_id}]"
        dataset = datasets.get(source.dataset_id)
        if dataset is None:
            problems.append(
                (f"{path}.dataset_id", f"dataset {source.dataset_id!r} is not registered")
            )
            continue
        try:
            validated_dataset = Dataset.model_validate(dataset)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            detail = f"{where}: {first['msg']}" if where else first["msg"]
            problems.append(
                (
                    f"{path}.dataset_id",
                    f"dataset {source.dataset_id!r} metadata is invalid ({detail})",
                )
            )
            continue
        file = base / str(validated_dataset.source_uri)
        if not file.is_file():
            problems.append(
                (
                    f"{path}.dataset_id",
                    f"the file of dataset {source.dataset_id} is missing: {file}",
                )
            )
            continue
        if file_checksum(file) != validated_dataset.checksum:
            problems.append(
                (
                    f"{path}.dataset_id",
                    f"{file.name} no longer matches the checksum of dataset {source.dataset_id}. "
                    "Register a new dataset version",
                )
            )
            continue
        try:
            loaded = _read_series_file(file, source.series_id)
        except (OSError, ValidationError, SeriesImportError) as error:
            problems.append((f"{path}.series_id", f"{type(error).__name__}: {error}"[:500]))
            continue
        stored.append(
            {
                "series_id": source.series_id,
                "dataset_id": source.dataset_id,
                "series": loaded.to_json_dict(),
            }
        )
    resolved = {name: value for name, value in data.items() if name != "series_sources"}
    resolved["time_series"] = stored
    return resolved, problems
