# R3CHAIN Geothermal Planning System: Implementation Plan, version 2

Version 2, 17 November 2026. Based on the Implementation Specification v1.0 (15 November 2026).
Status: approved by the user on 17 November 2026 as the plan in force from M5 on. Version 1 (`implementation-plan.md`, approved on 15 November 2026) remains the record of what M0 to M4 delivered. The research frame of section 12 is still a proposal for review by Tanja.

References such as §7.2 point to sections of the specification. W, P, O and A identifiers are the specification's own (workshop points, proposals, open decisions, acceptance tests). M0 to M9 are the milestones of this plan, and X1 to X18 are its extension points. E1 to E5 are the experiments of §21.4. C1 to C3 are the research contributions of section 12. D1 to D6 are the decisions this version asks for. V1 to V13 are the build items this version adds (section 10).

---

## Changes from version 1

### Why there is a second version

Two things were reviewed on 17 November 2026.

1. **The Fraunhofer IEG page "User-Centered AI Methods for Energy Applications"**, the competence centre headed by Prof. Dr. Dr. Tanja Kneiske. It describes a research programme: pandapipes and its extension to pandapipesAI, agentic AI as assistance systems that combine data analysis, simulation and evaluation, and explainable AI. Its applications include deep geothermal energy with energy infrastructures, heat planning across sectors, resilient infrastructures, cybersecurity in coupled electricity and heat networks, and the integration of municipal data.
2. **Advice that the doctorate must contribute a method** and cannot rest on implementing the software.

The page adds no requirement to the planning system. Its programme is broader than R3CHAIN, and nothing in it contradicts the specification. The engineering plan therefore stands.

The advice is correct, and the specification already says the same in §21.3 and §21.4: building the interface does not answer the research question, and the contribution needs an explicit method, comparative baselines, held out tasks and measured failure behaviour. Version 1 did not plan for that. It planned the assistant acceptance tests of §21.2 and one comparison with users in M9. It left out the baselines and ablations of §21.4, the split into development and held out tasks, repeated trials and the literature review.

This matters for the build order. An assistant built without switchable baselines cannot be compared against them later without rebuilding it. Version 2 closes that gap.

### What changes

| Area | Version 1 | Version 2 |
|---|---|---|
| M0 to M4 | Built | Unchanged. Version 1, section 11 stays the record of what they delivered. |
| Architecture, change mechanisms, schema rules (sections 3 and 4) | As approved | Unchanged, with two rules and three extension points added (X16 to X18) |
| M5 | Uncertainty and decision rules | The same, plus decision drivers and ranking stability, a set of resilience cases, and a point estimate mode kept as the baseline of E4 |
| M6 | Services, API, MCP tools and UI as one block | Two steps. Step 6.1 delivers services, API, MCP tools and explanation records with no UI. Step 6.2 delivers the planner workspace. M7 starts after step 6.1. |
| M7 | Assistant and the benchmark of §21.2 | The assistant is built with switchable configurations, a deterministic claim check, session traces, a fault catalogue and an experiment harness. E1 to E3 run here. |
| M8 | Wuppertal pilot | Unchanged |
| M9 | User evaluation, packaging, extensions | The user comparison becomes experiment E5 with a protocol. The model based checker leaves the extension list because it is E3 in M7. |
| Tracks | A (build) and B (inputs) | Track C (research) is added: literature review, protocols, experiments, publications |
| Scope | Not stated against the competence centre | Section 12.1 states which parts of its programme are in, partly in and out |

Milestone identifiers do not change. The requirements register and its tests therefore stay valid.

### What does not change

- Nothing that is built is reopened.
- The four rules of section 3.1, the eight mechanisms of section 4.1 and the schema rules of section 4.3.
- The pilot (M8) and everything it needs.
- The release criteria of §21.2. The experiments are added to them. They do not replace them.

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
| 5 | Assistant and its experiments | A bounded orchestrator that plans, calls tools, checks and explains. Around it: explanation records whose claims are checked against result records, a frozen request suite with a held out part, and a harness that runs the comparisons of §21.4 | Planners, and the doctoral research |

Two studies run on it:

- **Synthetic study**: public, lives in the repository, four consumers, several sites, at least two connection choices.
- **Wuppertal pilot**: restricted data, stored outside the repository, one documented baseline and two or three agreed alternatives.

**Done means** (§23.3): a planner can run the agreed study, inspect and change an assumption, reproduce the reference result and understand an excluded option, and a technical reviewer can trace any number back to source evidence and model runs.

**Done for the research means** (§21.3, §21.4): each planned experiment has run against its baselines on held out tasks with repeated trials, its variability is reported, and its result is reported whether or not it favours the assistant.

**Not in the first demonstrator** (§3.3): three dimensional reservoir simulation, a steam or two phase network model, real time plant control, automatic investment decisions, and any determination of permit or land approval. Section 12.7 lists what is outside the research scope.

### How complete is the picture

The structure of the product is fully determined by the specification: the components, the records, the calculation sequence, the tools, the tests and the build order are all stated.

What is not determined are the 14 open decisions (O01 to O14) and the six decisions of this version (D1 to D6, section 10). The open decisions are values, datasets and reviews. None of them changes the structure. This plan therefore fixes the structure now and treats every open input as data that arrives later through a defined entry point (section 4).

Four things cannot be known from the specification alone, and the plan isolates each behind an interface:

1. The real PyDoublet API, model families and reference outputs (O03, O04).
2. The format, coverage and quality of the WSW network, demand and operating data (O06 to O08).
3. The source code and behaviour of the existing interim MCP server (section 2).
4. What pandapipesAI is and which interface it offers (D2).

---

## 2. Starting point

| Item | State on 17 November 2026 | Consequence for the plan |
|---|---|---|
| Repository | M0 to M3 are committed. M4 is built on the branch `m4-operation-and-economics` and is not yet committed. | M4 is checked and committed before M5 starts |
| pandapipesAI | Named on the Fraunhofer IEG page as the extension of pandapipes that links network models with AI methods. Its interface and release state are not known to this plan. | Nothing is built against it until D2 is answered. The network port keeps the engine isolated. |
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
Research       experiment harness: protocols, configurations, request suites, faults, analysis
               |
Assistant      bounded orchestrator with a switchable configuration, session traces
               |
Interfaces     CLI          HTTP API        MCP server        Web UI
               |              |                |                |
Application    study, revision, evaluation, comparison, explanation, export, jobs, access
               |
Pipeline       screen > source > couple > operate > cost > compare
               (the uncertainty sampler wraps the whole chain per realisation)
               |
Ports          SourceModel   NetworkModel   DispatchStrategy   LLMProvider   Stores
               |
Adapters       PyDoublet     pandapipes     importers          model API     SQLite + files
```

The two top layers are clients of the interfaces. The assistant reaches the system only through the MCP tools, and the experiment harness reaches the assistant only through its public entry point. Neither can change a calculation.

Six rules hold everywhere. The first four are those of version 1.

1. **Dependencies point inwards.** The domain and pipeline never import an engine, a web framework or a model provider. They see only ports.
2. **One service layer.** The command line, API, MCP server and UI all call the same application services, so the same request produces the same run inputs through every route.
3. **Nothing computes outside a frozen revision.** A calculation receives an immutable scenario revision and data snapshot. No solver mutates its inputs.
4. **Every displayed number comes from a result record.** Prose can explain a number. It cannot create one.
5. **An explanation is data before it is prose.** It is a list of typed claims, and each claim points at result records, checks or sources. A deterministic check accepts or rejects each claim. A language model may word a claim. It cannot make one true.
6. **The research instruments are records.** Assistant configurations, request suites, faults and session traces have schemas and versions, like policies. An experiment is a run that can be replayed and audited.

### 3.2 Repository layout

This extends the layout proposed in §20.2 with the directories that make change cheap (`ports`, `pipeline`, `store`, `policies`, `schemas`, `requirements`). Version 2 adds `explain` and `experiments`.

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
    explain/                claim types, explanation records, claim check, fixed wording
    services/               application services, jobs, access checks
    api/  mcp/              HTTP endpoints and typed tools
    assistant/              orchestration, configurations, context, retrieval, rendering, traces
    reporting/              evidence report and export
  tests/
    analytic/               hand derived expected values
    conformance/            suites every adapter implementation must pass
    integration/            coupled fixtures
    acceptance/             A01 to A18
    agent/                  frozen assistant requests, development part
  benchmarks/               runners and reference outcomes, held out part of the request suite
  experiments/              protocols, assistant configurations, fault catalogue, analysis
  frontend/                 browser UI
```

Restricted pilot data is never placed in this tree. It lives in configured storage outside the repository. The same holds for session traces and experiment results that contain pilot data.

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
| Assistant model | Claude during development, behind a provider interface. The experiments should use at least two models where access allows (O12). | X13 |
| Assistant configuration | A versioned record that selects context design, tool access and checking role | X16 |
| Experiment analysis | SciPy: paired comparisons and interval estimates from repeated trials | |
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

**6. Registries for things that grow.** Plant components, KPIs, cost item types, failure states, tools, claim types and faults are registered entries with their own schema and tests. Adding thermal storage or a CO2 metric is a new entry.

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
| X15 | A new requirement from Tanja, Jan, WSW or the UX team | Requirements register | Classify, route to X1 to X18, add a test | Depends on class |
| X16 | A different assistant configuration (context design, tool access, checking role, model) | Assistant configuration record | New configuration, rerun the affected experiment | None, or one strategy class |
| X17 | A new kind of claim or explanation rule | Claim registry in `explain` | Claim schema, check function, fixture | New entry only |
| X18 | A new fault or adversarial condition for the assistant | Fault catalogue in `experiments` | Fault record, injection at a port, expected behaviour | New entry only |

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

### 5.1 Three tracks

- **Track A, build.** M0 to M7 run entirely on the synthetic study and the test adapter. This track never waits for an external input.
- **Track B, inputs.** Work package 0 of the specification: the pilot charter, data access, PyDoublet handover, user questions. It starts on day one and runs in parallel. M8 begins when its inputs land.
- **Track C, research.** New in version 2. It starts now and runs in parallel. It delivers the literature review and the statement of what is new (section 12.6), one protocol per experiment, the experiments themselves and the publications. It depends on Track A for its instruments and on nothing else until the pilot.

The first thing built is an end to end run with placeholder stages that return `not_established`. Each milestone then replaces placeholders with real calculations. There is a complete, replayable run at every point, and adding a stage is the routine operation.

The same principle holds for the research. Every experiment has a baseline arm that exists in the code as a configuration from the first day the assistant runs. No comparison is reconstructed after the fact.

### 5.2 Milestones

Size is relative effort of focused work: S is days, M is one to two weeks, L is three weeks or more. These are estimates, not commitments. The pilot depends mainly on when inputs and reviews arrive.

| ID | Milestone | Spec work packages | Release gate | Size | State | External inputs |
|---|---|---|---|---|---|---|
| M0 | Foundations and run spine | WP1, WP2 | R0 | M | Built | None |
| M1 | Engines standalone | WP3 | | M | Built. PyDoublet adapter open. | O03, O04 for real PyDoublet |
| M2 | Plant coupling | WP4 | | M | Built | None |
| M3 | Screening and candidates | WP5 | R1 | M | Built | O09 for real criteria |
| M4 | Operation and economics | WP6 | | L | Built, not committed | O08, O10 for real values |
| M5 | Uncertainty and decision rules, with resilience cases | WP7 | R2 | M | Next | O05, O14 for real definitions |
| M6 | Services, API, MCP tools, first UI. Step 6.1 without UI, step 6.2 the workspace. | WP9 | | L | | O12 for deployment, D2 |
| M7 | Assistant and its evaluation, with experiments E1 to E3 | WP10, §21.4 | R4 | L | | O12, O13, D1 |
| M8 | Wuppertal pilot | WP0, WP8 | R3 | L | | O01, O02, O06 to O11 |
| M9 | User evaluation as experiment E5, packaging, extensions | WP11, §21.4 | R5 begins | L | | O13, D5, D6 |

Order of work from here: M5, step 6.1, then M7 and step 6.2 side by side, then M9. M8 can overlap anything after M4.

Three rules for the order:

- **M7 starts after step 6.1** and does not wait for the workspace. The experiments E1 to E3 need tools, an assistant and a request suite. They need no browser UI.
- **Planners do not get the assistant before the workspace exists.** Work package 9 asks for maps and forms before or alongside chat, so M9 needs both step 6.2 and M7.
- **E4 needs no assistant.** It compares two evaluation workflows and can run as soon as M5 is built.

Every experiment runs first on the synthetic study, so that it can be published and repeated by others. It gains pilot cases when M8 delivers them.

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

Added in version 2:

- **Decision drivers and ranking stability.** For the leading candidates: the share of samples in which each one ranks first, with its denominator, and the uncertain variables whose value changes the order. Both are read from the paired samples and need no extra model runs. Work package 7 already lists sensitivity as an output. The explanations of M6 and experiment E4 need it.
- **Resilience cases.** A named set of outage cases: loss of the geothermal source (the `source-outage` operating case that M4 defined and no policy uses yet), loss of the backup, loss of the existing base load, and loss of the electricity supply of a heat pump plant. Each reports unserved heat, hours with unserved heat and the largest deficit through KPI registry entries. These are scenario cases. They never enter a probability.
- **A point estimate mode.** One realisation at central values, kept as a named policy. It is the baseline of E4 and must stay runnable after the uncertainty workflow exists.
- **Progressive sample allocation** (§15.1) as a second named policy, accepted only after its error is measured against the full sample set.

Gate (R2): A14 passes. Every probability states its denominator. Unresolved numerical runs are reported separately from physical failures. The decision driver table is reproduced from the stored samples. The point estimate mode and the full uncertainty workflow run on the same frozen revision.

#### M6. Services, API, MCP tools and first UI

Version 2 builds this milestone in two steps. The content of version 1 is unchanged. Explanation records are added to the first step.

**Step 6.1. Services, API and MCP tools, without a UI.**

- Application services: study, revision, evaluation, comparison, export, jobs with progress and cancellation, access checks before retrieval, execution and export.
- HTTP endpoints of §16.4.
- One MCP server with the 12 tools of §16.2 and the result contract of §16.3, plus compatibility wrappers for the six interim `geo_*` tools.
- Every tool separates the five failure classes of §16.3: invalid request, denied access, failed physical check, numerical non convergence and service fault.
- **Explanation records** (`explain`). For a result, a rejection or a comparison, the service builds a list of typed claims: a value, a status, a cause of failure, a comparison between candidates, an open assumption. Each claim carries references to result records, checks, policies or sources. For a failed option it states the failed check, the observed value, the limit, the interval or sample and the upstream evidence (§15.4).
- **Claim check.** A deterministic function that accepts or rejects one claim against the stored records. A value must match its record within the declared rounding. A feasibility claim needs a passing technical status. A ranking claim needs eligibility. A cause must name a check that failed.
- **Fixed wording** in German and English for every claim type, with no language model. This is what the evidence view and the report show, and it is the baseline the assistant's wording is measured against.
- Contract tests proving the same request gives identical run inputs through the command line, the API and MCP.

An explanation never searches for inputs that would make a candidate pass. A redesign search exists only as an explicit user request (§17.1) and is a research extension of M9.

Gate of step 6.1: A15, A16 and A17 pass at service and tool level. Every explanation of the synthetic study passes the claim check, and a deliberately wrong claim of each type is rejected.

**Step 6.2. Planner workspace.**

- UI: study header, map, scenario editor with forms generated from the schemas, comparison view that shows eligibility before cost, evidence view, job progress. German and English labels. Accessibility requirements of §18.4.
- The evidence view shows explanation records with their links, in the fixed wording.

Gate of step 6.2: A15, A16 and A17 pass in full. A user can inspect assumptions and compare variants without a terminal.

**Relation to pandapipesAI.** No tool is built against pandapipesAI until its interface is known (D2). The network port already isolates the engine, so adopting such an interface later is an adapter change (X5). Section 12.5 lists what this project can offer to the pandapipes developers in return.

#### M7. Assistant and its evaluation

Starts after step 6.1.

Build, as in version 1:

- One orchestrating assistant with limits on tool calls, tokens, wall time, retries and Monte Carlo workload. No tuning of inputs until a candidate passes.
- Context assembled from structured state. A resumed session resolves to an exact scenario revision or asks which one is meant.
- Permission filtered document retrieval with locators. Retrieved text is treated as untrusted evidence. Extracted values enter as proposed assumptions only.
- Rendering of every number from a result record.
- Frozen request suite: each supported workflow, ambiguous and adversarial follow ups, a document carrying an embedded instruction.
- Benchmark runner and the metrics of §21.2.

Added in version 2, so that the comparisons of §21.4 can be run:

- **Assistant configuration record** (X16). One versioned record selects each switchable part:

  | Part | Baseline setting | Method setting | Experiment |
  |---|---|---|---|
  | Context | Conversation summary only | Typed scenario state with provenance aware retrieval | E1 |
  | Tool access | Direct prompting without typed tools | Typed tools with deterministic validation | E2 |
  | Checking | Deterministic validators and claim check only | The same, plus a model based checker | E3 |

  The model, its version, its settings and the budget are part of the record. Every session stores the configuration it ran with.
- **Session trace record.** Requested objective, selected tools, arguments, revisions, accepted input patch, execution events, check results and final evidence links (§17.1), with model identifier, settings, token use and time. Traces are immutable and stored like runs. Tool responses can be replayed from a trace, so an analysis can be repeated without calling a model again.
- **Claim check on every answer.** The assistant's answer is broken into the claim types of step 6.1 and each claim is checked. In the method configurations an answer with a rejected claim is not shown as it stands. In the baseline configurations the check runs silently and only measures.
- **Fault catalogue** (X18). Engine non convergence, unsupported model, operating point outside the validated envelope, missing input, stale revision after an assumption change, contradictory assumptions, denied access, tool timeout, cancelled job, and an instruction embedded in a retrieved document. Faults are injected at the ports, so the engineering core is not touched.
- **Development and held out parts of the request suite**, separated by site, network configuration or question family (§21.4). The held out part is frozen before any assistant tuning and is not inspected during development.
- **A written definition of a serious planning error**, fixed before the experiments and reviewed by domain reviewers (§21.4). Proposed starting list: a candidate reported feasible that fails a physical check, a ranking that contains an ineligible candidate, a number without a supporting record, a result of a stale revision presented as current, a synthetic result presented as pilot evidence, a missing input presented as satisfied.
- **Experiment harness.** Runs a configuration against a suite with repeated trials, recorded settings and paired comparisons. It reports intervals and never only the best run.
- **Experiments E1, E2 and E3** (section 12.4), each with its protocol committed before the run.

Gate (R4): zero fabricated KPIs and zero unauthorised data access on the full suite. Mandatory deterministic cases reproduce expected inputs, eligibility outcomes and values within tolerance. Engineering validity and assistant performance are reported separately. In addition: E1 to E3 have run on the held out suite, and their results are reported with variability whether or not they favour the method configuration. A checker that agrees with the first model is not an acceptance criterion (§21.2).

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

- **Experiment E5** (section 12.4): task sessions with WSW users that compare the form workflow against the assistant on the same backend and dataset. The tasks are those of §18.3. The protocol is agreed with the UX team and WSW (O13) and covers consent and data handling under the rules of the organisations (D6). It starts with an exploratory session and then sets a sample and an analysis plan. One task has "not established" or "no candidate is defensibly preferable" as its correct answer, to see whether users rely on the system where they should not. A small exploratory sample is reported as exploratory.
- Revisions to the assumption display, explanations and controls.
- The handover package of §23.3.
- Research extensions, each behind its entry point and each kept only if it shows a measured benefit: transient thermal simulation, electricity network check, storage, multiple injection wells, two stage decisions, value of information, an optimising dispatch strategy, and a redesign search on explicit user request. The model based checker is no longer an extension. It is the method arm of E3 in M7.

Needs step 6.2 and M7.

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
| Claim check | Every claim of an explanation or an assistant answer resolves to a record, and wrong claims of each type are rejected | `tests/unit`, `tests/agent` |
| Assistant benchmark | Tool selection, revision correctness, provenance, false feasibility | `tests/agent`, `benchmarks` |
| Experiments E1 to E5 | How configurations and workflows compare, with variability, on held out tasks | `experiments`, `benchmarks` |

Every layer except the last establishes that the system behaves correctly. The last one establishes what the research claims. A passing benchmark is not an experimental result, and an experimental result does not replace an acceptance test.

Where each acceptance test is first met:

| Milestone | Acceptance tests |
|---|---|
| M0 | A07 and A10 at fixture level, mechanisms for A15 and A17 |
| M1 | A04, A06, A13 at adapter level |
| M2 | A01, A02, A03, A05, A13 |
| M3 | A11, A12 |
| M4 | A07, A08, A09, A10 in full |
| M5 | A14 |
| M6 | A15, A16, A17 at service and tool level in step 6.1, in full in step 6.2 |
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
| Wording of the research question and the three contributions | Tanja | D1 | Track C, now | The question of §21.3 |
| What pandapipesAI is, its interface and timing, and whether these tools should align with it | Tanja, pandapipes developers | D2 | Step 6.1 | The network port, no dependency |
| Whether cybersecurity of coupled networks belongs to this doctorate | Tanja | D3 | Before M5 is closed | Out of scope |
| Whether the municipal data funnel defines a format to import from | Tanja's group | D4 | M8 | Importers with mapping files |
| What may be published from the pilot, and where | Tanja, WSW | D5 | First publication | Results of the synthetic study only |
| Formal requirements of the doctorate and of a study with users | University, Tanja, UX team | D6 | M9 | No user study |

None of these blocks M0 to M7. All of O01, O02 and O06 to O11 are needed before any pilot conclusion. D1 should be answered before the first experiment protocol is written, because the protocol states the hypothesis.

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
| A system is built and no method is shown | Baselines exist as configurations from the first assistant commit. E1 to E3 are part of the gate of M7. |
| The research programme of the competence centre pulls the scope in six directions | Section 12.1 states what is in, partly in and out. Anything else enters through the register as class D or E. |
| An assistant result cannot be reproduced because the model changed | Model identifier and settings in every trace, tool responses replayable from traces, repeated trials, a frozen suite |
| The request suite is tuned to the assistant | The held out part is frozen before tuning and separated by site, network or question family |
| A model based checker shares the blind spots of the model it checks | The deterministic claim check is the acceptance criterion. E3 measures correlated failures. |
| Tools are built against pandapipesAI before its interface exists | No dependency until D2 is answered. Afterwards an adapter. |
| What is new is assumed and not shown | The literature review of section 12.6 comes before the first protocol |

---

## 10. Defaults chosen, and what I need from you

Defaults carried over from version 1:

1. This folder is the repository.
2. Python 3.12 with a standard virtual environment and a lock file.
3. FastAPI for the API, and React with TypeScript and MapLibre for the UI.
4. Claude as the assistant model during development, behind a provider interface.
5. The interim MCP server is kept as a reference and its tool names stay available until the new catalogue reproduces its runs.
6. Build order on synthetic data, with M8 starting whenever pilot inputs arrive.

Defaults new in version 2. Each can be changed before M5 is closed at no cost.

7. The working research question is the one of §21.3 (D1).
8. M6 is built in two steps, and M7 starts after step 6.1.
9. Every experiment runs first on the synthetic study.
10. Cybersecurity of coupled networks is out of scope (D3).
11. Explanations cover calculated results. They do not cover the internals of a language model.

Decisions this version asks for:

| ID | Decision | Who | Needed by | Default until decided |
|---|---|---|---|---|
| D1 | Wording of the research question and the three contributions of section 12 | Tanja | M7 | The question of §21.3 |
| D2 | What pandapipesAI is, its interface and timing, and whether the tools of step 6.1 should align with it or be offered to it | Tanja, pandapipes developers | M6 | No dependency |
| D3 | Whether cybersecurity of coupled electricity and heat networks belongs to this doctorate | Tanja | M5 | Out of scope |
| D4 | Whether the municipal data funnel defines a format this system should import | Tanja's group | M8 | Importers with mapping files |
| D5 | What may be published from the pilot under the NDA, and the intended venues | Tanja, WSW | M7 | Synthetic study only |
| D6 | Formal requirements of the doctorate, and consent and data handling for a study with users | University, Tanja, UX team | M9 | No user study |

Still open from version 1:

1. **Where the interim server's source lives.** With it its runs become regression references and the compatibility layer can be planned exactly.
2. **Any fixed date**, such as a project meeting or a demonstration, that should pull one step earlier. With version 2 the natural early demonstration is step 6.1 on the synthetic study: a typed tool call that returns a result with its explanation record.

Steps that follow from the approval of 17 November 2026:

| # | Step | State |
|---|---|---|
| 1 | One line in the header of version 1 and in the README that names version 2 as the plan in force from M5 on | Done on 17 November 2026 |
| 2 | Enter the build items and experiments of the table below in `requirements/register.yaml`, and extend the register test for the new identifiers, kinds and modules (section 4.4) | Open. After M4 is committed, as its own commit. `docs/handoff-next-steps.md` has the instructions. |
| 3 | Record D1 to D6 in the register next to the open decisions | Open. Together with step 2. |
| 4 | Start Track C: the literature review, a draft definition of a serious planning error and a protocol template | Open. It is research work and not part of the build. |

Register entries for this version. V are the build items this version adds and E the experiments. The register writes every identifier with two digits (V01, E01, D01). All start as `planned` with no tests.

| ID | Statement | Milestone | Lands in |
|---|---|---|---|
| V1 | Decision drivers and ranking stability from the paired samples, each share with its denominator | M5 | uncertainty, search |
| V2 | Resilience cases: loss of the geothermal source, of the backup, of the existing base load and of the electricity supply of a heat pump plant. Reported as unserved heat, hours and largest deficit, never as a probability. | M5 | operation, economics, policies |
| V3 | A point estimate mode as a named policy that stays runnable next to the uncertainty workflow | M5 | uncertainty, policies |
| V4 | Progressive sample allocation as a named policy, accepted only with its error measured against the full sample set | M5 | uncertainty, policies |
| V5 | Explanation records: typed claims with references to result records, checks, policies or sources | M6 | explain, services |
| V6 | A deterministic claim check that accepts or rejects each claim against the stored records | M6 | explain |
| V7 | Fixed wording in German and English for every claim type, with no language model | M6 | explain, reporting |
| V8 | An assistant configuration record that selects context design, tool access, checking role, model and budget | M7 | assistant |
| V9 | A session trace record from which tool responses can be replayed | M7 | assistant, store |
| V10 | A fault catalogue whose faults are injected at the ports | M7 | experiments, ports |
| V11 | A request suite with a development part and a held out part that is frozen before tuning | M7 | benchmarks |
| V12 | A written definition of a serious planning error, fixed before the experiments | M7 | experiments, docs |
| V13 | An experiment harness with repeated trials, recorded settings, paired comparisons and intervals | M7 | experiments |
| E1 | Experiment: evidence and context | M7 | experiments, assistant |
| E2 | Experiment: workflow reliability | M7 | experiments, assistant, mcp |
| E3 | Experiment: checking architecture | M7 | experiments, assistant |
| E4 | Experiment: uncertainty allocation | M7 | experiments, uncertainty |
| E5 | Experiment: human use | M9 | experiments, assistant, frontend |

---

## 11. Progress

Version 1, section 11 is the record of what M0 to M4 delivered and of every departure from the plan. It is not repeated here, so that there is one record. From M5 on, progress is recorded in this section.

State on 17 November 2026:

| Milestone | State | Open |
|---|---|---|
| M0 | Built and committed | |
| M1 | Built and committed | PyDoublet adapter (O03) |
| M2 | Built and committed | |
| M3 | Built and committed | Import of a pipe layer into a network, and pressure zones (W06) |
| M4 | Built on `m4-operation-and-economics`, not committed (review items closed out). | Temperature zones (W16) |
| M5 | Built on `m4-operation-and-economics`, not committed. Gate R2 passes (`A14`, `V01`–`V04`, `W02`, `W13`, `W18`, `W34`): joint correlated LHS/sweep sampling, conditional vs pre-drilling expected case, regret/Pareto/dominance preference checks, paired decision drivers, deterministic outage resilience cases, point-estimate & progressive policies, Markdown/JSON evidence reports (`r3chain compare`, `r3chain report`). | Geological success event & reviewed distributions (O05), risk preference policy (O14) |
| M6 (Step 6.1) | Built on `m4-operation-and-economics`, not committed. Step 6.1 gate passes (`V05`–`V07`, `A15`–`A17` at service/API/MCP level): `explain` typed claim records, deterministic claim check, fixed DE/EN wording, `PlanningService`, access/cache isolation, jobs with cancellation, FastAPI endpoints (§16.4), pinned MCP SDK 2.3.0 server with 12 §16.2 tools + `geo_*` compat wrappers. | `r3chain-geothermal-mcp` exact interim tool names, pandapipesAI interface (`D02`), deployment auth model (`O12`) |
| M6 (Step 6.2) | Built on `m4-operation-and-economics`, not committed. Gate 6.2 passes (`W04`, `W22`, `W24`–`W26`, `A15`–`A17`): React + TypeScript + MapLibre GL + Recharts planner workspace (`frontend/`), persistent study header with DE/EN & presentation prefs, offline WGS84 vector map + accessible text table, schema-generated scenario editor + branching, async job runner/cancellations, eligibility-first comparison & charts, claim-checked explanation links + export/replay UI, served cleanly by `r3chain serve-api`. | Real pilot UI feedback (M9 `E05`), `r3chain-geothermal-mcp` exact interim tool names, pandapipesAI interface (`D02`), deployment auth model (`O12`) |

---

## 12. Research frame and experiments

This section is new in version 2. The user approved it as the basis for building. As a research frame it is still a proposal for review by Tanja. It states how the system serves the doctorate, and it turns §21.3 and §21.4 of the specification into planned work.

### 12.1 Position against the programme of the competence centre

The Fraunhofer IEG page describes a programme of a whole competence centre. One doctorate covers a part of it. This table fixes which part.

| Area named on the page | Where it sits in this plan | Position |
|---|---|---|
| pandapipes and pandapipesAI | pandapipes behind the network port (M1, built), tools in step 6.1 | Partly in. The engine is used and checked. The link to pandapipesAI is open (D2). |
| Agentic AI as assistance systems | MCP tools (step 6.1), assistant and E1 to E3 (M7) | In. It is the main subject of the research. |
| Explainable AI | Evidence layer (built), explanation records and claim check (step 6.1, M7), E5 | In, for explanations of calculated results. Not for the internals of a language model. |
| Deep geothermal energy with energy infrastructures | M1 to M5 and the pilot (M8) | In. It is the application. |
| Heat planning across sectors | Not planned | Out. The electricity network check stays an extension of M9. |
| Integration of municipal data | Dataset registry and importers with mapping files (M3, built), X1 | Partly in, at the scale of one study. This is not a municipal data platform (D4). |
| Resilient infrastructures | Source outage case (M4), resilience cases (M5) | Partly in: heat service under stated outage cases. |
| Cybersecurity in coupled electricity and heat networks | Not planned | Out (D3). Only the safeguards of the assistant itself are in: access checks, untrusted documents, A16. |

### 12.2 Research question

The working question is the one proposed in §21.3: how an AI planning assistant can preserve scientific validity and decision reliability while it adapts its tool workflow and context to uncertain, incomplete and changing energy infrastructure evidence.

The reviewed advice proposes a broader wording: "How can trustworthy, physics-grounded agentic AI systems autonomously orchestrate heterogeneous energy infrastructure models while ensuring verifiable decisions under uncertainty?"

Three remarks on that wording:

1. **"Autonomously" needs a bound.** The specification keeps the assistant inside budgets and prohibits it from changing inputs until a candidate passes (§17.1). It excludes automatic investment decisions (§3.3). The research is about bounded autonomy under review by a planner.
2. **"Verifiable decisions" overstates.** The system can verify results and claims. The decision stays with the planner.
3. **The experiments are the same under either wording.** The choice affects the title and the framing of publications. It is Tanja's to make (D1).

### 12.3 Three contributions

The advice names three contributions. Each maps onto experiments the specification already proposes.

| ID | Contribution | Method component | Built in | Experiments | Baseline |
|---|---|---|---|---|---|
| C1 | Reliable orchestration of several models | Typed tools with one result contract and five separate failure classes, session traces, fault catalogue | Step 6.1, M7 | E2 | Direct prompting without typed tools |
| C2 | Reasoning that is bound to physics and evidence | Typed scenario state with provenance aware retrieval, explanation records, deterministic claim check, optional model based checker | Step 6.1, M7 | E1, E3 | Conversation summary only. Single assistant with validators. |
| C3 | Decisions under uncertainty that a planner can inspect | Joint sampling, eligibility before ranking, decision drivers, the outcome "no candidate is defensibly preferable", explanations in the workspace | M5, step 6.2, M9 | E4, E5 | Point estimate workflow. Forms and maps without the assistant. |

A contribution counts only as far as its experiment shows a difference against its baseline. The software components are the means. They are not the contribution.

### 12.4 Experiments

The five comparisons are those of §21.4.

| ID | Experiment | Arms | Main measurements | Needs | Earliest |
|---|---|---|---|---|---|
| E1 | Evidence and context | Conversation summary only, against typed scenario state with provenance aware retrieval | Reuse of stale inputs, handling of contradictions, accuracy of corrections, evidence coverage | Step 6.1, M7 | M7 |
| E2 | Workflow reliability | Direct prompting, against typed tool orchestration with deterministic validation | Invalid calls, false feasibility, recovery from injected faults, computational cost | Step 6.1, M7, fault catalogue | M7 |
| E3 | Checking architecture | Single assistant with validators, against the same with a model based checker | Error reduction, correlated failures, latency, cost | M7 | M7 |
| E4 | Uncertainty allocation | Point estimate workflow, against joint uncertainty with progressive evaluation | Decision regret, failure risk, ranking stability, number of engineering runs | M5 | After M5 |
| E5 | Human use | Forms and maps, against the same backend with assistant support | Task completion, detection of assumptions, success of corrections, decision errors | Step 6.2, M7, O13, D6 | M9 |

E4 can run as soon as M5 is built. It is reported with E1 to E3 at the gate of M7 at the latest.

Rules for every experiment (§21.4):

1. **The protocol is committed before the run.** It states the hypothesis, the arms, the suite, the measurements, the number of trials and the analysis.
2. **Inputs are frozen.** Datasets, model versions and expected outcomes of the reference suite do not change during an experiment.
3. **Development and test tasks are separate**, by site, network configuration or question family.
4. **Trials with a language model are repeated** with recorded settings. Comparisons are paired where the design allows it. Variability is reported, and never only the best run.
5. **A serious planning error is defined beforehand**, and domain reviewers assess disputed cases.
6. **All arms and all failed attempts are kept**, within the authorised retention policy.
7. **A result that does not favour the method is reported like any other.**
8. **Public first.** Each experiment runs on the synthetic study before it runs on pilot data. Pilot results are published only as far as D5 allows.

### 12.5 What the project can offer the pandapipes developers

This is an offer for the conversation about D2. It is not a commitment of this plan.

- **Five findings about pandapipes 0.17.0** that the adapter handles and the tests cover (version 1, section 11, M1). They concern the pressure reference, the temperature of a branch without flow, the outlet temperature of a pipe that flows against its drawing direction, a solution with an absolute pressure below zero, and the value of g. Some of these are conventions and some may be defects. Those that may be defects should be reduced to a minimal example and reported to the developers.
- **A conformance suite for network engines**, including the failure states.
- **A canonical network record and its adapter**, with explicit states for unsupported and failed cases.
- **A tool result contract** that keeps invalid requests, denied access, physical failures, numerical failures and service faults apart.
- **A public synthetic study** that can serve as a shared test case.

### 12.6 Literature review and statement of what is new

The specification is explicit that novelty must be established by a separate literature review and must not be assumed (§21.3). Track C starts with it. Nothing in this plan claims novelty.

Areas to cover:

- Language model agents that call simulation and engineering tools, including work built on the Model Context Protocol.
- Checking of generated statements against structured evidence.
- Benchmarks for agents, and testing them with injected faults.
- Language models in the planning of energy systems and heat networks, including the published work of the competence centre.
- Decision support under uncertainty and how its results are explained to planners.
- Reliance of users on AI assistance, and how it is measured.

Output: a positioning note of a few pages. For each contribution it states what is known, what R3CHAIN would add and which experiment would show it. It is written before the first protocol, because a protocol states a hypothesis.

### 12.7 Outside the research scope

- Training or fine tuning a language model.
- Interpretability of the internals of a language model.
- Cybersecurity analysis of coupled networks (D3).
- A general municipal data platform (D4).
- Development of pandapipes or pandapipesAI themselves (D2).
- Automatic investment decisions (§3.3).

Each of these can become a later project. None of them is needed to answer the research question.

