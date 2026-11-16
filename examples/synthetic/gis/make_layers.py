"""Write the GIS layers of the synthetic study.

Every geometry and value here is invented. The layers are designed in metres in EPSG:25832 on
the invented grid of the synthetic study and written as GeoJSON in WGS84, as RFC 7946 requires.
Importing them therefore exercises the reprojection to the study's reference system. Column
names and values imitate a municipal dataset, so that the mapping files have work to do.

Two geometry defects are included on purpose. Restriction R1 has a self touching ring, which the
import repairs and logs. Restriction R4 is a bow tie whose intended shape is ambiguous, which the
import rejects for review.

    .venv/bin/python examples/synthetic/gis/make_layers.py

Running it again writes identical files. A change to a layer is a new dataset version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyproj import Transformer

HERE = Path(__file__).resolve().parent
ORIGIN = (500_000.0, 5_700_000.0)
TO_WGS84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)


def point(x: float, y: float, z: float | None = None) -> list[float]:
    lon, lat = TO_WGS84.transform(ORIGIN[0] + x, ORIGIN[1] + y)
    position = [round(lon, 10), round(lat, 10)]
    return position if z is None else [*position, z]


def ring(*corners: tuple[float, float]) -> list[list[float]]:
    return [point(x, y) for x, y in corners]


def rectangle(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))
    return {"type": "Polygon", "coordinates": [ring(*corners)]}


def line(*vertices: tuple[float, float, float] | tuple[float, float]) -> dict[str, Any]:
    return {"type": "LineString", "coordinates": [point(*vertex) for vertex in vertices]}


def write(name: str, features: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    collection = {
        "type": "FeatureCollection",
        "name": name,
        "features": [
            {"type": "Feature", "properties": properties, "geometry": geometry}
            for properties, geometry in features
        ],
    }
    text = json.dumps(collection, indent=1, ensure_ascii=False) + "\n"
    (HERE / f"{name}.geojson").write_text(text, encoding="utf-8")


def main() -> None:
    write(
        "targets",
        [
            (
                {"ZIEL_ID": "target-alpha", "HORIZONT": "Synthetischer Kalkstein"},
                rectangle(300, 1300, 900, 1900),
            ),
            (
                {"ZIEL_ID": "target-beta", "HORIZONT": "Synthetischer Kalkstein"},
                rectangle(1500, 800, 2100, 1400),
            ),
            (
                {"ZIEL_ID": "target-gamma", "HORIZONT": "Synthetischer Kalkstein"},
                rectangle(4400, 2300, 5000, 2900),
            ),
        ],
    )
    write(
        "restrictions",
        [
            # Housing north of Alpha. The ring touches itself at (520, 1200), which encloses a
            # tiny second lobe. The import repairs it.
            (
                {"SCHUTZ_ID": "R1", "ART": "Wohnbebauung"},
                {
                    "type": "Polygon",
                    "coordinates": [
                        ring(
                            (380, 1100),
                            (520, 1100),
                            (520, 1200),
                            (521, 1201),
                            (521, 1200),
                            (520, 1200),
                            (380, 1200),
                            (380, 1100),
                        )
                    ],
                },
            ),
            ({"SCHUTZ_ID": "R2", "ART": "Wasserschutzgebiet"}, rectangle(5085, 2900, 5300, 3200)),
            # A power line west of Gamma. The screening policy gives no setback for it.
            ({"SCHUTZ_ID": "R3", "ART": "Freileitung"}, rectangle(4930, 2950, 4940, 3150)),
            # A bow tie, far from every site. Its intended shape is ambiguous. The import
            # rejects it.
            (
                {"SCHUTZ_ID": "R4", "ART": "Biotop"},
                {
                    "type": "Polygon",
                    "coordinates": [
                        ring((3500, 3500), (3600, 3600), (3600, 3500), (3500, 3600), (3500, 3500))
                    ],
                },
            ),
        ],
    )
    write(
        "jurisdictions",
        [
            ({"GEMEINDE": "Musterstadt (synthetisch)"}, rectangle(0, 0, 4500, 4000)),
            ({"GEMEINDE": "Nachbarort (synthetisch)"}, rectangle(4500, 0, 6000, 4000)),
        ],
    )
    street, field = "Strasse", "Feldweg"
    write(
        "corridors",
        [
            (
                {"WEG_ID": "c01", "TYP": street, "BREITE_M": 6.0},
                line((450, 1000, 164), (450, 700, 161), (450, 400, 160)),
            ),
            (
                {"WEG_ID": "c02", "TYP": street, "BREITE_M": 8.0},
                line((450, 400, 160), (1050, 400, 160)),
            ),
            (
                {"WEG_ID": "c03", "TYP": street, "BREITE_M": 8.0},
                line((1050, 400, 160), (1150, 400, 158)),
            ),
            (
                {"WEG_ID": "c04", "TYP": street, "BREITE_M": 8.0},
                line((1150, 400, 158), (1245, 400, 158)),
            ),
            (
                {"WEG_ID": "c05", "TYP": street, "BREITE_M": 5.0},
                line((1245, 400, 158), (1245, 600, 158)),
            ),
            (
                {"WEG_ID": "c06", "TYP": street, "BREITE_M": 8.0},
                line((1245, 400, 158), (2000, 400, 170)),
            ),
            (
                {"WEG_ID": "c07", "TYP": street, "BREITE_M": 7.0},
                line((2000, 400, 170), (2000, 1500, 185)),
            ),
            (
                {"WEG_ID": "c08", "TYP": street, "BREITE_M": 7.0},
                line((2000, 1500, 185), (2700, 1500, 178), (3550, 1500, 195)),
            ),
            (
                {"WEG_ID": "c09", "TYP": street, "BREITE_M": 7.0},
                line((3550, 1500, 195), (5050, 1500, 188)),
            ),
            (
                {"WEG_ID": "c10", "TYP": street, "BREITE_M": 6.0},
                line((5050, 1500, 188), (5050, 3000, 205)),
            ),
            (
                {"WEG_ID": "c11", "TYP": field, "BREITE_M": 3.0},
                line((450, 1000, 164), (1150, 900, 163)),
            ),
            (
                {"WEG_ID": "c12", "TYP": field, "BREITE_M": 3.0},
                line((1150, 900, 163), (1150, 400, 158)),
            ),
            (
                {"WEG_ID": "c13", "TYP": field, "BREITE_M": 3.0},
                line((5050, 3000, 205), (4300, 2800, 210), (3550, 2600, 198)),
            ),
            (
                {"WEG_ID": "c14", "TYP": field, "BREITE_M": 3.0},
                line((3550, 2600, 198), (3550, 1500, 195)),
            ),
        ],
    )
    write(
        "barriers",
        [
            ({"ID": "stream-1", "ART": "Gewaesser"}, line((0, 760), (920, 700))),
            ({"ID": "railway-1", "ART": "Bahn"}, line((2400, 0), (2400, 4000))),
        ],
    )
    write(
        "demand",
        [
            (
                {"KUNDE_ID": "potential-customer-1", "LEISTUNG_KW": 300, "STATUS": "geplant"},
                {"type": "Point", "coordinates": point(4000, 1530)},
            ),
            (
                {"KUNDE_ID": "committed-customer-1", "LEISTUNG_KW": 200, "STATUS": "zugesagt"},
                {"type": "Point", "coordinates": point(4300, 1520)},
            ),
            (
                {"KUNDE_ID": "potential-customer-2", "LEISTUNG_KW": 250, "STATUS": "geplant"},
                {"type": "Point", "coordinates": point(4600, 1470)},
            ),
        ],
    )


if __name__ == "__main__":
    main()
