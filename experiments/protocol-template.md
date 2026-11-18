# Experiment protocol: template

This template is empty on purpose. Copy it to `experiments/<id>-protocol.md`, fill in every field and commit it before the first run. The fields follow the eight rules of section 12.4 of `docs/implementation-plan-v2.md` (specification §21.4). Text in italics is guidance and is deleted when the protocol is written.

A field that does not apply is filled with "not applicable" and a reason. It is never left blank.

---

## 0. Identification

| Field | Entry |
|---|---|
| Experiment ID (E1 to E5) | |
| Title | |
| Protocol version | |
| Author | |
| Date | |
| Contribution addressed (C1 to C3) | |
| Status (draft, committed, running, closed) | |
| Commit that fixes this protocol | |
| Date and time of the first run | |

*Rule 1: the commit of the protocol must come before the first run.*

## 1. Hypothesis

- **Question:**
- **Hypothesis:**
- **Result that would count against the hypothesis:**
- **Prior work that the hypothesis rests on** (entries of `docs/research/positioning-note.md`):

*State the hypothesis so that the measurements of section 5 can contradict it.*

## 2. Arms

| | Baseline arm | Method arm | Further arm, if any |
|---|---|---|---|
| Name | | | |
| Assistant configuration record and version | | | |
| Context setting | | | |
| Tool access setting | | | |
| Checking setting | | | |
| Model and version | | | |
| Model settings | | | |
| Budget (tool calls, tokens, wall time, retries, Monte Carlo workload) | | | |

- **Held equal across the arms:**
- **The one thing that differs:**

## 3. Suite

- **Suite name and version:**
- **Scope of data (synthetic study or pilot):**
- **Number of tasks in total:**
- **Task families covered:**
- **Faults injected** (entries of the fault catalogue), or "none":

### 3.1 Development part

- **Tasks:**
- **Used for:**

### 3.2 Held out part

- **Tasks:**
- **Separated from the development part by** (site, network configuration or question family):
- **Frozen on (date and commit):**
- **Frozen before any tuning of the assistant (yes or no):**
- **Who may inspect it, and when:**
- **Record of any inspection before the run:**

*Rule 3: development and test tasks are separate. The held out part is not inspected during development.*

## 4. Frozen inputs

| Input | Identifier and version | Hash or commit |
|---|---|---|
| Datasets | | |
| Scenario revisions | | |
| Policies | | |
| Code version | | |
| Engine versions | | |
| Expected outcomes of the reference suite | | |
| Version of this suite | | |

- **Freeze date:**
- **How a change during the experiment is prevented or detected:**

*Rule 2: datasets, model versions and expected outcomes do not change during the experiment.*

## 5. Measurements

| Measurement | Primary or secondary | Definition | Computed from which stored records | Unit |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |

- **Unit of analysis** (task, answer, session, participant):
- **Cost measurements** (tokens, time, engineering runs):

## 6. Serious planning error

- **Version of the definition used** (`experiments/serious-planning-error.md`, version and commit):
- **Classes in scope for this experiment:**
- **Classes out of scope, with reason:**
- **How errors are detected automatically:**
- **How statements that cannot be checked automatically are reviewed:**
- **Domain reviewers** (names and roles):
- **Procedure for disputed cases:**
- **Whether reviewers know the arm:**

*Rule 5: the definition is fixed before the run, and domain reviewers assess disputed cases.*

## 7. Trials

- **Trials per task and arm:**
- **Reason for this number:**
- **Seeds:**
- **Settings recorded for each trial:**
- **Pairing** (which trials are compared with which), or the reason why the design is not paired:
- **Order of execution:**

*Rule 4: trials with a language model are repeated with recorded settings, and comparisons are paired where the design allows it.*

## 8. Analysis

- **Comparison made for each measurement:**
- **Statistic:**
- **Interval, and how it is computed:**
- **How variability across trials is reported:**
- **Treatment of failed, cancelled and incomplete trials:**
- **Treatment of several measurements and several comparisons:**
- **Rule for stopping or extending the experiment:**
- **Analyses that are exploratory and not planned here:**

*Rule 4: variability is reported, and never only the best run. Rule 7: a result that does not favour the method is reported like any other.*

- **What will be reported whatever the outcome:**

## 9. Retention

- **Kept for every arm** (session traces, configurations, answers, check results, tool responses):
- **Kept for failed and abandoned attempts:**
- **Storage location:**
- **Retention period:**
- **Authorised retention policy that applies:**
- **Who has access:**
- **What is deleted, when and on whose authority:**

*Rule 6: all arms and all failed attempts are kept, within the authorised retention policy.*

## 10. Data scope and publication

- **Run on the synthetic study first (yes or no, with date or plan):**
- **Run on pilot data (yes or no):**
- **Decision that permits publication of pilot results** (D5):
- **What will be published, and where:**

*Rule 8: each experiment runs on the synthetic study before it runs on pilot data.*

## 11. Deviations

*Filled in after the commit of the protocol. Every departure from sections 1 to 10 is recorded here with its date and reason. The sections above are not edited after the first run.*

| Date | Section | Deviation | Reason | Effect on the analysis |
|---|---|---|---|---|
| | | | | |

## 12. Sign off

| Role | Name | Date |
|---|---|---|
| Author | | |
| Supervisor | | |
| Domain reviewer | | |
| Domain reviewer | | |

## Checklist before the first run

- [ ] Rule 1. The protocol is committed, with hypothesis, arms, suite, measurements, number of trials and analysis.
- [ ] Rule 2. All inputs of section 4 are frozen and identified.
- [ ] Rule 3. The held out part is separate, frozen and not inspected.
- [ ] Rule 4. Trials are repeated, settings are recorded, the pairing is stated and variability will be reported.
- [ ] Rule 5. The definition of a serious planning error is fixed and the reviewers are named.
- [ ] Rule 6. Retention of all arms and all failed attempts is arranged.
- [ ] Rule 7. Section 8 states what is reported whatever the outcome.
- [ ] Rule 8. The synthetic run comes first, and the publication of pilot results is covered by D5.
