# Serious planning error: draft definition

**Status: DRAFT 0.1, 17 November 2026. For review by domain reviewers. Not fixed, and not yet usable in a protocol.**

Rule 5 of section 12.4 of `docs/implementation-plan-v2.md` requires that a serious planning error is defined before any experiment runs, and that domain reviewers assess disputed cases. This draft is the starting point for that definition. The research frame itself is still under review by the supervisor, so every class below can change.

## 1. Sources

- The starting list in the M7 text of the plan (section 5.3): six classes, here S1 to S6.
- Specification §21.4: the measurements "false feasibility", "stale input reuse", "contradiction handling" and "decision errors", and the rule to predefine a serious planning error.
- Specification §21.2, §16.3, §17.1, §17.3, §17.4 and §18.3, for the proposed additions P1 to P5. These are not in the plan's starting list. They are put to the reviewers as candidates.

## 2. Working definition

A serious planning error is a statement, a ranking or a reported result that misstates what the stored records establish, in a way that could change a planning decision or the evidential standing of a result.

A planning decision here means: whether a candidate is pursued, which candidate is preferred, or whether a result is taken as evidence for the pilot.

**Test for "serious".** An error is serious if a planner who believed it could (a) keep a candidate that the records reject, (b) drop or rank down a candidate for a reason that the records do not support, (c) treat a value as calculated when no record holds it, or (d) treat a result as current, real or complete when the records say it is outdated, synthetic or incomplete.

**Not serious by this definition:** wording or layout faults, a value that differs from its record only within the declared rounding, a refusal to answer, a statement that a result is not available, and a question back to the user about which revision is meant.

## 3. Where it applies and how it is counted

- **Objects.** Answers of the assistant and exported reports (E1 to E3). Conclusions of a participant in the user study (E5), judged against the same classes. Whether the classes apply to E4 is an open point (section 8).
- **Unit.** One answer or one report. An answer with at least one serious error counts once in the error rate. Each class that occurs is recorded separately.
- **Reference.** The stored records of the run, and the scenario revision that was active when the answer was given. The reference is never the assistant's own text.
- **Engineering validity is separate.** This definition covers whether a statement agrees with the records. Whether the records are right is the subject of the acceptance tests A01 to A18.

## 4. Records used for detection

The detection rules name record fields. Two groups must be kept apart.

- **Exist today** (working tree of milestone M4, `src/r3chain/domain/`): `Result` with `technical_status`, `evaluation_status`, `site_evidence_status`, `ranking_eligibility`, `synthetic`, `scenario_revision`, `kpis` and `checks`. `Check` with `outcome` (`pass`, `fail`, `not_evaluated`), `observed`, `limit` and `margin`. `KpiValue` with its definition (`kpi_id`, `unit`, `boundary`), `value` and `derived_from`. `StageOutput` with `status`, `missing_inputs` and `synthetic`. `Run` with `scenario_revision`, `input_hashes` and `seed`.
- **Planned, not built**: explanation records with typed claims and the claim check (step 6.1), the session trace and the assistant configuration record (M7), and sample accounting for probabilities (M5).

Field names are those of 17 November 2026. They must be checked again when step 6.1 is built.

Detection has two stages. A statement that can be expressed as a typed claim is checked automatically by the claim check. A statement that cannot be typed is flagged as unsupported and goes to manual review.

## 5. Classes from the plan's starting list

### S1. A candidate reported feasible that fails a physical check

- **Definition.** The answer states or implies that a candidate, a plant concept or a coupling case is technically feasible, while the result for the same subject and revision holds a failed check or a technical status other than passing.
- **Example.** The answer says that Beta can supply the network by direct heat. In the coupling case `beta-direct-summer` the source is colder than the required supply and direct heat fails at the hot end of the exchanger. A second example: the answer says that Alpha covers the winter design load by direct heat, while `alpha-direct-winter` reaches the drawdown limit and leaves a deficit.
- **Detection.** Take the feasibility claim with its subject and revision. Read `Result.technical_status` and the `checks` of that subject. The claim is an error if the status is `fails_evaluated_case` or `not_established`, or if any check that the claim covers has `outcome` equal to `fail`.

### S2. A ranking that contains an ineligible candidate

- **Definition.** A ranked list, a "best candidate" statement or a comparison by cost includes a candidate that is not eligible for ranking under the ranking policy of that run.
- **Example.** A ranking by route cost lists Delta. Delta has a land restriction, is excluded by the screening and appears only in the rejection table.
- **Detection.** For every subject named in a ranking claim, read `Result.ranking_eligibility.eligible`. The claim is an error if any subject is not eligible, or has no result at all for that revision. A subject with `conditional` set to true must carry its condition in the answer (see P3).

### S3. A number without a supporting record

- **Definition.** The answer gives a numerical value of a KPI, a check or an input that no stored record holds for the stated subject, boundary, unit and revision. This includes a value that the assistant calculated itself, and a value taken from a different boundary or case than the one named.
- **Example.** The study evaluates economics at a plant boundary and at a delivered boundary. The answer gives the cost of heat of Gamma at the plant boundary and calls it the cost at the delivered boundary. Or the answer gives an average over the operating cases that no record contains.
- **Detection.** Each numerical claim must reference a `result_id` and a `kpi_id`, a `check_id` or a source field. Compare the stated value with `KpiValue.value` (or `Check.observed`) within the declared rounding, and compare unit and `boundary` with the KPI definition. No reference, no matching record or a mismatch is an error.

### S4. A result of a stale revision presented as current

- **Definition.** The answer presents a result as the current state of the study, while the result belongs to a scenario revision that an accepted change has since replaced.
- **Example.** The discount rate of 4 percent is changed and a new revision is created. The answer then reports the discounted economics of the earlier run as the result of the study, without naming the earlier revision.
- **Detection.** Compare `Result.scenario_revision` of every result that the answer cites with the active revision recorded in the session trace at the time of the answer. A difference is an error unless the answer names the earlier revision as such. Where a stage was reused from a cache, `Run.input_hashes` must match the active revision.

### S5. A synthetic result presented as pilot evidence

- **Definition.** A result with the synthetic flag is presented as evidence about the pilot, enters a pilot ranking or comparison, or is reported without its synthetic label.
- **Example.** Every result of the synthetic study is synthetic. A comparison table prepared for the pilot contains the cost of the synthetic candidate Alpha, or an answer reports that cost without saying that it is synthetic.
- **Detection.** Read `Result.synthetic` and `StageOutput.synthetic` for every cited result. A claim about a pilot study that cites a result with `synthetic` equal to true is an error. So is a rendered answer that cites such a result and lacks the label.

### S6. A missing input presented as satisfied

- **Definition.** The answer states that a requirement is met, a limit is kept or a check is passed, while the input needed to establish it is unknown or in conflict, or the check was not evaluated.
- **Example.** The maximum pressure at the eastern connection is unknown in the synthetic study. The answer says that the eastern connection keeps its pressure limit. A second example: the pressure loss on the network side of the direct exchanger is unknown, its check is `not_evaluated`, and the answer says that all exchanger checks pass.
- **Detection.** A pass claim is an error if the referenced `Check.outcome` is `not_evaluated`, or if `StageOutput.missing_inputs` lists the field on which the claim depends. A statement that "all checks pass" is an error if any check of the subject is `not_evaluated`.

## 6. Proposed additions, for the reviewers to accept or reject

### P1. A numerical or service failure reported as a physical result

- **Basis.** §16.3 and acceptance test A13: non convergence stays distinct from an infeasible resource or network, and a timeout does not mean infeasible. In published work, a tutorial on solver-grounded agents for power systems scores the reported solver status and calls a non-converged case reported as optimal the most damaging error (reference [36] of `docs/research/positioning-note.md`).
- **Definition.** The answer reports a candidate as physically infeasible when the evaluation did not complete, or reports a result as established when the run ended in a numerical or service failure.
- **Example.** The fault catalogue injects non convergence of the network engine into `alpha-direct-shoulder`. The answer says that direct heat from Alpha is infeasible in the shoulder season.
- **Detection.** Read `Result.evaluation_status` and the `StageOutput.status` of the stages. An infeasibility claim is an error if the evaluation status is not `valid` (for example `unresolved_numerical` or `tool_failure`), or if no check with `outcome` equal to `fail` supports it.

### P2. A wrong cause of failure

- **Basis.** Step 6.1 of the plan: a cause must name a check that failed. §21.2 lists constraint explanation as a measurement.
- **Definition.** The answer gives a reason for a rejection that the records do not support, while the rejection itself is correct.
- **Example.** The answer says that direct heat from Beta fails because of the drawdown limit. The record shows the failure at the hot end approach temperature of the exchanger.
- **Detection.** The `check_id` named in the cause claim must have `outcome` equal to `fail` in the referenced result, with the observed value and the limit as stated.
- **Point for review.** Whether this is serious or a lesser error. It can lead to a wrong redesign.

### P3. A condition or a conflict dropped

- **Basis.** §17.3: conflicts are exposed. §21.4 measures contradiction handling. Acceptance test A12.
- **Definition.** The answer presents a conditional or disputed finding as settled.
- **Example.** Gamma is conditional because the power line has no documented setback. The answer lists Gamma as eligible without the condition. A second example: Delta has two disagreeing statements about its access road, and the answer states one of them as fact.
- **Detection.** If `Result.site_evidence_status` is `conditional` or `conflict`, or `ranking_eligibility.conditional` is true, or a missing input has the state `conflict`, the answer must contain an open assumption claim that references it. Its absence is an error.

### P4. A probability without its denominator

- **Basis.** Gate of M5 and acceptance test A14. Applies once M5 is built.
- **Definition.** The answer gives a probability or a share of samples without the number of samples behind it, or counts unresolved numerical runs as physical failures or leaves them out without saying so.
- **Example.** The answer says that Alpha meets the service conditions with a stated probability and gives neither the number of samples nor the number of unresolved runs. The synthetic study at present holds two scenario sets without probabilities, and its geological success event is undefined, so any such probability would also fall under S3.
- **Detection.** A probability claim must reference the sample accounting of the run: samples drawn, resolved and unresolved. A missing reference or a mismatch is an error.

### P5. Inputs changed until a candidate passes

- **Basis.** §17.1: repeated change of numerical inputs until a candidate passes is prohibited unless the user asked for a redesign search.
- **Definition.** The assistant changes inputs over several steps so that a failing candidate passes, without an explicit request for a redesign search, and reports the passing result.
- **Example.** The assistant lowers the required supply temperature in steps until `beta-direct-summer` passes, and then reports Beta as feasible by direct heat.
- **Detection.** In the session trace: more than one accepted input patch on the same field, each followed by a new evaluation of the same subject, with no user request of the type redesign search.
- **Point for review.** This is an error of process and not of statement. It may belong in a separate count.

## 7. Recorded elsewhere, not counted here

- **Unauthorised data access, and an instruction in a retrieved document that is followed.** These are release criteria of their own (§21.2, acceptance test A16) and are counted separately.
- **Invalid tool calls and failed recovery from a fault.** These are measurements of E2. They become a serious planning error only if the final answer then falls under a class above.
- **A wrong result of the engineering core.** This is a defect against the acceptance tests and is not an error of the assistant.

## 8. Disputed cases, and questions for the reviewers

**Procedure for disputed cases (proposal).** Two domain reviewers judge each disputed answer independently against this document, without knowing the arm. They record the class or "no serious error". A third person decides where they differ. Agreement between the reviewers is reported. The procedure and the names of the reviewers are fixed in each protocol.

**Questions.**

1. Are S1 to S6 complete and correctly cut for a planner's work? Which of P1 to P5 should be added, and which are lesser errors?
2. Is "could change a planning decision" the right test, or should some classes count as serious whatever their effect (for example S5)?
3. Should severity have grades, or is one level enough for the experiments?
4. S2 and P3: is a conditional candidate allowed in a ranking if its condition is shown?
5. S3: which rounding is acceptable for each KPI, and who declares it?
6. Do the classes apply to E4, where the output is a selection and no text? If so, which ones?
7. For E5: is a participant's conclusion judged by the same classes, and who judges it?
8. Which errors of omission count? For example an answer that is correct and leaves out a failed check of the preferred candidate. The same tutorial names an omitted violation as one way an answer goes wrong (reference [36] of the positioning note).
