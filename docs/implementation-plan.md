# R3CHAIN Geothermal Planning System: Implementation Plan

Draft 1, 15 November 2026. Based on the Implementation Specification v1.0 (15 November 2026).
Status: approved as the basis for building M0 to M4 on 15 November 2026; superseded from M5 on by plan v2. Section 11 records progress (M0 to M4 built) and every departure from this plan.
From M5 on, version 2 of this plan is in force: [implementation-plan-v2.md](implementation-plan-v2.md), approved on 17 November 2026. This document remains the record of what M0 to M4 delivered.

References such as §7.2 point to sections of the specification. W, P, O and A identifiers are the specification's own (workshop points, proposals, open decisions, acceptance tests). M0 to M9 are the milestones of this plan, and X1 to X15 are its extension points.

---

## 1. The end product

The product is a planning system that evaluates geothermal site, well, route, connection and integration options for a district heating network as one coupled calculation. It reports which options deliver useful heat, what they cost, why the others fail, and how the answer moves when geology, prices or customer connections are uncertain.

It has five parts.

| # | Part | What it is | Used by |
|---|---|---|---|
| 1 | Engineering core | Python library: contracts, screening, source and network adapters, plant coupling, dispatch, economics, uncertainty, ranking | Every other part. Runs with no LLM and no UI. |
| 2 | Run and evidence layer | Frozen scenario revisions, immutable run records, assumption and source registers, replay | Reviewers and the researcher |
| 3 | Interfaces | Command line, HTTP API, and one MCP server with typed tools | Developer, UI, assistant |
| 4 | Planner workspace | Browser UI: map, scenario editor, comparison, evidence view, chat | WSW planners and analysts |
| 5 | Assistant and its benchmark | A bounded orchestrator that plans, calls tools, checks and explains, plus a frozen request suite that measures it | Planners, and the doctoral research |

Two studies run on it:

- **Synthetic study**: public, lives in the repository, four consumers, several sites, at least two connection choices.
- **Wuppertal pilot**: restricted data, stored outside the repository, one documented baseline and two or three agreed alternatives.

**Done means** (§23.3): a planner can run the agreed study, inspect and change an assumption, reproduce the reference result and understand an excluded option, and a technical reviewer can trace any number back to source evidence and model runs.

**Not in the first demonstrator** (§3.3): three dimensional reservoir simulation, a steam or two phase network model, real time plant control, automatic investment decisions, and any determination of permit or land approval.

### How complete is the picture

The structure of the product is fully determined by the specification: the components, the records, the calculation sequence, the tools, the tests and the build order are all stated.

What is not determined are the 14 open decisions (O01 to O14). Those are values, datasets and reviews. None of them changes the structure. This plan therefore fixes the structure now and treats every open input as data that arrives later through a defined entry point (section 4).

Three things cannot be known from the specification alone, and the plan isolates each behind an interface:

1. The real PyDoublet API, model families and reference outputs (O03, O04).
2. The format, coverage and quality of the WSW network, demand and operating data (O06 to O08).
3. The source code and behaviour of the existing interim MCP server (section 2).

---

## 2. Starting point

| Item | State on 15 November 2026 | Consequence for the plan |
|---|---|---|
| Project folder | Empty, not yet a git repository | It becomes the repository in M0 |
| Tooling on this machine | Python 3.12, Node 24 and git are installed. The default `python3` is 3.9. `uv` and `pandoc` are not installed. | Use `python3.12` explicitly, with a standard virtual environment and a lock file |
| Interim MCP server | `r3chain-geothermal-mcp` v0.1.0 is connected. It has six `geo_*` tools, deterministic and Monte Carlo modes, and a fixed set of run artifacts. Only its capability listing has been inspected, not its code or outputs. | Kept as a behavioural reference (see below) |
| Integration architecture | Already decided: one R3CHAIN MCP server, no separate PyDoublet MCP server | PyDoublet is an adapter inside the core, never its own server |
| PyDoublet | Not yet in hand | A deterministic test adapter stands in, labelled synthetic |
| Pilot data | Not yet in hand, NDA dependent | All development proceeds on the synthetic study |

**Treatment of the interim server.** The new system does not extend the interim server's code blindly, and it does not discard it either:

- Its workflow contract and run artifacts become regression references for the new core where their inputs are recoverable.
- Its six tool names stay available as a thin compatibility layer on the new server in M6, so existing demonstrations keep working.
- It is retired once the new tool catalogue reproduces its reference runs.

This needs the location of the interim server's source, which I do not have yet.

---

## 3. Architecture

### 3.1 Layers

```
Interfaces     CLI          HTTP API        MCP server        Web UI
               |              |                |                |
Application    study, revision, evaluation, comparison, export, jobs, access
               |
Pipeline       screen > source > couple > operate > cost > compare
               (the uncertainty sampler wraps the whole chain per realisation)
               |
Ports          SourceModel   NetworkModel   DispatchStrategy   LLMProvider   Stores
               |
Adapters       PyDoublet     pandapipes     importers          model API     SQLite + files
```

Four rules hold everywhere:

1. **Dependencies point inwards.** The domain and pipeline never import an engine, a web framework or a model provider. They see only ports.
2. **One service layer.** The command line, API, MCP server and UI all call the same application services, so the same request produces the same run inputs through every route.
3. **Nothing computes outside a frozen revision.** A calculation receives an immutable scenario revision and data snapshot. No solver mutates its inputs.
4. **Every displayed number comes from a result record.** Prose can explain a number. It cannot create one.

### 3.2 Repository layout

This extends the layout proposed in §20.2 with the directories that make change cheap (`ports`, `pipeline`, `store`, `policies`, `schemas`, `requirements`).

```
R3Chain/
  pyproject.toml            package, dependencies, tool settings
  docs/                     this plan, contracts, decision log
  requirements/             register linking each requirement to modules and tests
  schemas/                  generated JSON Schemas, one per record and version
  policies/                 screening, numerical, dispatch, ranking and metric policies
  examples/synthetic/       the public synthetic study
  src/r3chain/
    domain/                 records, units, evidence, statuses, time series
    ports/                  interfaces for engines, strategies, providers and stores
    store/                  datasets, scenario revisions, runs, results, policies
    pipeline/               stages, dependency hashing, cache, runner
    ingestion/              dataset registration, importers, validation reports
    geospatial/             site screening, route generation
    adapters/               pydoublet, source_test, pandapipes
    coupling/               exchanger, heat pump, plant, coupling loop, energy ledger
    operation/              operating cases, dispatch, annual run
    economics/              cost ledger, cash flow, baseline comparison, KPIs
    uncertainty/            uncertainty model, sampling, risk outputs
    search/                 candidate enumeration, eligibility, ranking
    services/               application services, jobs, access checks
    api/  mcp/              HTTP endpoints and typed tools
    assistant/              orchestration, context, retrieval, rendering
    reporting/              evidence report and export
  tests/
    analytic/               hand derived expected values
    conformance/            suites every adapter implementation must pass
    integration/            coupled fixtures
    acceptance/             A01 to A18
    agent/                  frozen assistant requests
  benchmarks/               runners and reference outcomes
  frontend/                 browser UI
```

Restricted pilot data is never placed in this tree. It lives in configured storage outside the repository.

### 3.3 Technology choices

These follow the proposals in §6.2 and §20.2. Each is a default that can be changed.

| Concern | Choice | Replaceable through |
|---|---|---|
| Language | Python 3.12, subject to PyDoublet compatibility | |
| Validation | Pydantic models with exported JSON Schema | |
| Numerics and tables | NumPy, SciPy, pandas, Parquet | |
| Geospatial | GeoPandas, Shapely, pyproj, NetworkX | |
| Network engine | pandapipes at a pinned, tested release | X5 |
| Source engine | Jan's PyDoublet at a recorded commit. A test adapter until then. | X4 |
| Metadata and files | SQLite plus immutable files, later PostgreSQL with PostGIS | X14 |
| HTTP API | FastAPI | |
| MCP | Official MCP Python SDK at a pinned version | |
| Assistant model | Claude during development, behind a provider interface | X13 |
| Frontend | React with TypeScript, MapLibre for maps, one maintained chart library | |
| Quality | pytest, property tests, linting, type checking, CI on every change | |

---

## 4. Designing for later data and later requirements

The specification expects late inputs: 14 open decisions, a real engine that replaces a stand in, restricted data that replaces synthetic data, and requirements that continue to arrive from workshops (W36). The design goal is that a late arrival is an addition at one known place, not an edit across the system.

### 4.1 Eight mechanisms

**1. Ports and adapters.** Each engine sits behind an interface and declares what it supports. Swapping the test adapter for real PyDoublet, or adding a second network engine, touches one adapter. A capability an engine lacks returns `unsupported_model`, never a zero.

```python
class SourceModel(Protocol):
    def capabilities(self) -> SourceCapabilities: ...
    def evaluate(self, request: SourceRequest) -> SourceResult: ...
```

**2. Inputs are versioned data, never code.** Datasets, parameter packages, scenarios and policies are immutable, content hashed records. New information creates a new version and a new scenario revision. Old runs keep pointing at what they actually used.

**3. Policies are records.** Every threshold and rule is a named, versioned policy file: screening criteria, numerical tolerances, dispatch rule, ranking and risk policy, metric boundary. A new limit from WSW is a new policy version. The tool catalogue in §16.2 already passes policy identifiers for this reason.

**4. Unknown is a value.** Every input is one of three things: known with provenance, unknown with a named owner, or in conflict. A calculation that needs an unknown returns `not_established`. It never substitutes a default. When the value arrives later it replaces the unknown in a new revision and the affected results recompute.

**5. A staged pipeline with dependency hashing.** The evaluation is a chain of stages. Each stage declares its inputs. Its cache key is the hash of those inputs plus the engine and policy versions. Adding a stage, such as an electricity connection check, means adding a node. Changing an input invalidates exactly the stages downstream of it, which is also what makes stale results impossible (A15).

**6. Registries for things that grow.** Plant components, KPIs, cost item types, failure states and tools are registered entries with their own schema and tests. Adding thermal storage or a CO2 metric is a new entry.

**7. Schemas that evolve by rule.** Section 4.3.

**8. A requirements register.** Section 4.4.

### 4.2 Where each kind of late arrival lands

| ID | What arrives later | Where it lands | Work needed | Core code changes |
|---|---|---|---|---|
| X1 | A new or updated dataset (GIS layer, demand series, measurements) | Dataset registry and an importer | Register, validate, map, snapshot, new revision, rerun | None if the format is known. One importer if it is new. |
| X2 | A value that was unknown (pressure limit, pad area, tariff) | Assumption register | Replace unknown with a sourced value in a new revision | None |
| X3 | A threshold, rule or criterion | Policy record | New policy version | None |
| X4 | Real PyDoublet, a new version, a new model family, more wells, decline | Source port and capability record | Adapter mapping, conformance suite, reference comparison | Adapter only |
| X5 | A pandapipes upgrade, transient mode, a steam boundary, zone separation | Network port | Adapter change or a second adapter, conformance suite | Adapter only |
| X6 | A new integration concept (storage, peak boiler, second heat pump stage) | Plant component registry | Component schema, evaluator, fixture | New component only |
| X7 | A new check or calculation step (electricity grid, emissions, transients) | Pipeline stage registry | Stage, inputs, outputs, fixture | New stage only |
| X8 | A different dispatch method (WSW schedule import, optimisation) | Dispatch strategy port | Strategy implementation, comparison against the rule based one | New strategy only |
| X9 | New cost items, funding rules, accounting conventions | Cost ledger types and economic scenario | Ledger entry type or scenario field | Small, local |
| X10 | A new metric | KPI registry | Definition with boundary, units and provenance | New entry only |
| X11 | New uncertain variables, distributions, correlations, success definitions | Uncertainty model record | New record version, reviewed by specialists | None |
| X12 | A new user question or workflow | Question register, then a service, tool and view | Service method, thin tool, UI view, acceptance test | Additive |
| X13 | A different model provider, including an organisation hosted one | Provider port | Provider adapter, rerun the assistant benchmark | Adapter only |
| X14 | More users, larger spatial workloads | Store ports | PostgreSQL with PostGIS, a job queue | Store adapters only |
| X15 | A new requirement from Tanja, Jan, WSW or the UX team | Requirements register | Classify, route to X1 to X14, add a test | Depends on class |

### 4.3 Schema evolution rules

1. Every record carries `schema_version`. Schemas are generated from the models and committed. CI fails if a model changes without a version change.
2. Adding an optional field is a minor version. Renaming, removing or changing the meaning of a field is a major version and requires a migration function plus a migrated fixture.
3. Stored runs and results are never rewritten. They remain readable under the schema version they were written with.
4. Internal values are SI. Field names carry the unit (`_k`, `_pa`, `_w`, `_kg_s`). Pressure states absolute or gauge. Depth states vertical or measured.
5. Serialising and reloading any record must preserve every value and all provenance.

### 4.4 Change intake

Every incoming item gets an entry in `requirements/register.yaml` and is classified before any code is written.

| Class | Example | Route | Typical cost |
|---|---|---|---|
| A. New data, same shape | Updated demand series | X1, new revision, rerun, difference report | Hours, no code |
| B. New value or rule | Velocity limit from WSW | X2 or X3, new revision | Hours, no code |
| C. New capability in an existing port | Multiple injection wells | X4 to X11, with conformance tests and a synthetic fixture | Days |
| D. New step, workflow or tool | Electricity grid check | X7 or X12, with an acceptance test | Days to weeks |
| E. Contradiction or boundary change | A different cost boundary than agreed | Decision log entry and a named reviewer first | Decided before code |

A register entry looks like this:

```yaml
- id: W24
  source: "workshop transcript p39, 1:43:55"
  statement: "Show assumptions automatically and allow them to be changed"
  status: planned          # planned, built, verified, superseded
  owner: WSW
  lands_in: [domain.evidence, services.revision, frontend.assumptions]
  tests: [A15, test_assumptions_visible_by_default]
```

CI checks that every requirement marked built has at least one passing test.

Two working rules keep this honest:

- **Synthetic first.** Every new feature and every later requirement lands in the synthetic study with a fixture before it touches pilot data.
- **One seam per change.** If a late change needs edits in more than one layer beyond its own entry point, the entry point is wrong and gets fixed first.

### 4.5 What stays expensive regardless

Good structure does not remove scientific work. These remain real effort whenever they arrive: validating new physics (steam, transients, reservoir decline), reconstructing and validating the pilot baseline against measurements, agreeing probability definitions with the geological specialists, and running user studies.

---

## 5. Roadmap

### 5.1 Two tracks

- **Track A, build.** M0 to M7 run entirely on the synthetic study and the test adapter. This track never waits for an external input.
- **Track B, inputs.** Work package 0 of the specification: the pilot charter, data access, PyDoublet handover, user questions. It starts on day one and runs in parallel. M8 begins when its inputs land.

The first thing built is an end to end run with placeholder stages that return `not_established`. Each milestone then replaces placeholders with real calculations. There is a complete, replayable run at every point, and adding a stage is the routine operation.

### 5.2 Milestones

Size is relative effort of focused work: S is days, M is one to two weeks, L is three weeks or more. These are estimates, not commitments. The pilot depends mainly on when inputs and reviews arrive.

| ID | Milestone | Spec work packages | Release gate | Size | External inputs |
|---|---|---|---|---|---|
| M0 | Foundations and run spine | WP1, WP2 | R0 | M | None |
| M1 | Engines standalone | WP3 | | M | O03, O04 for real PyDoublet |
| M2 | Plant coupling | WP4 | | M | None |
| M3 | Screening and candidates | WP5 | R1 | M | O09 for real criteria |
| M4 | Operation and economics | WP6 | | L | O08, O10 for real values |
| M5 | Uncertainty and decision rules | WP7 | R2 | M | O05, O14 for real definitions |
| M6 | Services, API, MCP tools, first UI | WP9 | | L | O12 for deployment |
| M7 | Assistant and its evaluation | WP10 | R4 | L | O12, O13 |
| M8 | Wuppertal pilot | WP0, WP8 | R3 | L | O01, O02, O06 to O11 |
| M9 | User evaluation, packaging, extensions | WP11 | R5 begins | L | O13 |

M8 can overlap anything after M4. The assistant benchmark in M7 starts on synthetic reference cases and gains pilot cases when M8 delivers them.

### 5.3 Milestone detail

#### M0. Foundations and run spine

Build:

- Repository, environment, lock file, linting, type checking, test runner, CI.
- `domain`: the 13 core records of §7.2, units and conversions, provenance types, the known, unknown and conflict input states, the seven adapter states of §7.4, and the four separate result statuses of §7.5.
- `domain` time series: timezone aware intervals in UTC, daylight saving and leap year handling, power versus energy semantics.
- `store`: content addressed datasets, scenario revisions with field level change records, run and result records with parent links, policies.
- `pipeline`: stage interface, dependency hashing, cache, runner, placeholder stages.
- Command line: validate, run, show, replay.
- `requirements/register.yaml`, seeded with W01 to W36, O01 to O14 and A01 to A18.
- Analytic fixtures with hand derived expected values: exchanger energy balance, a hydraulic resistance case, an elevation pressure case, constant and variable cash flow discounting.

Gate (R0): schemas validate, records round trip without loss, and ambiguous units, missing source references, invalid times and inconsistent pressure references are rejected. A07 and A10 pass at fixture level. A placeholder run replays to an identical record (the mechanism behind A15 and A17).

#### M1. Engines standalone

Build:

- Source port and capability record.
- Deterministic test adapter. Its results are labelled synthetic and can never enter a pilot ranking (§20.13).
- PyDoublet adapter, written against Jan's actual API when it arrives: run Jan's reference scenario directly, run it through the adapter, compare normalised results and diagnostics.
- Source performance envelope over flow and injection temperature, with its validated domain and approximation error recorded.
- Network port and pandapipes adapter: canonical network to engine network, a four consumer hot water network, hydraulic and thermal cases.
- Topology checks: node snapping, false connections at crossings, islands, duplicate pipes, supply and return pairing.
- Conformance suites that any source or network adapter must pass.

Gate: adapter results equal direct tool results at the same inputs. Unsupported capabilities return explicit states. A04, A06 and A13 pass at adapter level. No AI and no shared state between runs.

#### M2. Plant coupling

Build:

- Exchanger: enthalpy model, counterflow terminal check, effectiveness or UA model, approach temperature at both ends, pressure loss.
- Heat pump: performance map interface, plus a clearly labelled screening model.
- Plant component registry: direct exchanger, heat pump assisted arrangement, pressure separation exchanger.
- Coupling loop of §10.4 with damping, residuals for temperature, heat and flow, and a numerical policy record.
- Energy ledger from reservoir extraction to customer delivery, with an audit of every slack source exchange.

Fixtures: a passing case, insufficient temperature, insufficient power, forced non convergence.

Gate: A01, A02, A03, A05 and A13 pass. The same frozen input reproduces the same result within tolerance. No hidden slack heat.

#### M3. Screening and candidates

Build:

- Ingestion: dataset registration, GeoPackage and Parquet importers, validation report, raw to canonical mapping files.
- Site screening of §8.2: reprojection, logged geometry repair, usable pad area after setbacks, access and constraint checks, each criterion recorded as pass, fail or unknown with its evidence.
- Route generation of §8.3: route graph, several distinct alternatives, separate trench and pipe quantities, crossings, elevation profile, linked customers.
- Candidate identity and enumeration: site, target and well configuration, connection pair, route, integration mode, customers, operating policy.
- Synthetic study, first full version (section 6).
- Candidate table, rejection table, map layers.

Gate (R1): A11 and A12 pass. Source results from one site are never combined with a route from another. Feasible and failed cases are both explained. Rankings state that they cover the evaluated options only.

#### M4. Operation and economics

Build:

- Representative operating cases: winter peak, shoulder season, summer minimum, source outage.
- Dispatch strategy port and a rule based strategy that respects existing base load, availability, minimum output and maintenance, and records curtailed and unserved heat.
- Chronological annual run. Any interval reduction is accepted only after its error is measured against the full run.
- Cost ledger with asset, cost owner, location, inclusion flag and source for each item, reconciled against upstream source economics.
- Cash flow model: asset specific lives, replacement, residual value, zero discount rate, LCOH at plant and delivered boundaries, undefined LCOH when no heat is delivered.
- Baseline comparison: incremental cost and NPV, funded and unsubsidised cases kept separate.
- KPI registry.

Gate: A07, A08, A09 and A10 pass. An underused geothermal plant earns no credit for unused heat. A longer route carries the correct supply and return quantities.

#### M5. Uncertainty and decision rules

Build:

- Uncertainty model records: variables, distributions or scenario sets, correlation groups, success event definitions.
- Joint sampling with common random numbers across candidates, recorded seeds, full sample accounting.
- Risk outputs of §14.4: probability of meeting service conditions, quantiles, regret, adoption thresholds, and a pre drilling expected case that includes failure and fallback.
- Eligibility separation and ranking by a ranking policy record. A Pareto view. An explicit outcome for when no candidate is defensibly preferable.
- Evidence report and export.

Gate (R2): A14 passes. Every probability states its denominator. Unresolved numerical runs are reported separately from physical failures.

#### M6. Services, API, MCP tools and first UI

Build:

- Application services: study, revision, evaluation, comparison, export, jobs with progress and cancellation, access checks before retrieval, execution and export.
- HTTP endpoints of §16.4.
- One MCP server with the 12 tools of §16.2 and the result contract of §16.3, plus compatibility wrappers for the six interim `geo_*` tools.
- UI: study header, map, scenario editor with forms generated from the schemas, comparison view that shows eligibility before cost, evidence view, job progress. German and English labels. Accessibility requirements of §18.4.
- Contract tests proving the same request gives identical run inputs through the command line, the API and MCP.

Gate: A15, A16 and A17 pass. A user can inspect assumptions and compare variants without a terminal.

#### M7. Assistant and its evaluation

Build:

- One orchestrating assistant with limits on tool calls, tokens, wall time, retries and Monte Carlo workload. No tuning of inputs until a candidate passes.
- Context assembled from structured state. A resumed session resolves to an exact scenario revision or asks which one is meant.
- Permission filtered document retrieval with locators. Retrieved text is treated as untrusted evidence. Extracted values enter as proposed assumptions only.
- Rendering of every number from a result record.
- Frozen request suite: each supported workflow, ambiguous and adversarial follow ups, a document carrying an embedded instruction.
- Benchmark runner and the metrics of §21.2.

Gate (R4): zero fabricated KPIs and zero unauthorised data access on the full suite. Mandatory deterministic cases reproduce expected inputs, eligibility outcomes and values within tolerance. Engineering validity and assistant performance are reported separately.

#### M8. Wuppertal pilot

Build:

- Pilot charter: area, baseline heat service, principal question, two or three alternatives.
- Import of approved datasets with raw to canonical mappings.
- Reconstruction of the current network and operating state.
- Comparison with measurements for representative periods and, where possible, independent validation periods.
- Review of deviations and gaps with WSW. Tolerances agreed before results are judged.
- Agreed variants, which become reference scenarios for the assistant benchmark.

Gate (R3): A18 passes. The baseline validation report is reviewed by WSW and the model specialists.

#### M9. User evaluation, packaging and extensions

Build:

- Task sessions with WSW users. A comparison of the form workflow against the assistant on the same backend.
- Revisions to the assumption display, explanations and controls.
- The handover package of §23.3.
- Research extensions, each behind its entry point and each kept only if it shows a measured benefit: model based checker, transient thermal simulation, electricity network check, storage, multiple injection wells, two stage decisions, value of information.

---

## 6. The synthetic study

Its purpose is a public twin that exercises every feature, so that progress never waits for restricted data and every test can run anywhere.

Contents:

- A hot water network with four consumers and elevation differences.
- Several candidate sites, including one excluded by a land restriction, one whose temperature is inadequate for direct heat, and one reached only by a longer route that passes additional customers.
- At least two connection pairs, so nothing can assume the single fixed connection of the workshop demonstration.
- An existing source that covers the summer base load, and a backup source with finite capacity.
- A year of interval demand, availability and price data.
- A cost set and an uncertainty model with declared, obviously synthetic values.

All inputs are defined by us and all expected values are derived independently. The workshop demonstration figures (about 52.22 and 57.4 euros per MWh for Alpha and Gamma) are not used as numerical targets unless the original scenario files are recovered (§23.1).

---

## 7. Verification

| Layer | What it establishes | Location |
|---|---|---|
| Analytic fixtures | Formulas agree with hand derivations | `tests/analytic` |
| Property tests | Energy conservation, unit round trips, hash stability | `tests/analytic` |
| Conformance suites | Any adapter honours its port, including failure states | `tests/conformance` |
| Integration fixtures | Coupled pass, physical fail and numerical fail cases | `tests/integration` |
| Acceptance tests | A01 to A18 | `tests/acceptance` |
| Replay | A report's frozen inputs reproduce its result with no assistant | `tests/acceptance` |
| Assistant benchmark | Tool selection, revision correctness, provenance, false feasibility | `tests/agent`, `benchmarks` |

Where each acceptance test is first met:

| Milestone | Acceptance tests |
|---|---|
| M0 | A07 and A10 at fixture level, mechanisms for A15 and A17 |
| M1 | A04, A06, A13 at adapter level |
| M2 | A01, A02, A03, A05, A13 |
| M3 | A11, A12 |
| M4 | A07, A08, A09, A10 in full |
| M5 | A14 |
| M6 | A15, A16, A17 in full |
| M8 | A18 |

Each release report keeps four claims separate (§21.1): code correctness, model applicability, data quality and decision uncertainty. Passing tests establishes only the first.

---

## 8. Inputs needed, and what stands in until they arrive

| Input | From | Open decision | First needed | Until then |
|---|---|---|---|---|
| Pilot area, question, baseline, alternatives | Tanja, WSW | O01, O02 | M8 | Synthetic study |
| PyDoublet package, API, reference scenario and outputs | Jan | O03 | M1 | Test adapter, labelled synthetic |
| Support for fractures, scaling, multiple injectors, decline | Jan, specialists | O04 | M1, M9 | Capability reported as unsupported |
| Success event, distributions, dependence | AP1 to AP3 specialists | O05 | M5 | Scenario sweeps, no probability claims |
| Studies, raw datasets, permitted use | WSW | O06 | M8 | No restricted import |
| Carrier, temperatures, pressure zones, connection pairs | WSW network planning | O07 | M8 | Synthetic network |
| Demand and source series, operating rules, market interface | WSW dispatch | O08 | M4 for realism, M8 for the pilot | Synthetic series, rule based dispatch |
| Pad area by phase, access, land, acceptance criteria | WSW, site specialists | O09 | M3 for realism, M8 for the pilot | Configurable screening policy |
| Cost sources, boundary, lives, discounting | WSW economics | O10 | M4 for realism, M8 for the pilot | Labelled synthetic cost set |
| Technical limits, validation tolerances | WSW, reviewers | O11 | M8 | Policy records marked unreviewed |
| Model endpoint, deployment, access model | Organisations | O12 | M6, M7 | Local environment, synthetic data only |
| UX tasks and evaluation protocol | UX team, WSW | O13 | M7, M9 | Workshop questions as tasks |
| Risk preference and ranking policy | WSW, Tanja | O14 | M5 | Trade off view, no forced winner |

None of these blocks M0 to M7. All of O01, O02 and O06 to O11 are needed before any pilot conclusion.

---

## 9. Risks

| Risk | Response |
|---|---|
| Building a general planning agent before one reference case is correct | The assistant arrives in M7, after the deterministic chain and its tests |
| Upstream geology treated as more mature than it is | Capability records, model family and probability definitions travel with every source result |
| Geothermal utilisation overstated | Chronological dispatch with existing base load from M4. No full load hour shortcuts. |
| Hidden slack sources, unit errors, duplicate costs, stale context | Energy ledger, unit carrying fields, cost reconciliation, dependency hashing |
| Compute cost of candidates times samples times intervals | Source envelope inside its validated domain, hash based caching, measured interval reduction, background jobs |
| pandapipes or PyDoublet API changes | Pinned versions, adapters, conformance suites rerun on every upgrade |
| The real PyDoublet differs from what the test adapter assumed | The port is kept minimal, and the adapter is the only code that knows the engine |
| Pilot data late, incomplete or restricted | Track A never depends on it. Unknowns stay visible as unknowns. |
| Interim server and new system diverge | Compatibility wrappers and shared regression runs until the interim server is retired |
| A convincing answer that hides an assumption | Assumptions and failure states shown by default. Numbers rendered from records only. |

---

## 10. Defaults chosen, and what I need from you

Defaults I have chosen. Each can be changed before M0 at no cost.

1. This folder becomes the repository.
2. Python 3.12 with a standard virtual environment and a lock file.
3. FastAPI for the API, and React with TypeScript and MapLibre for the UI.
4. Claude as the assistant model during development, behind a provider interface.
5. The interim MCP server is kept as a reference and its tool names stay available until the new catalogue reproduces its runs.
6. Build order M0 to M7 on synthetic data, with M8 starting whenever pilot inputs arrive.

Two things would sharpen the plan:

1. **Where the interim server's source lives.** With it I can turn its runs into regression references and plan the compatibility layer exactly.
2. **Any fixed date**, such as a project meeting or a demonstration, that should pull one milestone earlier. For example, a thin UI or assistant slice can be brought forward on top of M2 if something needs to be shown early.

---

## 11. Progress

This section records what each finished milestone delivered and where it departed from the plan above. The plan text itself is left as approved.

### M0. Foundations and run spine (built)

Delivered as planned. Gate R0 passes.

Choices made beyond the plan:

- The assumption register is derived from the scenario and is not stored separately, so a value and its source cannot drift apart.
- Numbers are strict. A string, a boolean or NaN is rejected and never coerced into an engineering value.
- A temperature difference has its own unit suffix (`_delta_k`) and cannot be converted as if it were a temperature.
- A `synthetic` provenance type was added. Any result that depends on such a value is labelled synthetic.
- The synthetic study has no demand or price series yet. They arrive with M4.

### M1. Engines standalone (built, with two items open)

Delivered:

- Source port, with request, result and capability contracts, and a conformance suite.
- Deterministic source test engine. It has a closed form solution, a genuine non convergence regime and explicit operating limits.
- Source performance envelope with a validated domain and a recorded approximation error.
- Network port and conformance suite.
- pandapipes 0.17.0 adapter for hot water networks, checked against a direct engine run and against independent textbook formulas.
- Graph based topology checks, run as part of scenario validation.
- The source stage of the pipeline is real. The network engine joins the pipeline with the coupling stage of M2, because a network calculation needs an operating case.

Gate: adapter results equal direct engine results at the same inputs. Unsupported capabilities return explicit states. A04 and A06 are met. A13 is met at adapter level.

Open:

1. **PyDoublet adapter.** Not written. It needs the PyDoublet package, its API and reference outputs (open decision O03). The port, the conformance suite and the pipeline stage are ready for it. Until then every source result comes from the test engine and is labelled synthetic.
2. **Geometric topology checks.** Snapping of nearby nodes and pipes that cross without being connected need pipe geometry. They move to the GIS import of M3.

Schema changes, each with a migration: policy 0.2.0 adds the policy kind `source`. Result 0.2.0 adds a `synthetic` flag to every stage output.

Findings about pandapipes 0.17.0 that the adapter handles and the tests cover:

- Pressures are gauge pressures relative to the air pressure at the height of each node. The adapter converts with the engine's own barometric formula and reports in the reference of the case.
- A branch without flow is given a default temperature of 293.15 K. The adapter reports no temperature for a stagnant branch.
- The outlet temperature of a pipe is reported at the end the pipe was drawn to. For a pipe that flows against its drawing this is the wrong end, and the heat loss would be understated. The adapter rebuilds such pipes along their flow and solves again.
- A solution with an absolute pressure below zero is returned with a warning only. The adapter reports it as a physical limit that was exceeded, without node results.
- The engine uses g = 9.81 m/s2.

Choices made beyond the plan:

- pandapipes is an optional dependency, pinned to the tested release. The core works without it.
- The synthetic network's pressure reference was raised from 4 to 7.5 bar absolute. At 4 bar its highest node, 50 m above the reference, would be below zero absolute pressure. The lower value is kept in the tests as a deliberate infeasible case.

### M2. Plant coupling (built)

Delivered:

- Enthalpy models for liquid streams: a constant heat capacity that must state its basis, and a tabulated heat capacity that is integrated exactly and never extrapolated.
- Counterflow exchanger with five named limits (demand, hot end approach, cold end approach, lowest geothermal outlet temperature, surface rating by effectiveness and NTU), a fouling allowance and a pressure loss scaled from a rated point.
- Heat pump behind one interface: a tabulated performance map that is interpolated and never extrapolated, and a Carnot based screening model whose results are labelled as such. The energy balance uses a stated share of the compressor electricity that is lost to the surroundings.
- Plant registry with three arrangements: `direct_exchanger`, `heat_pump_assisted` and `pressure_separation_exchanger`. A new arrangement is one registry entry, which a test demonstrates.
- The coupling iteration of specification 10.4, with residuals for temperature, heat and flow, a recorded history of every pass and damping from the numerical policy.
- Energy ledger from reservoir extraction to consumer delivery, with a balance for the geothermal circuit, the plant and the network, and an audit of the ideal boundary of the network model.
- Records: plant, operating case, coupling case and coupling result. The scenario gains plants, operating cases and coupling cases (scenario 0.2.0, with a migration).
- The coupling stage of the pipeline replaces its placeholder. `r3chain couple` evaluates the coupling cases of a scenario file.
- Six coupling cases in the synthetic study, chosen so that every outcome occurs once. The forced non convergence, the failures of each engine and the unsupported cases are produced in the tests by changing these cases.

Gate: A01, A02, A03, A05 and A13 are met. The same frozen input reproduces the identical result. The heat of the ideal network boundary is always assigned to the plant, to a declared backup or to a reported deficit.

How the coupling is set up. These are modelling decisions that the specification leaves open. Each one should be reviewed by Tanja and Jan before pilot use:

1. **The network is solved at the supply temperature the connection requires.** Its return temperature, mass flow and heat at the connection are the demand. The iteration then runs between the source and the plant. The reason is the consumer model: a consumer takes a fixed heat, so a source that is short of power has no steady state at a lower supply temperature. A shortfall is therefore a deficit at the connection, or heat from a declared backup. It is never a silently lower temperature in the network. Operating the network below its required supply temperature would need a consumer model that responds to temperature.
2. **Each engine owns the heat accounting of its own fluid.** The plant takes the mean heat capacity of the geothermal stream from the source result and that of the network stream from the network result. A fixed 4180 J/(kg K) for network water would be 0.2 percent off between 50 and 80 degrees Celsius, which is more than the heat tolerance of 0.1 percent.
3. **Geothermal flow control.** `follow_demand` uses the smallest flow that covers the demand. If the demand cannot be covered, it uses the smallest flow that delivers as much heat as the largest flow would, so a source that is too cold is not driven to its flow limit for nothing. For a heat pump plant the smallest flow means the largest cooling of the geothermal stream and the largest compressor work. That is a stated rule and not an optimum. `fixed` takes a given flow. A dispatch strategy arrives with M4.
4. **The source is operated inside the flow range of the source policy.** When the source rejects a flow, the largest flow it accepts is found by bisection on the answers of the source model itself. No engine specific limit is read.
5. **The minimum approach is a design rule on top of the surface rating.** A very large UA never lets two streams come closer than the approach.
6. **Checks are evaluated in a fixed order, each for the duty the earlier ones permit.** A shortfall is then reported once, at the limit that causes it. A source that is both too cold and too small shows both.
7. **A backup stands in series after the plant at the same connection.** The network adapter supports one supply point. A second source elsewhere in the network needs the adapter work planned with M4.
8. **A heat pump operating point outside its performance map is a failed check.** The map is taken as the range the machine may run in.

Not modelled yet, and listed in every coupling result: heat loss and pressure loss of the route between site and connection (M3), electricity of the network circulation pump (its hydraulic power is reported), part load behaviour, and pumping of the intermediate loop. Exchanger pressure losses are reported and checked against a limit. They are not yet fed back into the network hydraulics.

Choices made beyond the plan:

- The coupling stage needs the source stage only, because the coupling cases are written out in the scenario. From M3 they come from the screened candidates.
- A coupling case that fails physically is a valid evaluation and leaves the stage complete. A case that could not be evaluated gives the stage its own state, and the stages after it then do not run for any case. Results per candidate arrive with M3.
- A coupling case at the eastern connection ends as `unsupported_model`, because the pandapipes adapter needs the pressure reference at the connection it supplies.
- Results are converged to the tolerances of the numerical policy and not beyond. The balance of the geothermal circuit can therefore show a residual of up to 0.1 percent of the heat.
- The numerical policy is now version 2. It adds absolute tolerances, damping and the settings of the network engine.
- The definitions of the synthetic study are dataset version 3.
- A unit suffix `_ratio` was added for dimensionless ratios such as a coefficient of performance.

### M3. Screening and candidates (built, with pressure-zone item in W06 open)

Delivered:

- **GIS ingestion and validation (`r3chain.geospatial.importers`, `sources`, `domain.records.spatial`):** Dataset registration and checksum + metadata (`allowed_use`, `access_group`, `owner`) verification before importing GeoPackage (`.gpkg`), GeoParquet (`.parquet`) or GeoJSON layers via declarative `.mapping.yaml` files (`layer_mapping` 0.1.0, `spatial_layer` 0.1.0). Reprojects to the study CRS, rounds coordinates to declared precision, converts raw units to canonical SI, leaves untranslatable values unset with warnings, logs validatable geometry repairs and rejects ambiguous geometries for review.
- **Site screening (`r3chain.geospatial.screening`):** Configurable criteria read strictly from `policies/screening` (`r3chain.domain.policies`). Computes usable pad area after per-kind restriction setbacks (missing setback or nearby rejected restriction leaves usable pad area `unknown`), access corridor reachability and width, geological target reachability, jurisdictional overlap, and leaves unassessed criteria (noise, acceptance, permitting) and non-permit notices (`NOT_A_PERMIT`) explicit.
- **Route graph and route generation (`r3chain.geospatial.graph`, `routes`, `export`):** Builds a checked corridor line graph (snapping end points within tolerance, flagging near misses, end-points near lines, and crossings without junctions instead of silently connecting them), finds distinct route alternatives up to `max_shared_length_fraction` and ensures the shortest corridor is included even when costlier, records separate `corridor_length_m`, `supply_pipe_length_m` and `return_pipe_length_m`, barrier crossings, elevation profiles, linked customers, and indicative route costs (`currency`). Exports `sites.geojson`, `routes.geojson`, `connections.geojson` and `candidates.geojson` in WGS84 via `r3chain layers`.
- **Candidate enumeration, transmission coupling and provisional assessment (`r3chain.search.candidates`, `pipeline.evaluation`):** Generates stable content-addressed candidate identifiers (`cand-...`) across screened sites, targets/well configurations, connections, routes and plants without ever crossing site and route identities. Couples each candidate's route as supply and return transmission pipes sized by the candidate policy (reporting route heat loss and pressure lift directly in `CouplingResult`), saves per-candidate `Result` records, prints candidate/rejection tables (`r3chain candidates`), and produces a provisional eligibility separation and shared-tolerance ranking over evaluated options only.
- **Multi-connection network support (`adapters.pandapipes_network`, `coupling.cases`):** pandapipes adapter accepts a network whose pressure reference is away from the supply point (`conn-east`) by solving the incompressible network and shifting solved pressures uniformly to satisfy the reference node.
- **Pipeline and CLI:** Seven active stages (`site_evidence`, `source`, `screen`, `routes`, `candidates`, `couple`, `assess`) plus three placeholders (`operate`, `cost`, `compare`). New commands `r3chain candidates RUN [--json]` and `r3chain layers RUN DIRECTORY`.
- **Synthetic study v4:** Six invented municipal-style GIS layers generated by `examples/synthetic/gis/make_layers.py` (`EPSG:25832` exported to WGS84 with `DHHN2016` heights), including intentional defects (`R1` self-touching ring repaired, `R4` bow-tie rejected) and 27 evaluated candidates across 3 screened sites (`delta` rejected at screening; `gamma` conditional on missing power-line setback; 11 ranked by indicative route cost).

Gate (R1): A11 and A12 pass (all nine acceptance criteria M1–M3 are `built`). Source results from one site are never combined with a route from another, feasible and failed/rejected options are both explained, and rankings explicitly state they cover evaluated candidates only.

Open:

- **GIS-to-network import and internal pressure zones (`W06` remains `partial`):** `LayerKind.NETWORK_PIPE` is defined, but importing a raw pipe GIS layer into a canonical `Network` with hydraulic valves separating multiple pressure zones inside one network waits on pipe-parameter/control mapping and network valve support (M1/M4 adapter scope).

Schema, unit and policy updates (each schema change with a migration):

- `scenario` `0.3.0` (adds `spatial_layers`), `policy` `0.3.0` (adds `route` policy kind), `connection` `0.2.0` (optional `location`), `coupling_case` `0.2.0` (optional `route_id`), `coupling_result` `0.2.0` (optional `transmission_route_id`), `network_case` `0.2.0` (optional `return_pressure`); new schemas `spatial_layer` `0.1.0` and `layer_mapping` `0.1.0`.
- `domain/units.py` adds `_currency_per_m` and `_currency`.
- `store/store.py` computes `policy_ref` hashes excluding `schema_version` so schema upgrades preserve unchanged policy identity.
- Policies: `screening-synthetic` v2, `route-synthetic` v1, `candidate-synthetic` v1, `ranking-synthetic` v1, and `numerical-coupling-default` v3 (`pressure_difference_tolerance_pa`).
- Definitions of the synthetic study are dataset version 4.

Modelling choices for review before pilot use (with Tanja and Jan, alongside O09):

1. **Indicative transmission pipe sizing (`candidate-synthetic` v1):** Picks the smallest step diameter (0.025 m steps) keeping peak connection mass-flow velocity <= 1.5 m/s at 975 kg/m³. This is an indicative sizing rule for coupling transmission heat and pressure losses, not a final pipe design.
2. **Provisional ranking (`ranking-synthetic` v1):** Before M4 economics exist, eligible candidates are ranked solely on indicative route cost with separate trench and pipe rates (a 1% tie band gives shared ranks and skips shared places). Conditional sites and synthetic engines are evaluated cleanly and always excluded from ranking eligibility.
3. **Unknown setbacks fail open-to-conditional, never pass:** Any nearby restriction kind without a policy setback, or any nearby ambiguous rejected restriction, makes `usable_pad_area` and the site screening `unknown`/`conditional`.

### M4. Operation and economics (built, with multi-zone temperature separation in `W16` and uncertainty in `W13` open)

Delivered:

- **Time-series ingestion and interval reduction (`r3chain.operation.series`, `reduction`):** Imports external time series (`JSON`, `YAML`, `Parquet`) declared via `StoredSeries` (`series_sources`), checking dataset `allowed_use`, `access_group`, `owner`, and SHA-256 checksum before import (`W28`). Supports interval duration checking across leap years and daylight-saving transitions (`A07`) and interval aggregation (`measure_reduction_error`) that measures energy/peak/price approximation errors against the chronological run and rejects reductions exceeding policy tolerances.
- **Dispatch port and rule-based dispatch engine (`r3chain.ports.dispatch`, `r3chain.operation.dispatch`, `tests/conformance/test_dispatch_conformance.py`):** Defines the `DispatchStrategy` interface (`X8`) with `DispatchRequest` `0.1.0` and `DispatchResult` `0.1.0`. Implements `RuleBasedDispatchStrategy` with priority-ordered existing base load, geothermal, and peak/backup units respecting scheduled maintenance windows (`geothermal_availability`), minimum output fractions, distribution loss, and electricity/heat tariffs (`W08`, `A08`). Separates `delivered`, `curtailed`, and `unserved` heat per interval with zero heat credit for curtailed surplus, rejects negative demand inputs upfront (`validate_scenario` and `build_dispatch_request`), charges full active route heat loss independent of load factor whenever geothermal delivers heat, raises `NotEstablished` if pump/compressor electricity lacks a tariff, interpolates well-pump and compressor electricity piecewise-linearly from `(0, 0)` across coupled operating points (`DispatchElectricityPoint` curve, holding the highest point's ratio above its heat and disclosing single-point proportional or piecewise-linear assumptions in `not_modelled`), and derives geothermal capacity as the largest compliant plant output across coupled operating and capacity cases (`candidate-synthetic` v2 evaluates `operating_case_ids: shoulder-season` for eligibility and `capacity_case_ids: winter-design` for capacity limits/bounds without disqualifying network limits), marked `limit_proven` when a coupled case left a deficit or `lower_bound` when all covered demand (setting `0.0 W` on `ShortfallCause.INSUFFICIENT_TEMPERATURE`). In `reduced` interval mode, stride error is verified via `measure_reduction_error` while retaining the already-computed chronological solve.
- **Cost reconciliation and lifecycle cashflow model (`r3chain.economics.ledger`, `cashflow`, `policies`, `kpis`):** Assembles cost items across `plant`, `geothermal_delivered`, and `system_delivered` boundaries (`W15`, `W35`), preventing duplicate `(asset_id, category)` ownership (`W03`, `A09`), pricing routes from separate trench length, `supply_pipe_length_m`, `return_pipe_length_m`, and barrier crossings (accepting explicit `link (<kind>)` corridor rates and raising `NotEstablished` on unpriced corridor kinds), handling whole-year asset lifetimes with replacements (requiring explicit `lifetime_years` on `REPLACEMENT` items and rejecting non-whole asset lives), linear remaining-life residual credits, positive `RESIDUAL_VALUE` credits, zero discount rate, undefined LCOH at zero delivered heat (`A10`), explicit disclosure that multi-year reservoir thermal decline is not modelled (`not_modelled`), separate `funded` vs `unsubsidised` evaluations via `FundingRule` (requiring explicit `applies_to_replacements` to control replacement grants and replacement residual credits), incremental NPV/cost comparisons against `baseline-existing` (enforcing matching delivered annual heat in `compare_to_baseline`), explicit `RouteDemandOptionResult` comparisons with vs without linked additional demand (`W33`), and extensible `KPI_DEFINITIONS` (`X10`).
- **Pipeline, M3 hardening and CLI (`r3chain.pipeline.operation_cost`, `runner`, `cli`, `geospatial`, `coupling.loop`):** Activates `operate` and `cost` stages (nine active stages total; `compare` remains placeholder; `OperateStage.requires` drops `assess` so ranking-only changes reuse dispatch and cost caches, and `_subject_results` merges stages order-independently), requires explicit `operating_scenario_id` / `economic_scenario_id` policy selectors when multiple scenarios exist, merges per-subject `kpis`, `checks`, and `diagnostics` into each candidate's `Result` record for exact replay (`A17`) while clearing `ranking_eligibility.eligible` whenever downstream evaluation is `not_evaluated` or `invalid_input`, maps residual cash-flow `ValueError` exceptions cleanly to `invalid_input` rather than `tool_failure` (while validating horizons/lifetimes/replacement years/residual signs upfront), leaves candidates `not_established` on missing economic or tariff inputs (`A12`), supports surgical cache invalidation on economic/dispatch changes (`A15`), and adds `r3chain economics RUN [--json]`. Also hardens M3 GIS import for column names with spaces/parentheses and unreadable/geometry-less Parquet files (`LayerImportError`), widens site restriction search to `max(search_distance, max_setback)`, rejects out-of-CRS `Connection.location` / site / route geometries, and checks `connection_max_heat` net of route transmission heat loss.
- **Synthetic study v5 and policies:** Adds `policies/dispatch/synthetic.yaml` (`dispatch-synthetic` v1) and `policies/economic/synthetic.yaml` (`economic-synthetic` v1), updates `policies/candidate/synthetic.yaml` to v2 (`operating_case_ids: shoulder-season`, `capacity_case_ids: winter-design`), adds 8,760-hour 2026 UTC series (`examples/synthetic/series/annual_2026.json` via `make_series.py`), updates synthetic definitions v5 (`examples/synthetic/definitions.md`), `source-outage` operating case (defined in `scenario.yaml`, reserved for M5 resilience evaluation and unused in M4 `candidate-synthetic` v2), `operation-base` operating scenario, `funding_rules` (`applies_to_replacements: false`), and scoped `cost_items` (including `route-alpha-west-capex` scoped cleanly to `route-alpha-west`).

Gate: `A07`, `A08`, `A09`, and `A10` pass (all thirteen acceptance criteria M1–M4 are `built`). An underused geothermal plant receives zero credit for curtailed heat, longer routes carry separate supply/return quantities and losses, and upstream/route/system costs reconcile without double counting.

Open:

- **Internal temperature zones (`W16`) and uncertainty (`W13`):** Multi-temperature-zone separation inside one hydraulic `Network` waits on network-adapter valve/zone support alongside `W06`; probabilistic success likelihood and joint uncertainty sampling belong to M5.

Schema, unit and policy updates (each schema change with a migration):

- `policy` `0.4.0` (adds `PolicyKind.ECONOMIC`), `operating_scenario` `0.2.0` (existing heat sources, tariffs, geothermal availability, minimum output, distribution loss), `economic_scenario` `0.2.0` (`funding_rules` with `applies_to_replacements`, `carbon_monetised`, scoped `CostItem.applies_to`), `scenario` `0.4.0` (`time_series`); new contract schemas `dispatch_request` `0.1.0` (`DispatchElectricityPoint` curve, `capacity_basis`) and `dispatch_result` `0.1.0`.
- `domain/units.py` adds `_currency_per_j`, `currency/J`, `currency/kWh`, `currency/MWh`; `domain/timeseries.py` annotates `IntervalSeries.values` with unit metadata.
- Policies: `candidate-synthetic` v2, `dispatch-synthetic` v1, and `economic-synthetic` v1. Synthetic definitions updated to version 5.

Modelling choices for review before pilot use:

1. **Maximum coupled capacity with proven-limit vs lower-bound tagging and capacity cases (`candidates.py`, `dispatch.py`):** Because coupling is demand-following (`plant_heat_w` tracks demand until a limit stops it), geothermal capacity is taken as the largest compliant output across coupled operating and capacity cases (`operating_case_ids: shoulder-season` and `capacity_case_ids: winter-design`), tagged `limit_proven` if any coupled case hit a deficit/shortfall or `lower_bound` if all covered demand with zero deficit; cases failing `ShortfallCause.INSUFFICIENT_TEMPERATURE` assign `0.0 W`. Connection-level winter pressure failures in a capacity case (such as `conn-east` `network:positive_absolute_pressure`) leave capacity at `lower_bound` and emit diagnostics without disqualifying candidate eligibility.
2. **Full active route heat loss and piecewise-linear / disclosed simplifications (`dispatch.py`, `cashflow.py`):** Active transmission route heat loss is charged in full whenever geothermal delivers heat rather than scaled by load factor; pump and compressor electricity is interpolated piecewise-linearly from `(0, 0)` across coupled operating points (holding the top point's ratio above its heat, falling back to proportional scaling when only one coupled operating point is available, both disclosed in `not_modelled`), and multi-year reservoir thermal decline is explicitly flagged as not modelled (`year-1` operation repeats over the horizon).
3. **Route CAPEX and demand comparisons on three separate boundaries (`economic-synthetic` v1):** Plant-boundary excludes transmission route and existing-system costs; `geothermal_delivered` includes geothermal plant and sized transmission route; `system_delivered` adds existing base-load and backup energy/asset costs (`baseline-existing` compared identically on the system boundary), with `RouteDemandOptionResult` re-evaluating routes with vs without linked additional demand customers. Grants apply only when explicitly configured via `FundingRule` (`applies_to_replacements` controlling replacement grants and replacement residual credits) in the `funded` case, and positive `RESIDUAL_VALUE` entries and `linear_remaining_life` credits reduce terminal-year net cost.
