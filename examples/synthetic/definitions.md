# Synthetic study: definitions

Every value in `scenario.yaml` is invented for software development and testing. Nothing here
describes Wuppertal, WSW assets, a real parcel or a measured reservoir. Results computed from this
study are labelled synthetic and can never enter a pilot ranking.

This file is registered as dataset `syn-definitions`. Its checksum is recorded in the scenario, so
a change to this file requires a new dataset version.

## 1. Purpose

A small public study that exercises every part of the evaluation chain, so that development and
tests never depend on restricted data. This is the milestone M4 version, registered as dataset
version 5. Milestone M5 adds uncertainty models and risk definitions.

## 2. Sites

Four candidate sites on an invented grid in EPSG:25832. Heights use the label DHHN2016.

| Site | Usable pad | Land | Role in the study |
|---|---|---|---|
| Alpha | 6000 m2 | available | A well documented site near the western connection |
| Beta | 5200 m2 | available | A site whose reservoir temperature is too low for direct heat |
| Gamma | 5600 m2 | available | A site reached by a longer route that passes more customers |
| Delta | not surveyed | restricted | A site with a land restriction, an unknown pad area and two disagreeing statements about its access road |

Delta carries an unknown value and a conflict on purpose. They show how missing and disputed
inputs stay visible.

## 3. Subsurface

One doublet concept per site for Alpha, Beta and Gamma, using the model family label
`synthetic-test-model`. Depths, temperatures, thickness, net to gross and permeability are round
invented numbers. Well trajectories are left unknown.

Each concept also carries three operating limits for the synthetic test model: a largest mass
flow of 25 kg/s, a lowest injection temperature of 303.15 K and a largest drawdown of 6 MPa. With
these limits the three sources reach their drawdown limit at different flows, so an operating
grid shows both feasible and infeasible points.

| Site | Vertical depth | Reservoir temperature |
|---|---|---|
| Alpha | 3000 m | 378.15 K |
| Beta | 2200 m | 348.15 K |
| Gamma | 3200 m | 383.15 K |

## 4. Network

One hot water network with a supply and a return circuit, five node pairs, four consumers and an
elevation range of 150 m to 210 m. Two connection pairs exist, in the west and in the east, so
that nothing can assume a single fixed connection. The maximum pressure at the eastern connection
is left unknown.

The pressure reference is 750 000 Pa absolute at the western return node. That keeps the highest
node, 50 m above it, well above ambient pressure. A lower reference makes the network infeasible
at its high points, which the tests use on purpose.

## 5. Economics

Euro, price year 2026, real prices, without VAT, carbon not monetised. The discount rate of 4
percent per year and the horizon of 20 years repeat the workshop demonstration values and serve as
a labelled regression example. They are not project assumptions. Cost amounts, asset lives and the illustrative unreviewed initial-CAPEX exploration grant
rule (`0.2` of source CAPEX, `applies_to_replacements: false`) are round invented numbers.

## 6. Uncertainty

Two scenario sets without probabilities. The geological success event is left undefined, because
its definition is open decision O05.

## 7. Plants

Three integration concepts, one for each registered arrangement. All equipment values are round
invented numbers. None describes a selected product.

| Plant | Arrangement | Purpose in the study |
|---|---|---|
| plant-direct | direct_exchanger | Direct heat through one exchanger |
| plant-heat-pump | heat_pump_assisted | Heat for a source that is colder than the required supply |
| plant-separated | pressure_separation_exchanger | A second exchanger that separates the network pressure zone |

The exchangers have a conductance of 450 kW/K, a fouling allowance of 10 percent and a minimum
approach of 5 K. The separation exchanger has 600 kW/K and 3 K. The pressure loss on the network
side of the direct exchanger is left unknown on purpose, so that its check stays visible as not
evaluated.

The heat pump map is an invented table of the coefficient of performance and the capacity over
source inlet and sink outlet temperature. It is not a product and not manufacturer data. The
geothermal stream is not cooled below 303.15 K, the same value as the lowest injection
temperature of the synthetic sources. Five percent of the compressor electricity is taken as
lost to the surroundings.

## 8. Operating cases and coupling cases

Three steady network states (`winter-design`, `shoulder-season`, `summer-minimum`), plus an
uncoupled `source-outage` operating case reserved for M5 resilience checks (not evaluated in M4).
Every consumer returns water at 323.15 K.

| Operating case | Load | Ground temperature |
|---|---|---|
| winter-design | 100 percent of the design heat, 3.0 MW | 278.15 K |
| shoulder-season | 40 percent, 1.2 MW | 283.15 K |
| summer-minimum | 20 percent, 0.6 MW | 288.15 K |
| source-outage | Geothermal source offline during shoulder demand, 1.2 MW covered by existing units | 283.15 K |

Six coupling cases at the western connection, with a circulation pressure lift of 150 000 Pa.
They are chosen so that every outcome of the coupling occurs once.

| Coupling case | What it shows |
|---|---|
| alpha-direct-shoulder | Direct heat covers the demand |
| alpha-direct-winter | The source reaches its drawdown limit and leaves a deficit |
| alpha-direct-winter-backup | The same shortfall, covered by a declared backup boiler of 1.5 MW |
| beta-direct-summer | The source is colder than the required supply, so direct heat fails at the hot end |
| beta-heat-pump-shoulder | The same source covers the demand with a heat pump |
| alpha-separated-shoulder | Direct heat through a pressure separation exchanger |

## 9. GIS layers

Six invented layers in `gis/`, written as GeoJSON in WGS84 by `gis/make_layers.py` from designs
in metres in EPSG:25832. Each is registered as its own dataset and imported through a mapping
file that translates the raw columns, which imitate a municipal dataset. Heights are in DHHN2016.

| Layer | Contents | Role in the study |
|---|---|---|
| targets | One target area per well concept | Every concept reaches its target |
| restrictions | Housing, a water protection zone, a power line, a biotope | Alpha loses 2000 m2 to the housing setback. Gamma loses 1050 m2 to the water protection zone, and the power line has no documented setback, so Gamma is conditional |
| jurisdictions | Two municipalities | Gamma lies in the neighbouring one, which is recorded and does not exclude it |
| corridors | Streets and field paths | The graph that routes follow |
| barriers | A stream and a railway | Crossings with a cost |
| demand | Three buildings along the eastern street | Customers that a longer route from Gamma passes |

Two defects are included on purpose. Restriction R1 has a self touching ring, which the import
repairs and reports. Restriction R4 is a bow tie whose intended shape is ambiguous, which the
import rejects and reports. R4 lies far from every site.

The routes are designed so that, from Alpha to the western connection, the shortest route by
length runs along streets and crosses the stream, while a route 107 m longer follows field
paths and is cheaper.

## 10. Candidates

The candidate policy couples every candidate in the shoulder-season and winter-design operating
cases. Route pipes are sized for
the largest mass flow of their connection at 1.5 m/s. The ranking policy ranks by indicative
route cost only, while M4 evaluates annual operation and discounted economics separately across
plant and delivered boundaries. Delta is excluded by the screening, so it appears in the
rejection table and not among the candidates.

## 11. Chronological operation and economics (M4)

An annual hourly UTC dataset for 2026 (`series/annual_2026.json`, generated by
`series/make_series.py`) provides seasonal heat demand between `0.6 MW` and `3.0 MW`, planned
maintenance outages for waste incineration (14 days in July) and geothermal (7 days in May), and
winter/summer electricity and fuel price differentials. Existing waste heat base load (`600 kW`)
dispatches ahead of geothermal and curtails geothermal output in summer; an existing peak/backup
boiler (`4.0 MW`, 90 percent thermal efficiency) covers peaks and outages. Candidate policy
`candidate-synthetic` v2 couples `shoulder-season` as its operating case and `winter-design` as
its capacity case, and dispatch interpolates well-pump and compressor electricity piecewise-linearly
between coupled points rather than using full nonlinear part-load curves.
