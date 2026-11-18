# Handoff: how to continue the R3CHAIN build

Written on 17 November 2026 by the reviewing model, for the model that continues the work.

Read this file completely before changing anything. The repository is the source of truth. If the code differs from what is described here, trust the code, check again, and tell the user what you found.

---

## 1. Where things stand

Verified on 17 November 2026.

| Item | State |
|---|---|
| Branch checked out | `m4-operation-and-economics` |
| Last commit | `45c5233 Add screening and candidates (milestone M3)` |
| `main` | `f5f8b72 Add plant coupling (milestone M2)`. It does not contain M3 yet. |
| Working tree | Milestone M4 (operation and economics) is built and **not committed**: 48 changed or new files |
| Checks | All pass: `ruff check`, `ruff format --check`, `mypy` (70 source files), `r3chain schemas check`, 311 tests |
| New, also uncommitted | `docs/implementation-plan-v2.md` and this file. The user approved version 2 of the plan on 17 November 2026. It is in force from M5 on. One line in the header of `docs/implementation-plan.md` and one in `README.md` point to it. |

A review of M3 and M4 on 17 November 2026 found twelve defects. They were corrected in the working tree afterwards. I checked each one again by reading the code and by running the synthetic study:

| # | Finding of the review | State now |
|---|---|---|
| 1 | Geothermal capacity in the annual dispatch was the demand of one operating point | Fixed, with a side effect that is still open: task 3.1 |
| 2 | Route heat loss was scaled with part load | Fixed. The full loss is charged whenever the plant delivers. |
| 3 | A missing electricity tariff gave an electricity cost of zero | Fixed. It raises `NotEstablished` and names the tariff. |
| 4 | Cash flow invented a replacement year, ignored a non whole asset life, and added a residual value as a cost | Fixed, but the errors surface as the wrong status: task 3.3 |
| 5 | An unknown corridor kind was priced at the link rate | Fixed |
| 6 | A cost item was scoped to a candidate that does not exist | Fixed. `route-alpha-west-capex` applies to `route-alpha-west`. |
| 7 | A candidate without established economics stayed eligible for ranking | Fixed in `PipelineRunner._subject_results` |
| 8 | The GIS importer lost columns whose names contain spaces or brackets | Fixed |
| 9 | An unreadable GIS file raised a raw `ValueError` | Fixed. It raises `LayerImportError`. |
| 10 | A restriction beyond the search distance was ignored even when its setback reached the parcel | Fixed |
| 11 | The reference system of a connection location was not checked | Fixed. Validation reports `wrong_reference_system`. |
| 12 | `connection_max_heat` was checked with the heat at the plant | Fixed. The route heat loss is subtracted. |

Figures of the synthetic study after these fixes. Use them to see whether a later change moved something it should not have moved.

| Quantity | Value |
|---|---|
| Run time of one full study | about 15 s |
| Candidates evaluated | 27 |
| Candidates eligible for ranking | 2 (it was 11 before M4, see task 3.1) |
| Baseline: demand, existing base load, backup | 15,768, 5,054 and 10,714 MWh per year |
| Baseline: system LCOH | 71.73 EUR/MWh |
| `alpha to conn-west by route 1, plant-direct`: capacity | 2.54 MW, limit proven |
| Same candidate: geothermal delivered, curtailed, backup | 10,444, 11,422 and 269 MWh per year |
| Same candidate: LCOH at plant, geothermal delivered, system delivered | 106.56, 116.49 and 87.09 EUR/MWh |
| Same candidate: pump electricity | 1,130 MWh per year (too high, see task 3.2) |

To reproduce them. The run store `.r3chain/` is ignored by git.

```bash
.venv/bin/r3chain run examples/synthetic/scenario.yaml
```

Then use `.venv/bin/r3chain candidates RUN` and `.venv/bin/r3chain economics RUN` with the run identifier that the first command prints.

---

## 2. Rules of this project

These hold for every change. The plan explains each one in sections 3 and 4. The two versions of the plan agree on them.

1. **Unknown is a value.** An input is known with provenance, unknown with an owner, or in conflict. A calculation that needs an unknown raises `NotEstablished` with the field path. It never substitutes a default, and it never picks "the first one" silently.
2. **Thresholds live in policy records** under `policies/`. No limit, rate or tolerance is written into code.
3. **A committed record, schema or policy is never edited in place.** A change needs a new version. A schema change needs a migration (`register_migration`) and a migrated fixture. Files that are not committed yet may still be changed in place. Today that is everything M4 added: the schemas `dispatch_request-0.1.0`, `dispatch_result-0.1.0`, `policy-0.4.0`, `scenario-0.4.0`, `operating_scenario-0.2.0`, `economic_scenario-0.2.0`, the policies `candidate-synthetic` version 2, `dispatch-synthetic` version 1 and `economic-synthetic` version 1, and version 5 of `examples/synthetic/definitions.md`. Regenerate schemas with `r3chain schemas export --force` only for these.
4. **Field names carry their unit**: `_k`, `_w`, `_pa`, `_kg_s`, `_j`, `_currency`, `_currency_per_m`, `_ratio` and so on.
5. **Statuses stay separate.** A physical failure, a numerical non convergence, a missing input, an invalid input and a tool failure are five different outcomes. Do not let one turn into another.
6. **Requirements register.** Every requirement has an entry in `requirements/register.yaml`. A test marked `@pytest.mark.requirement("A0x")` must be listed by that entry. `CURRENT_MILESTONE` in `tests/unit/test_requirements_register.py` rises only when a milestone gate passes.
7. **Synthetic first.** Every feature lands in `examples/synthetic` with a fixture. Results of the synthetic engine are labelled synthetic. Restricted pilot data never enters the repository.
8. **A change of a policy file changes its hash.** Run `r3chain policies` and update the hash where the scenario refers to it. A change of `examples/synthetic/definitions.md` changes the checksum of the dataset `syn-definitions` in `examples/synthetic/scenario.yaml`.
9. **`ruff format` rewrites files.** Read a file again before you edit it after formatting.
10. **Commit only when the user asks.** Do not push. There is no remote.
11. **Do not write planning or analysis documents unless the user asks.** Work from the plan, the specification and the code.

Checks to run before you say something is done. The test suite takes about four minutes.

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

```bash
.venv/bin/mypy
```

```bash
.venv/bin/r3chain schemas check
```

```bash
.venv/bin/python -m pytest -q
```

Report results as they are. If a test fails, say so and show the output.

---

## 3. Task 1: finish M4

Work through these in order. For each one, write the failing test first, then the change, then run the checks.

### 3.1 A capacity case must not decide the technical status (most important)

**What happens now.** `policies/candidate/synthetic.yaml` version 2 has `operating_case_ids: shoulder-season, winter-design`. The winter case was added so that the capacity of the plant is measured at design load. That part works: Alpha at the western connection shows 2.54 MW, limit proven. But `assess_candidates` in `src/r3chain/search/candidates.py` (the `zip` near line 466) uses every coupled case for the technical status. A plant that cannot cover the winter peak on its own is now `fails_evaluated_case`.

**Evidence.** `r3chain candidates` lists 2 eligible candidates of 27. Before M4 it listed 11. The candidate with the lowest system cost, `alpha to conn-west by route 1, plant-direct` at 87.09 EUR/MWh, is excluded from the ranking with `exchanger_cold_end_approach, source:max_drawdown_pa`. Those are the limits that the capacity measurement is meant to find. In the same milestone the annual dispatch covers that peak with the existing boiler and leaves 269 MWh per year to backup. The two parts of M4 contradict each other. No document mentions the change from 11 to 2.

**Change.**

1. In `policies/candidate/synthetic.yaml` (version 2 is not committed, so edit it in place): set `operating_case_ids: shoulder-season` and add `capacity_case_ids: winter-design`. Explain both in the comment. Update the policy hash in `examples/synthetic/scenario.yaml`.
2. In `src/r3chain/search/candidates.py`: read `capacity_case_ids` into the settings (near line 140). `policies.names` goes through `policies.text` and does not accept a missing parameter, so read it only when the policy has it. A policy without `capacity_case_ids`, such as the committed version 1, must keep working and means that no capacity case is coupled. Validate the names like the operating cases (near line 249). Give `Candidate` (near line 72) the fields `capacity_case_ids` and `capacity_coupling_case_ids`, empty by default. Create the coupling cases for them in the enumeration loop (near line 342). `Candidate` has no exported schema in `schemas/`, so no schema version is involved. The candidate identifier is a hash of site, source scenario, connection, route and plant. It must not change.
3. In `CouplingStage` (`src/r3chain/pipeline/evaluation.py`, the loop over `candidate.coupling_case_ids`): evaluate the capacity coupling cases as well and mark each stored result with its role, `operating` or `capacity`.
4. `assess_candidates` keeps using the operating cases only. A deficit or a failed limit in a capacity case never changes `technical_status`, `evaluation_status` or the eligibility.
5. `OperateStage` (`src/r3chain/pipeline/operation_cost.py`) passes the results of both roles to `geothermal_spec_from_coupling`. A capacity case that ends without an operating point, for example `network:positive_absolute_pressure` at the eastern connection, leaves the capacity as `lower_bound` and adds a diagnostic to the candidate's result that names the case and the reason. It is not a failure of the candidate. The network model has one supply point, so it cannot represent winter operation with the backup feeding in elsewhere.
6. Show the outcome of each capacity case in `r3chain candidates` and `r3chain economics`: limit found, demand covered, or not established with its reason.
7. Do not invent a limit for unserved heat. Unserved heat of the year is reported as a KPI. Add a check on it only if the dispatch policy defines a limit.

**Tests.** A deficit in a capacity case leaves the technical status unchanged. A capacity case that the network engine rejects gives `lower_bound` and a diagnostic. A policy without `capacity_case_ids` still enumerates and couples. The capacity test `test_geothermal_capacity_uses_max_coupled_heat_and_marks_limit_proven` still passes.

**Done when.** `r3chain candidates` shows 11 ranked candidates again. The capacity and energy figures of section 1 are unchanged. Replay of the run gives the identical result hash.

If the user prefers that the winter case stays a service case, stop and ask. It is a modelling decision. It should then be written into the plan as a choice for review, together with its effect on the ranking.

### 3.2 Pump and compressor electricity use the ratio of the peak point for every hour

**What happens now.** `geothermal_spec_from_coupling` (`src/r3chain/operation/dispatch.py`) takes the electricity per unit of plant heat from the point with the largest output and applies it to all hours. Pump power grows faster than heat, so part load hours are charged too much.

**Evidence.** Alpha, western connection, route 1, direct plant: 272,594 W of pump power at 2,581,969 W of plant heat in the winter case is a ratio of 0.106. In the shoulder season case it is 81,237 W at 1,304,026 W, a ratio of 0.062. The dispatch uses 0.106 throughout and reports 1,130 MWh per year. Most hours of the year lie at or below the shoulder load.

**Change.** Carry every compliant coupled point into `DispatchGeothermalSpec` as an ordered table of plant heat, pump power and compressor power. In the dispatch, read the power of an interval by linear interpolation over plant heat, with zero power at zero heat as the first point. Above the last point keep the ratio of the last point. Replace the `not_modelled` note by one that states the method and the number of points. The schemas `dispatch_request-0.1.0` and `dispatch_result-0.1.0` are not committed, so regenerate them.

**Tests.** An analytic case with two points and a hand calculated annual electricity. At each coupled point the interpolated power equals the coupled power. With one point the result equals the present behaviour.

**Done when.** The pump electricity of the candidate above falls clearly below 1,130 MWh per year, and the figures of every other quantity in section 1 change only through the electricity cost.

### 3.3 An invalid cost input becomes a tool failure of the whole cost stage

**What happens now.** `evaluate_boundary_cashflows` (`src/r3chain/economics/cashflow.py`) raises `ValueError` for an asset life that is not a whole number of years, a horizon that is not whole, a replacement year outside the horizon and a negative residual value. `CostStage` catches `NotEstablished` only. The runner (`PipelineRunner._execute`) turns any other exception into `StageStatus.TOOL_FAILURE` with a traceback. One wrong cost item then marks the cost of every candidate as a tool failure. That is the wrong status, and `r3chain validate` does not report the input.

**Change.** Check these four conditions when the scenario is validated, in `src/r3chain/domain/validation.py` or in the validators of `src/r3chain/domain/records/scenarios.py`, each with its field path. Keep the checks in the cash flow function as a second line. In `CostStage`, catch the error for the candidate concerned and report it as invalid input for that candidate. Confirm the present behaviour with a test before you change it.

**Tests.** `r3chain validate` reports a life of 12.5 years with its field path. A run with such an item ends with invalid input and not with a tool failure.

### 3.4 A grant is also applied to every automatic replacement

**What happens now.** `cashflow.py` near line 157 applies the grant fraction of a funding rule to each automatic replacement of an asset. Whether a grant covers replacements is a funding rule, and nothing states it.

**Change.** Add a required field to `FundingRule`, for example `applies_to_replacements`, and use it. Set it in the synthetic scenario with its origin, and describe it in `examples/synthetic/definitions.md`. `economic_scenario-0.2.0` is not committed and can be changed in place.

**Tests.** The same asset with the field true and false gives hand calculated funded cash flows.

### 3.5 Silent choices

| Where | What happens now | Change |
|---|---|---|
| `operation_cost.py` near lines 161 and 528 | `scenario.operating_scenarios[0]` and `scenario.economic_scenarios[0]` are used whatever else exists | Select by an identifier in the dispatch and economic policy. With exactly one scenario and no identifier, use it. With several and no identifier, return invalid input and name the choices. |
| `_power_series_w` in `operation/dispatch.py` | A negative demand value is set to zero | Reject the series with its identifier and the first interval concerned. Report it at validation where possible. |
| `CostStage`, near line 782 | `len(cand_entries) == len(reconciliation_failures)` compares a number of candidates with a number of messages | Collect the identifiers of the candidates that failed and compare the counts of candidates |
| `OperateStage.requires`, line 138 | Lists `assess`, which the stage does not read | Check whether it only fixes the order in which subject results are merged. If so, say that in a comment. If not, remove it, so that a ranking policy change does not recompute the year. |
| `compare_to_baseline` in `cashflow.py` | Takes the annual heat of baseline and candidate | Confirm that it refuses or flags a comparison when the two do not deliver the same heat service. Add the check and a test if it is missing. |

The reduced interval mode computes the full year in any case and returns the full result. It is correct and saves nothing. Leave it. It matters in M5, where a measured error from an earlier full run on the same inputs can justify using the reduced run alone.

### 3.6 Documents that no longer match

- `docs/implementation-plan.md`, line 4 of the header still says that M0, M1 and M2 are built and M3 to M9 are not. M0 to M4 are built.
- `docs/implementation-plan.md`, section 11, M4: it says "Gate (R2)". Section 5.2 assigns R2 to M5. M4 has no release gate. The comment in `tests/unit/test_requirements_register.py` near line 144 says the same.
- Section 11, M3 says 11 candidates are ranked. That is true again after task 3.1. Check it against `r3chain candidates`.
- Section 11, M4 must describe the capacity cases of task 3.1, the electricity method of task 3.2 and the funding rule of task 3.4, each under "Modelling choices for review before pilot use".
- `README.md` and `examples/synthetic/definitions.md` follow. A change of the definitions needs the new checksum in `scenario.yaml`.
- The operating case `source-outage` exists in the scenario and no policy uses it. Say so in the M4 text. It gets its use in M5.

---

## 4. Task 2: close M4

1. Run all checks of section 2 and the commands of section 1. Compare with the table of figures and explain every difference.
2. Run `r3chain replay` on the run and confirm that the result is identical.
3. Tell the user what changed, with the figures before and after, and what is not done.
4. Commit only when the user asks. One commit for the milestone, in the style of the earlier ones: `Add operation and economics (milestone M4)`. Leave `docs/implementation-plan-v2.md` and `docs/handoff-next-steps.md` out of it. They belong to the commit of task 3. `main` is two milestones behind. Update it only when the user asks.

---

## 5. Task 3: adopt plan version 2 in the register

Do this after M4 is committed, so that the milestone commit stays clean. It is steps 2 and 3 of the table "Steps that follow from the approval" in `docs/implementation-plan-v2.md`, section 10. Step 1 is done. Step 4, the research track, is the user's own work. Do not start it unless the user asks.

**`requirements/register.yaml`.** Add 24 entries from the two tables of section 10 of version 2. The register writes identifiers with two digits.

- `V01` to `V13`, kind `plan_item`: the build items version 2 adds. Status `planned`, `tests: []`, with `milestone` and `lands_in` from the table.
- `E01` to `E05`, kind `experiment`: the experiments of specification section 21.4. Status `planned`, `tests: []`.
- `D01` to `D06`, kind `open_decision`: the decisions version 2 asks for. Status `open`, with `reviewer` from the column "Who" and `milestone` from the column "Needed by".

Extend the comment at the top of the file so that it names the three new groups and their source. Two examples:

```yaml
  - id: V06
    kind: plan_item
    source: "implementation plan version 2, section 5.3, M6 step 6.1"
    statement: A deterministic claim check accepts or rejects each claim against the stored records
    milestone: M6
    lands_in: [explain]
    status: planned
    tests: []
  - id: D02
    kind: open_decision
    statement: >-
      What pandapipesAI is, its interface and timing, and whether the tools of step 6.1 should
      align with it or be offered to it
    reviewer: Tanja with the pandapipes developers
    milestone: M6
    status: open
```

**`tests/unit/test_requirements_register.py`.** Three changes and the docstring:

- `EXPECTED_IDS` gains `V01` to `V13`, `E01` to `E05` and `D01` to `D06`. The register then has 92 entries.
- The mapping from the first letter to the kind in `test_entries_are_well_formed` gains `"V": "plan_item"`, `"E": "experiment"` and `"D": "open_decision"`.
- `PLANNED_MODULES` gains `explain` and `experiments`, the two directories version 2 adds in section 3.2.

**Done when.** The register tests pass and all checks of section 2 pass. Commit when the user asks, together with the two documents, for example as `Adopt implementation plan version 2`.

---

## 6. Task 4: milestone M5

**Which plan applies.** Version 2, `docs/implementation-plan-v2.md`, approved on 17 November 2026. It keeps the content of M5 and adds four items, V1 to V4 in its section 10: decision drivers and ranking stability, a set of resilience cases, a point estimate mode, and progressive sample allocation. The resilience cases give the operating case `source-outage` its use. Record progress in section 11 of version 2.

**Read before you design.** The M5 text of version 2, section 5.3. The specification, sections 14, 15.2 to 15.4, 20.8 and acceptance test A14. Extract it with a short `zipfile` script, because `pandoc` is not installed. The register entries of the milestone:

```bash
grep -n "milestone: M5" -B5 -A4 requirements/register.yaml
```

**What M5 has to deliver** (version 2, section 5.3):

- Uncertainty model records: variables, distributions or scenario sets, correlation groups, definitions of the success event.
- Joint sampling with common random numbers across candidates, recorded seeds, and an account of every sample.
- Risk outputs of specification section 14.4: probability of meeting the service conditions, quantiles, regret, adoption thresholds, and a case before drilling that includes failure and fallback.
- Eligibility separated from ranking, a ranking policy record, a Pareto view, and an explicit outcome when no candidate is defensibly preferable.
- Evidence report and export.
- V1: decision drivers and ranking stability, read from the paired samples with no extra model runs.
- V2: resilience cases as named outage cases. They report unserved heat, hours with unserved heat and the largest deficit. They never enter a probability.
- V3: a point estimate mode as a named policy. It must stay runnable on the same frozen revision as the uncertainty workflow, because it is the baseline of experiment E4.
- V4: progressive sample allocation as a second named policy, accepted only with its error measured against the full sample set.
- Gate R2: A14 passes, every probability states its denominator, unresolved numerical runs are reported apart from physical failures, the decision driver table is reproduced from the stored samples, and the point estimate mode and the uncertainty workflow run on the same frozen revision.

**How it fits this code base.**

1. **Where it lands.** A new package `src/r3chain/uncertainty/` for the model record, the sampler and the risk outputs. Ranking continues in `src/r3chain/search/`, where the provisional ranking of M3 lives. Reports go to `src/r3chain/reporting/`. The placeholder stage `compare` becomes real.
2. **Records.** The uncertainty model is a domain record with a schema. The scenario gains a list of them, which is scenario 0.5.0 with a migration. Ranking and risk rules are policy records.
3. **One realisation is one frozen revision.** The sampler wraps the whole chain. Build each realisation as a changed copy of the frozen revision and send it through the existing stages. Do not add a second calculation path.
4. **Common random numbers.** Sample index n gives the same draw of every shared variable, such as the geology of a site, prices and customer adoption, for all candidates. The run record already stores a seed.
5. **Every sample ends in exactly one class**: valid, physical failure, numerical failure, or not established. The classes add up to the number of samples. A probability names the class counts it was computed from.
6. **No made up probability.** The success event and the distributions are open decision O05. Without them the result is `not_established`. The synthetic study may define invented ones, and every result from them is labelled synthetic.
7. **Cost of computing.** One study run takes about 15 s for 27 candidates. A hundred samples in sequence take about 25 minutes. Use the dependency hashes so that a sample which changes only prices reruns `operate` and `cost` and nothing before them. Measure before you reduce anything, and accept a reduction only with its measured error.
8. **No forced winner.** Open decision O14 leaves the risk preference open. Show the trade off, and report "no candidate is defensibly preferable" when the difference lies inside the tolerance of the ranking policy or inside the sampling error.

Raise `CURRENT_MILESTONE` to 5 and set the register statuses only when the gate passes.

---

## 7. What to tell the user at the end of each task

- What changed, in which files.
- The figures of section 1 before and after.
- Which checks ran and their real results.
- What is open, and every modelling choice you made that the specification leaves open. Add each one to the plan under "Modelling choices for review before pilot use".
