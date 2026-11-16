# R3CHAIN planning system

Evaluates geothermal site, well, route, connection and integration options for a district heating
network as one coupled calculation, with every number traceable to its inputs.

- Specification: Implementation Specification v1.0, 15 November 2026
- Build plan and progress: [docs/implementation-plan.md](docs/implementation-plan.md)
- Requirements and their tests: [requirements/register.yaml](requirements/register.yaml)

## Status

Milestones **M0** (foundations and run spine) and **M1** (engines standalone) are built.
Milestones M2 to M9 are not.

What works today:

- The 13 core records of the specification, with strict units, provenance and schema versions.
- Every engineering input is known (with its source), unknown (with an owner) or in conflict.
- Scenario validation that reports all problems, all inputs that are not established, and
  network topology faults.
- Immutable datasets, policies, scenario revisions, runs and results in a local store.
- A staged pipeline with dependency hashing, validly reused results and exact replay.
- **A source port** with a conformance suite, a deterministic test engine behind it, and
  performance envelopes with a recorded approximation error.
- **A network port** with a conformance suite and a pandapipes 0.17.0 adapter for hot water
  networks: hydraulic and thermal steady state, elevation, pressure limits, energy balance.
- A command line for all of the above.

What does not exist yet:

- **No PyDoublet.** The only source engine is `r3chain-synthetic-source`, an invented test model.
  It is not PyDoublet and not validated physics. Every result that depends on it is labelled
  synthetic and can never enter a pilot ranking. The real adapter needs the PyDoublet package,
  its API and reference outputs (open decision O03).
- No plant coupling, no finite source in the network, no dispatch, no economics beyond
  discounting, no uncertainty, no API, no MCP tools, no assistant, no user interface.
- In the network adapter: no steam, no pumps or valves inside the network, one supply point
  only, no transient heat transport. Each of these returns `unsupported_model`.
- A supply point is an ideal source. The heat it feeds in is reported, so that the coupling step
  of milestone M2 can require a finite source to cover it.

The pipeline runs two real stages (site evidence, source envelopes) and five placeholders that
always end as `not_established` and never produce a number.

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

Solve one operating case of the synthetic network with pandapipes:

```bash
.venv/bin/r3chain network examples/synthetic/scenario.yaml examples/synthetic/cases/design-thermal.yaml
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

## Layout

| Path | Contents |
|---|---|
| `src/r3chain/domain` | Records, units, evidence, statuses, time series, validation, topology, field paths |
| `src/r3chain/ports` | The source and network interfaces with their request and result contracts |
| `src/r3chain/adapters` | The synthetic source test engine and the pandapipes network adapter |
| `src/r3chain/coupling` | Stream heat balance and source performance envelopes |
| `src/r3chain/store` | Immutable local store |
| `src/r3chain/pipeline` | Stage interface, runner, default chain |
| `src/r3chain/economics`, `reference` | Discounting, independent hydraulic formulas |
| `policies` | Versioned thresholds and rules. Never in code. |
| `schemas` | Generated JSON Schemas, one per record and version |
| `examples/synthetic` | The public synthetic study and a network operating case |
| `requirements` | Requirement register |
| `tests/analytic` | Fixtures with hand derived expected values |
| `tests/conformance` | Suites that every adapter of a port must pass |
| `tests/unit` | Records, validation, store, pipeline, adapters, command line |

Restricted pilot data never belongs in this repository.
