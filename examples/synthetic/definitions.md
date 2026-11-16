# Synthetic study: definitions

Every value in `scenario.yaml` is invented for software development and testing. Nothing here
describes Wuppertal, WSW assets, a real parcel or a measured reservoir. Results computed from this
study are labelled synthetic and can never enter a pilot ranking.

This file is registered as dataset `syn-definitions`. Its checksum is recorded in the scenario, so
a change to this file requires a new dataset version.

## 1. Purpose

A small public study that exercises every part of the evaluation chain, so that development and
tests never depend on restricted data. This is the milestone M1 version, registered as dataset
version 2. Milestone M3 extends it with full screening inputs, and milestone M4 adds demand,
availability and price series.

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

Euro, price year 2026, real prices, without VAT. The discount rate of 4 percent per year and the
horizon of 20 years repeat the workshop demonstration values and serve as a labelled regression
example. They are not project assumptions. Cost amounts are round invented numbers.

## 6. Uncertainty

Two scenario sets without probabilities. The geological success event is left undefined, because
its definition is open decision O05.
