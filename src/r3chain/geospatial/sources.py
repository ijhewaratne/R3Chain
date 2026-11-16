"""Turn the layer sources of a scenario file into imported layers.

A scenario file names its GIS inputs in ``layer_sources``::

    layer_sources:
      - layer_id: corridors
        dataset_id: syn-gis-corridors
        mapping: gis/corridors.mapping.yaml

The file of each layer is the ``source_uri`` of its dataset, relative to the scenario file, and
must still match the dataset's checksum. The imported layers become part of the frozen scenario
revision, so a run never reads a GIS file again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from r3chain.domain.base import Identifier, NonEmptyStr, Record
from r3chain.domain.hashing import file_checksum
from r3chain.domain.records.study import Dataset
from r3chain.geospatial.importers import LayerImportError, import_layer, load_mapping


class LayerSource(Record):
    layer_id: Identifier
    dataset_id: Identifier
    mapping: NonEmptyStr


def resolve_layer_sources(data: Any, base: Path) -> tuple[Any, list[tuple[str, str]]]:
    """Import the layer sources of scenario data.

    Returns the data with ``layer_sources`` replaced by ``spatial_layers``, and a list of
    problems as pairs of field path and message. A layer with a problem is left out.
    """
    if not isinstance(data, dict) or "layer_sources" not in data:
        return data, []
    problems: list[tuple[str, str]] = []
    datasets = {
        item.get("dataset_id"): item for item in data.get("datasets", []) if isinstance(item, dict)
    }
    target_crs = ((data.get("study") or {}).get("study_area") or {}).get("crs")
    layers: list[dict[str, Any]] = list(data.get("spatial_layers", []))
    for index, raw in enumerate(data.get("layer_sources") or []):
        try:
            source = LayerSource.model_validate(raw)
        except ValidationError as error:
            problems.append((f"layer_sources[{index}]", str(error.errors()[0]["msg"])))
            continue
        path = f"layer_sources[{source.layer_id}]"
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
        if not isinstance(target_crs, str):
            problems.append(("study.study_area.crs", "the study area states no reference system"))
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
            mapping = load_mapping(base / source.mapping)
            layer = import_layer(
                file,
                layer_id=source.layer_id,
                dataset_id=source.dataset_id,
                mapping=mapping,
                target_crs=target_crs,
            )
        except (OSError, ValidationError, LayerImportError) as error:
            problems.append((f"{path}.mapping", f"{type(error).__name__}: {error}"[:500]))
            continue
        layers.append(layer.to_json_dict())
    resolved = {name: value for name, value in data.items() if name != "layer_sources"}
    resolved["spatial_layers"] = layers
    return resolved, problems
