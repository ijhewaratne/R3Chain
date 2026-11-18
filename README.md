# R3CHAIN planning system

Evaluates geothermal site, well, route, connection and integration options for a district heating
network as one coupled calculation, with every number traceable to its inputs.

- Specification: Implementation Specification v1.0, 15 November 2026
- Build plan and progress: [docs/implementation-plan.md](docs/implementation-plan.md)
- Plan in force from M5 on: [docs/implementation-plan-v2.md](docs/implementation-plan-v2.md) (version 2, approved on 17 November 2026)
- Requirements and their tests: [requirements/register.yaml](requirements/register.yaml)

## Status

Milestones **M0** (foundations and run spine), **M1** (engines standalone), **M2** (plant coupling),
**M3** (screening and candidates, with pipe-GIS-to-network/multi-zone `W06` open), **M4**
(operation and economics, with multi-zone temperature separation in `W16` open), **M5**
(uncertainty sampling, pre-drilling risk, resilience stress cases, Pareto/regret comparison,
decision drivers and evidence reports) and **M6** (Step 6.1 shared application services, access
checks, background jobs with cancellation, FastAPI HTTP API, pinned MCP server with 12 canonical
tools plus interim `geo_*` compatibility wrappers, `explain` typed claims/checks/DE+EN wording,
and Step 6.2 browser planner workspace UI in `frontend/` covering `W04`, `W22`, `W24`–`W26`,
`V05`–`V07`, `A15`–`A17`) are built. Milestones **M7** to **M9** are not built yet.

What works today:

- The 13 core records of the specification, with strict units, provenance and schema versions
  (plus `SpatialLayer` and `LayerMapping` for GIS import).
- Every engineering input is known (with its source), unknown (with an owner) or in conflict.
- Scenario validation that reports all problems, all inputs that are not established,
  imported layer repairs/rejections, and network topology faults.
- Immutable datasets, policies, scenario revisions, runs and results (including per-candidate
  results) in a local store.
- A staged pipeline with dependency hashing, validly reused results and exact replay.
- **GIS import and screening.** GeoPackage, GeoParquet and GeoJSON import through declarative
  mapping files after checking dataset origin, permitted uses and checksums; reprojection,
  logged validatable repairs, rejection of ambiguous shapes, setback-aware usable pad area,
  access and target checks, and explicit non-permit screening records.
- **Route generation and map export.** Checked corridor line graph, distinct route
  alternatives keeping trench length separate from supply/return pipe lengths, crossings,
  elevation profiles, linked customers, indicative route costs, and WGS84 GeoJSON map exports.
- **Candidate search and coupled evaluation.** Content-addressed candidate enumeration across
  sites, targets, connections, routes and plant arrangements, coupling transmission pipe heat
  and pressure losses for each candidate, rejection tables and provisional eligibility/ranking.
- **A source port** with a conformance suite, a deterministic test engine behind it, and
  performance envelopes with a recorded approximation error.
- **A network port** with a conformance suite and a pandapipes 0.17.0 adapter for hot water
  networks: hydraulic and thermal steady state, elevation, pressure limits, energy balance,
  and pressure references held either at or away from the supply point.
- **Plant coupling.** A counterflow exchanger, a heat pump and three plant arrangements: direct
  exchanger, heat pump assisted, and pressure separation exchanger.
- **A coupled operating point** of one source, one plant, optional transmission route and one
  network case, found by iteration with recorded residuals. A source that is too cold or too
  small leaves a reported deficit, or a declared backup source covers it. Non convergence is a
  separate outcome.
- **An energy ledger** from reservoir extraction to consumer delivery. The heat of the ideal
  boundary of the network model is always assigned to the plant, a backup or a deficit.
- **Chronological operation and dispatch.** Time-series ingestion from JSON, YAML or Parquet after
  checking dataset origin, permitted uses and checksums (`StoredSeries`), interval reduction with
  measured and bounded approximation error, priority-ordered rule-based dispatch across competing
  base-load waste heat, geothermal and peak/backup sources with maintenance outages and minimum
  output limits, separating delivered heat from curtailed or unserved heat (never crediting
  curtailed heat).
- **Cost reconciliation and lifecycle economics.** Policy-governed CAPEX/OPEX/revenue ledger
  reconciling source, route, connection, plant and system items once per boundary (`plant`,
  `geothermal_delivered`, `system_delivered`), discounting over differing asset lives with
  replacements and residual values, separate `funded` vs `unsubsidised` evaluations, baseline
  incremental NPV/LCOH comparison, and explicit route comparison with vs without linked
  additional demand.
- **Uncertainty, resilience and decision analysis.** Correlated joint sampling / scenario sweeps
  preserving common random numbers across candidates (`UncertaintyModel` 0.2.0), separation of
  unresolved numerical runs from physical failures (`A14`), conditional vs pre-drilling expected
  economics, ascending quantiles (`p05/p50/p95`), minimax regret, Pareto frontier and explicit
  no-defensible-preference checks, decision drivers and first-rank stability (`V01`), deterministic
  outage resilience cases (`V02`), connection adoption thresholds (`W18`), point-estimate (`V03`)
  and progressive sample-error-checked (`V04`) policies, and reproducible Markdown/JSON evidence
  reports (`r3chain compare`, `r3chain report`).
- **Typed explanations and verification (`V05`–`V07`).** Five claim types (`value`, `status`,
  `cause_of_failure`, `comparison`, `open_assumption`), deterministic claim checking against stored
  records, and fixed German/English wording rendered cleanly without an LLM (`r3chain explain`).
- **Application services, HTTP API & MCP server (`M6 Step 6.1`).** Shared `PlanningService` used by
  CLI, FastAPI endpoints (`r3chain serve-api`), and the official MCP SDK server (`r3chain serve-mcp`
  with all 12 tools of §16.2 plus six `geo_*` compatibility wrappers), with project/dataset access
  and stage-cache isolation (`A16`), job progress and cooperative cancellation, and separated §16.3
  failure classes.
- **Planner workspace UI (`M6 Step 6.2`, `frontend/`).** Accessible React + TypeScript + MapLibre GL
  + Recharts browser workspace served directly by `r3chain serve-api` (once built in `frontend/dist`),
  with German/English labels, presentation preferences retaining mandatory assumptions (`W24`–`W26`),
  WGS84 vector screening/candidate maps with text equivalents, JSON-Schema-generated scenario editing
  and revision branching (`W22`, `A15`), live job monitoring/cancellation, eligibility-first
  comparison, claim-verified explanation links, report export, and assistant-free replay (`A17`).
- A command line for all of the above (`validate`, `assumptions`, `run`, `variant`, `show`,
  `replay`, `candidates`, `layers`, `economics`, `compare`, `report`, `explain`, `network`,
  `couple`, `policies`, `schemas`, `serve-api`, `serve-mcp`).

What does not exist yet:

- **No PyDoublet.** The only source engine is `r3chain-synthetic-source`, an invented test model.
  It is not PyDoublet and not validated physics. Every result that depends on it is labelled
  synthetic and can never enter a pilot ranking. The real adapter needs the PyDoublet package,
  its API and reference outputs (open decision O03).
- No assistant orchestrator (`M7`), Wuppertal pilot reconstruction (`M8`), or user evaluation/research extensions (`M9`).
- In GIS/network import (`W06`, `W16`): no automatic conversion of `network_pipe` GIS layers into
  canonical hydraulic `Network` records, and in the network adapter: no steam, no pumps or
  valves inside the network (separate pressure/temperature zones), one supply point only, no
  transient heat transport. Each unsupported network feature returns `unsupported_model`.
- In coupling (`operating_point`): no electricity of the network pump (its hydraulic power is
  reported) and no detailed off-design part-load efficiency curves (chronological dispatch
  interpolates well-pump and compressor electricity piecewise-linearly from `(0, 0)` across coupled
  operating points). When evaluating standalone cases without a route, route heat and pressure
  losses remain listed as not modelled; `source-outage` is defined in the synthetic study for M5
  and not coupled in M4.
- The heat pump map, annual time series, economic tariffs and GIS layers of the synthetic study
  are invented. A real study uses reviewed datasets. The Carnot based screening model is labelled
  in every result that uses it.

The pipeline runs all ten active stages (`site_evidence`, `source`, `screen`, `routes`,
`candidates`, `couple`, `assess`, `operate`, `cost`, `compare`).

The coupling makes modelling decisions that the specification leaves open, for example that the
network is solved at its required supply temperature. They are listed for review in section 11
of the [build plan](docs/implementation-plan.md).

The only study in the repository is synthetic. Every value in `examples/synthetic/` is invented.

## Set up

Requires Python 3.12.

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements.lock
```

```bash
.venv/bin/python -m pip install --no-deps -e .
```

## Try it

Check the synthetic scenario and list the inputs that are not established:

```bash
.venv/bin/r3chain validate examples/synthetic/scenario.yaml
```

List every engineering input with its value, unit and provenance:

```bash
.venv/bin/r3chain assumptions examples/synthetic/scenario.yaml
```

Freeze the scenario as a revision and evaluate it. The output shows a run identifier and a
revision identifier:

```bash
.venv/bin/r3chain run examples/synthetic/scenario.yaml
```

Show the stored run with the performance envelope summary of each source. Replace `RUN` with
the identifier printed by the previous command:

```bash
.venv/bin/r3chain show RUN
```

Show the candidate and rejection tables of the stored run, and write WGS84 GeoJSON map layers
for inspection:

```bash
.venv/bin/r3chain candidates RUN
```

```bash
.venv/bin/r3chain layers RUN /tmp/r3chain-layers
```

Show annual dispatch, cost reconciliation, boundary LCOHs, funded vs unsubsidised cases,
baseline comparisons and route comparisons with vs without extra demand:

```bash
.venv/bin/r3chain economics RUN
```

Solve one operating case of the synthetic network with pandapipes:

```bash
.venv/bin/r3chain network examples/synthetic/scenario.yaml examples/synthetic/cases/design-thermal.yaml
```

Evaluate the coupling cases of the synthetic study. Two of the six fail on purpose, so the
command ends with a failure status:

```bash
.venv/bin/r3chain couple examples/synthetic/scenario.yaml
```

Show one coupled operating point in full, with its checks, iterations and energy ledger:

```bash
.venv/bin/r3chain couple examples/synthetic/scenario.yaml --case beta-heat-pump-shoulder
```

Create a changed revision and evaluate it. Only the stages that depend on the changed input are
recomputed. Replace `REVISION` with an identifier printed by `run`:

```bash
.venv/bin/r3chain variant REVISION --set-known 'well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]=388.15' --author you --rationale "Warmer reservoir case" --run
```

Execute a stored run again from its frozen inputs and compare the result:

```bash
.venv/bin/r3chain replay RUN
```

Runs are stored in `.r3chain/` in the current directory. Set `R3CHAIN_HOME` or pass `--store` to
use another location.

## Develop

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

```bash
.venv/bin/mypy
```

```bash
.venv/bin/r3chain schemas check
```

Rules that the checks enforce:

- A record cannot change without a new schema version. Raise `schema_version`, add a migration,
  then run `r3chain schemas export`.
- A numeric field carries its unit in its name, for example `return_temperature_k`.
- A requirement marked as at least partly built in the register must name tests that exist.
- Every adapter of a port must pass that port's conformance suite in `tests/conformance`.

### Adding an engine

A new source or network engine is one adapter module that implements the port, plus one line in
the conformance suite. Nothing else in the system changes.

1. Implement `SourceModel` from `r3chain.ports.source`, or `NetworkModel` from
   `r3chain.ports.network`.
2. Add the adapter to `SOURCE_ADAPTERS` or `NETWORK_ADAPTERS` in `tests/conformance`.
3. Add tests that compare the adapter with a direct run of the engine at the same inputs.
4. For a source engine, add it to `default_source_models` in `r3chain.pipeline.stages`.

pandapipes is pinned to the release the adapter was written against. Change the pin only
together with the adapter and its tests.

### Adding a plant arrangement

A new integration concept, for example a storage tank or a peak boiler, is one entry in the
registry of `r3chain.coupling.plant`.

1. Write an evaluation that takes a geothermal inlet and a network demand and returns a
   `PlantPoint` with its binding limit and its checks.
2. Register it with `register_arrangement`, naming its required and optional parameters.
3. Add a plant with that arrangement to a scenario, and a test with hand derived values.

## Layout

| Path | Contents |
|---|---|
| `src/r3chain/domain` | Records, units, evidence, statuses, time series, validation, topology, policies, field paths |
| `src/r3chain/geospatial` | GIS import mappings, site screening, checked corridor graph, route generation, GeoJSON export |
| `src/r3chain/search` | Candidate identity, transmission sizing, eligibility separation and provisional ranking |
| `src/r3chain/ports` | The source and network interfaces with their request and result contracts |
| `src/r3chain/adapters` | The synthetic source test engine and the pandapipes network adapter |
| `src/r3chain/coupling` | Fluids, exchanger, heat pump, plant registry, coupling loop, energy ledger, source envelopes |
| `src/r3chain/operation` | Time-series ingestion, dispatch engine, interval reduction error checking |
| `src/r3chain/store` | Immutable local store |
| `src/r3chain/pipeline` | Stage interface, runner, stage evaluators, default chain |
| `src/r3chain/economics`, `reference` | Cost ledger reconciliation, lifecycle cashflows, KPIs, discounting, reference formulas |
| `src/r3chain/uncertainty`, `reporting`, `explain` | Joint sampling, regret/Pareto risk, resilience cases, evidence reports, verified DE/EN claims |
| `src/r3chain/services`, `api`, `mcp` | Application service layer, access/job manager, FastAPI HTTP endpoints, typed MCP server |
| `policies` | Versioned thresholds and rules. Never in code. |
| `schemas` | Generated JSON Schemas, one per record and version |
| `examples/synthetic` | The public synthetic study, with six GIS layers, coupling cases, and a network operating case |
| `requirements` | Requirement register |
| `tests/analytic` | Fixtures with hand derived expected values |
| `tests/conformance` | Suites that every adapter of a port must pass |
| `tests/geospatial` | GIS import, screening, corridor graph and route generation |
| `tests/unit` | Records, validation, store, pipeline, adapters, plant, coupling, candidates, operation, economics, command line |

Restricted pilot data never belongs in this repository.
