"""Importing GIS files into canonical layers (specification 8.1 and 8.2)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import geopandas
import pytest
import yaml
from shapely.geometry import shape

from r3chain.domain.records.spatial import (
    LAYER_SPECS,
    LayerKind,
    SpatialLayer,
    number_properties_carry_units,
)
from r3chain.domain.validation import load_scenario_file, read_data_file
from r3chain.geospatial.importers import (
    LayerImportError,
    LayerMapping,
    import_layer,
    load_mapping,
)
from r3chain.geospatial.sources import resolve_layer_sources
from tests.conftest import REPO_ROOT, SYNTHETIC_SCENARIO

GIS = REPO_ROOT / "examples" / "synthetic" / "gis"
STUDY_CRS = "EPSG:25832"


def synthetic(name: str) -> SpatialLayer:
    return import_layer(
        GIS / f"{name}.geojson",
        layer_id=name,
        dataset_id=f"syn-gis-{name}",
        mapping=load_mapping(GIS / f"{name}.mapping.yaml"),
        target_crs=STUDY_CRS,
    )


def mapping(name: str, **changes: Any) -> LayerMapping:
    data = yaml.safe_load((GIS / f"{name}.mapping.yaml").read_text(encoding="utf-8"))
    return LayerMapping.model_validate({**data, **changes})


def test_every_numeric_layer_property_carries_its_unit() -> None:
    assert number_properties_carry_units() == []
    assert set(LAYER_SPECS) == set(LayerKind)


@pytest.mark.requirement("W31")
def test_layers_are_reprojected_into_the_study_reference_system() -> None:
    # The synthetic files are WGS84 GeoJSON. Alpha's access street was drawn at x = 450 m,
    # y = 1000 m to 400 m on the invented grid that starts at (500000, 5700000).
    corridors = synthetic("corridors")
    assert corridors.source_crs in {"EPSG:4326", "EPSG:4979"}
    assert corridors.crs == STUDY_CRS
    street = next(item for item in corridors.features if item.feature_id == "c01")
    start, *_, end = street.geometry["coordinates"]
    assert start == pytest.approx([500450.0, 5701000.0, 164.0], abs=1e-3)
    assert end == pytest.approx([500450.0, 5700400.0, 160.0], abs=1e-3)
    # Heights are kept, and their datum comes from the mapping.
    assert corridors.vertical_datum == "DHHN2016" and corridors.has_heights()
    # Coordinates are rounded to the precision of the mapping.
    assert all(
        abs(value * 10_000 - round(value * 10_000)) < 1e-6
        for value in street.geometry["coordinates"][1][:2]
    )


def test_raw_values_are_translated_into_canonical_properties() -> None:
    corridors = {item.feature_id: item for item in synthetic("corridors").features}
    assert corridors["c01"].properties == {"corridor_kind": "street", "width_m": 6.0}
    assert corridors["c11"].properties == {"corridor_kind": "field_path", "width_m": 3.0}
    # The demand is given in kW and stored in W.
    demand = {item.feature_id: item for item in synthetic("demand").features}
    assert demand["committed-customer-1"].properties == {
        "design_heat_w": 200_000.0,
        "customer_status": "committed",
    }
    assert demand["potential-customer-1"].properties["customer_status"] == "potential"


@pytest.mark.requirement("W31")
def test_a_well_understood_defect_is_repaired_and_logged() -> None:
    restrictions = synthetic("restrictions")
    (repair,) = restrictions.repairs
    assert repair.feature_id == "R1"
    assert "Self-intersection" in repair.defect
    assert repair.size_change_fraction < 1e-3
    housing = next(item for item in restrictions.features if item.feature_id == "R1")
    assert shape(housing.geometry).is_valid
    # The repaired area is the rectangle and its tiny lobe: 140 m by 100 m plus 0.5 m2.
    assert shape(housing.geometry).area == pytest.approx(14_000.5, abs=0.01)


@pytest.mark.requirement("W31")
def test_an_ambiguous_geometry_is_rejected_for_review() -> None:
    restrictions = synthetic("restrictions")
    (rejected,) = restrictions.rejected
    assert rejected.feature_id == "R4"
    assert rejected.reason.startswith("ambiguous geometry")
    assert "Review it" in rejected.reason
    assert rejected.bounds == pytest.approx((503500.0, 5703500.0, 503600.0, 5703600.0), abs=1e-3)
    assert "R4" not in {item.feature_id for item in restrictions.features}


def _frame(name: str) -> Any:
    return geopandas.read_file(GIS / f"{name}.geojson")


@pytest.mark.parametrize("suffix", [".gpkg", ".parquet"])
@pytest.mark.requirement("W31")
def test_geopackage_and_geoparquet_give_the_same_layer(tmp_path: Path, suffix: str) -> None:
    frame = _frame("demand")
    path = tmp_path / f"demand{suffix}"
    if suffix == ".gpkg":
        frame.to_file(path, layer="kunden", driver="GPKG")
        layer_mapping = mapping("demand", source_layer="kunden")
    else:
        frame.to_parquet(path)
        layer_mapping = mapping("demand")
    imported = import_layer(
        path,
        layer_id="demand",
        dataset_id="syn-gis-demand",
        mapping=layer_mapping,
        target_crs=STUDY_CRS,
    )
    reference = synthetic("demand")
    assert [item.feature_id for item in imported.features] == [
        item.feature_id for item in reference.features
    ]
    for got, expected in zip(imported.features, reference.features, strict=True):
        assert got.properties == expected.properties
        assert got.geometry["coordinates"] == pytest.approx(
            expected.geometry["coordinates"], abs=1e-3
        )


def test_files_that_cannot_be_imported_say_why(tmp_path: Path) -> None:
    frame = _frame("demand")
    gpkg = tmp_path / "demand.gpkg"
    frame.to_file(gpkg, layer="kunden", driver="GPKG")
    with pytest.raises(LayerImportError, match="must name its source_layer"):
        import_layer(
            gpkg,
            layer_id="d",
            dataset_id="d",
            mapping=mapping("demand"),
            target_crs=STUDY_CRS,
        )
    with pytest.raises(LayerImportError, match="lacks the mapped columns: STATUS"):
        _missing_column(tmp_path)
    shapefile = tmp_path / "demand.shp"
    shapefile.write_text("not a supported format")
    with pytest.raises(LayerImportError, match="unsupported format"):
        import_layer(
            shapefile, layer_id="d", dataset_id="d", mapping=mapping("demand"), target_crs=STUDY_CRS
        )
    # Heights without a vertical datum are refused.
    with pytest.raises(LayerImportError, match="vertical_datum"):
        import_layer(
            GIS / "corridors.geojson",
            layer_id="c",
            dataset_id="c",
            mapping=mapping("corridors", vertical_datum=None),
            target_crs=STUDY_CRS,
        )


def _missing_column(tmp_path: Path) -> None:
    data = json.loads((GIS / "demand.geojson").read_text(encoding="utf-8"))
    for feature in data["features"]:
        del feature["properties"]["STATUS"]
    path = tmp_path / "without-status.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    import_layer(
        path, layer_id="d", dataset_id="d", mapping=mapping("demand"), target_crs=STUDY_CRS
    )


def test_untranslatable_values_are_left_out_and_reported(tmp_path: Path) -> None:
    data = json.loads((GIS / "demand.geojson").read_text(encoding="utf-8"))
    data["features"][0]["properties"]["STATUS"] = "vielleicht"
    data["features"][1]["properties"]["LEISTUNG_KW"] = None
    data["features"][2]["properties"]["NOTIZ"] = "a column nobody mapped"
    path = tmp_path / "demand.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    layer = import_layer(
        path, layer_id="d", dataset_id="d", mapping=mapping("demand"), target_crs=STUDY_CRS
    )
    first = next(item for item in layer.features if item.feature_id == "potential-customer-1")
    # No default is filled in. The value is simply not there, and the import says so.
    assert "customer_status" not in first.properties
    committed = next(item for item in layer.features if item.feature_id == "committed-customer-1")
    assert "design_heat_w" not in committed.properties
    assert any("'vielleicht' is not in the value map" in line for line in layer.import_warnings)
    assert "column NOTIZ is not mapped and is not imported" in layer.import_warnings


def test_a_mapping_must_fit_its_layer_kind() -> None:
    with pytest.raises(ValueError, match="must map: customer_status"):
        mapping("demand", properties=[{"source": "LEISTUNG_KW", "target": "design_heat_w"}])
    with pytest.raises(ValueError, match="'colour' is not a property"):
        mapping("barriers", properties=[{"source": "ART", "target": "colour"}])
    with pytest.raises(ValueError, match="is a number and cannot have a value map"):
        mapping(
            "corridors",
            properties=[
                {"source": "TYP", "target": "corridor_kind"},
                {"source": "BREITE_M", "target": "width_m", "values": {"x": "y"}},
            ],
        )


def test_layer_sources_are_imported_when_a_scenario_file_is_loaded(tmp_path: Path) -> None:
    scenario, report = load_scenario_file(SYNTHETIC_SCENARIO)
    assert scenario is not None and report.executable
    assert [layer.layer_id for layer in scenario.spatial_layers] == [
        "targets",
        "restrictions",
        "jurisdictions",
        "corridors",
        "barriers",
        "demand",
    ]
    # The same files always give the same layers, so the revision is stable.
    again, _ = load_scenario_file(SYNTHETIC_SCENARIO)
    assert again is not None and again.revision_id() == scenario.revision_id()

    # A file that no longer matches its dataset checksum is refused.
    copy = tmp_path / "study"
    shutil.copytree(SYNTHETIC_SCENARIO.parent, copy)
    corridors = copy / "gis" / "corridors.geojson"
    corridors.write_text(
        corridors.read_text(encoding="utf-8").replace("Feldweg", "Weg"), encoding="utf-8"
    )
    data, problems = resolve_layer_sources(read_data_file(copy / "scenario.yaml"), copy)
    assert problems == [
        (
            "layer_sources[corridors].dataset_id",
            "corridors.geojson no longer matches the checksum of dataset syn-gis-corridors. "
            "Register a new dataset version",
        )
    ]
    assert "corridors" not in {layer["layer_id"] for layer in data["spatial_layers"]}
    _, report = load_scenario_file(copy / "scenario.yaml")
    assert not report.executable
    assert [(issue.code, issue.field_path) for issue in report.errors] == [
        ("layer_import", "layer_sources[corridors].dataset_id")
    ]


def test_a_layer_in_another_reference_system_is_an_error(scenario_data: dict[str, Any]) -> None:
    from r3chain.domain.validation import parse_scenario

    layers = scenario_data["spatial_layers"]
    layers[0]["crs"] = "EPSG:4326"
    _, report = parse_scenario(scenario_data)
    assert ("wrong_reference_system", "spatial_layers[targets].crs") in [
        (issue.code, issue.field_path) for issue in report.errors
    ]


@pytest.mark.requirement("W28")
def test_permitted_uses_and_origin_must_be_declared_before_import(tmp_path: Path) -> None:
    copy = tmp_path / "study"
    shutil.copytree(SYNTHETIC_SCENARIO.parent, copy)
    raw = read_data_file(copy / "scenario.yaml")
    datasets = raw["datasets"]
    corridors_meta = next(item for item in datasets if item["dataset_id"] == "syn-gis-corridors")
    # Missing permitted use or access group prevents importing the GIS file.
    missing_use = {**corridors_meta}
    missing_use.pop("allowed_use")
    bad = {
        **raw,
        "datasets": [
            missing_use if item["dataset_id"] == "syn-gis-corridors" else item for item in datasets
        ],
    }
    data, problems = resolve_layer_sources(bad, copy)
    assert len(problems) == 1
    assert problems[0][0] == "layer_sources[corridors].dataset_id"
    assert "allowed_use" in problems[0][1]
    assert "corridors" not in {layer["layer_id"] for layer in data["spatial_layers"]}

    # Unregistered datasets are also refused before any import happens.
    unregistered = {
        **raw,
        "datasets": [item for item in datasets if item["dataset_id"] != "syn-gis-barriers"],
    }
    data, problems = resolve_layer_sources(unregistered, copy)
    assert problems == [
        ("layer_sources[barriers].dataset_id", "dataset 'syn-gis-barriers' is not registered")
    ]
    assert "barriers" not in {layer["layer_id"] for layer in data["spatial_layers"]}

    # Restricted datasets with declared permitted use, access group and checksum do import and
    # preserve their dataset tracking on the scenario.
    restricted = {
        **raw,
        "datasets": [
            {**item, "allowed_use": "restricted", "access_group": "wsw-geology"}
            if item["dataset_id"] == "syn-gis-targets"
            else item
            for item in datasets
        ],
    }
    data, problems = resolve_layer_sources(restricted, copy)
    assert problems == []
    targets_dataset = next(
        item for item in data["datasets"] if item["dataset_id"] == "syn-gis-targets"
    )
    assert (targets_dataset["allowed_use"], targets_dataset["access_group"]) == (
        "restricted",
        "wsw-geology",
    )
