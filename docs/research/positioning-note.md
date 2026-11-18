# Positioning note for research track C

**Status: draft 2, 17 November 2026. Draft 2 adds the full-text reading of the four closest papers (section 3.7). For review by the supervisor (decision D1 of the plan). Nothing in this note is settled, and nothing in it claims novelty.**

This is the literature review and positioning note that section 12.6 of `docs/implementation-plan-v2.md` asks for. It reports what one search found in the six areas of that section and where the gaps appear to be. It comes before the first protocol, because a protocol states a hypothesis.

## 1. Summary

- 65 sources were opened and checked in this session. They are listed in section 7. Sources that could not be opened are listed apart in section 8. The full texts of the four closest papers [36], [37], [39], [40] were read as well (section 3.7).
- **C1 (reliable orchestration).** The general result is already published: typed and validated tools make an agent that drives a simulator more reliable than free generation [6], [40], [36]. Testing agents with injected faults is also established [26], [27]. Keeping numerical failure apart from physical failure is proposed as an evaluation measure in [36]. The apparent gap is narrower: a coupled chain of several engineering models, failure classes carried in one tool result contract, and false feasibility measured under injected engineering faults. None of the four papers read in full injects faults.
- **C2 (reasoning bound to physics and evidence).** The principle that a number is reported only if it comes from a trusted tool and passes a check is already stated and demonstrated for power systems [36], [37]. That a sound external check beats a model checking itself is also shown [19], [32]. The full texts confirm that the published checks compare reported numbers with tool values [36] or scan the answer for numbers [37]. [36] names misreported values, omitted violations and overstated validity as an open problem. The apparent gap is a typed claim schema that goes beyond numbers (status, cause, comparison, open assumption), and the handling of stale revisions. No experiment in the plan isolates the claim check.
- **C3 (decisions under uncertainty that a planner can inspect).** The computation is established practice and method [49], [51], [54]. The apparent gap lies on the human side: whether planners detect assumptions and avoid decision errors. That rests on E5, the experiment with the highest practical risk.
- **Thin areas:** published work of the competence centre on pandapipesAI and agents, evaluated work on language models for heat networks, explanation of uncertainty results to planners, reliance studies with engineering professionals, and stale context in agents. Section 5 gives the detail.

## 2. How the search was done, and its limits

- One session on 17 November 2026. About 25 web searches over the six areas of section 12.6, then each candidate source opened in a browser.
- **Verified** means: the abstract page or the record page of the source was opened in this session, and title, authors, year and identifier were taken from that page.
- **Mostly abstracts were read.** The full texts of the four closest papers [36], [37], [39] and [40] were read on the same day in their arXiv HTML versions, for the parts on design, verification, evaluation, failure analysis and limitations (section 3.7). For [41] one passage of the full text was read to confirm the tool interface. Every other statement is limited to what an abstract or a project page says. Numbers are the authors' own reports.
- Many of the closest sources are preprints or workshop posters from 2025 and 2026, for example [6], [27], [32], [36], [37], [40], [43]. Their review status is unknown or pending.
- This is not a systematic review. No bibliographic database was searched, no reference list was followed, and only English queries were used. A source that the search missed can change any statement below.
- Search queries held no personal detail and no unpublished project detail.

## 3. What the search found

### 3.1 Agents that call simulation and engineering tools, including MCP

- A language model that plans and a deterministic tool that computes is now a common pattern across fields: chemistry with 18 expert tools [1], computational fluid dynamics [2], [3], seismic wave simulation [4], chemical process simulation [5], reservoir simulation [6], power systems [36] to [41], [43] and geothermal field development [45].
- The Model Context Protocol is used as the tool interface in [2], [3], [4], [5], [6], [38], [41] and [43]. The protocol defines tools, resources and prompts, with utilities for progress, cancellation and error reporting [7].
- Most of these papers evaluate by a success rate on a task set or by case studies. Foam-Agent reports 88.2 percent on 110 tasks [2]. The seismology, process simulation and Grid-Orch papers report case studies or workflow demonstrations [4], [5], [38]. The process simulation paper states that calculation errors and oversimplification remain, and that expert oversight is still needed [5].
- A few papers compare designs. The open-darts-mcp paper compares four server designs and finds that typed and immediately validated contracts are the most reliable, and that moving orchestration, validation and recovery into deterministic code lets small local models complete structured subtasks [6]. PowerDAG checks prerequisites before every tool call, blocks invalid actions, and is evaluated on 150 held out queries over six agent systems and ten models [40]. A capacity expansion interface built on MCP is compared with direct access to the code base: both completed six tasks correctly, and direct access used about 81 percent more tokens [43]. A tutorial compares a model alone with its solver grounded counterpart in four case studies [36].
- General benchmarks built on MCP servers exist and show that current models still fail often on realistic multi step tasks [8], [9].

### 3.2 Checking generated statements against structured evidence

- Verifying a sentence against a table is a long standing task. TabFact has 118 thousand statements over Wikipedia tables and includes a method that parses a statement into a program and executes it against the table [10]. FEVEROUS combines sentences and table cells as evidence [11]. SciTab uses scientific tables and finds most models barely above chance [12]. ClaimDB grounds claims in databases with millions of records, finds that reading the evidence breaks down at that scale and that models are weak at admitting that there is no evidence to decide [13].
- Attribution of generated text to sources has its own frameworks: AIS for human judgement of whether a statement is supported by identified sources [14], ALCE for automatic evaluation of citations [15], and FActScore, which breaks a text into atomic facts and reports the share that a knowledge source supports [16]. A gold standard method exists for checking the accuracy of texts generated from data [17].
- A model that criticises its own answer is unreliable, and a sound external verifier helps. One study finds a collapse of performance with self critique and gains with sound external verification [19]. A position paper argues for pairing language models with external model based verifiers [18].
- The same idea has reached energy applications. The smart grid tutorial states a solver grounded principle: a numerical result is reported only when it comes from a trusted tool and passes explicit verification, and it proposes faithfulness and safe failure as an evaluation group [36]. Grid-Mind includes a validation of the response against simulation outputs after it is written [37]. A monitor that grounds its checks in structured tool outputs reduced fabrication after tool faults from 24.7 to 20.3 percent [27].
- For context and provenance: W3C PROV is the standard model for provenance records [20]. A survey classifies conflicts between what a model holds in its parameters and what its context says [21]. MemGPT manages memory tiers for long conversations and is evaluated on document analysis and multi session chat [22].

### 3.3 Benchmarks for agents, and testing with injected faults

- Agents are inconsistent across repeated trials. The tau-bench paper introduces a measure of reliability over k trials and reports under 25 percent for eight consecutive successes in one domain [23].
- Two papers criticise evaluation practice. Agent benchmarks ignore cost, often lack held out sets and are hard to reproduce [24]. Evaluations are experiments and should be analysed as such, with formulas for comparing two models and for planning an evaluation [25].
- Fault injection for tool using agents exists. ToolMisuseBench is offline and deterministic, injects replayable faults under budgets for steps, calls and retries, and reports invalid calls and recovery quality over 6800 tasks [26]. A second study uses nine fault types over 2100 runs and finds correct recovery in 27 to 54 percent of runs, and a confident fabricated answer in 19 to 30 percent. It finds that the type of fault governs the failure more than the model does: partial or misleading data drive fabrication, hard denials drive honest failure [27].
- Instructions hidden in tool data are tested by AgentDojo and InjecAgent [28], [29].
- A taxonomy from more than 1600 annotated traces lists 14 failure modes of multi agent systems in three groups, one of which is task verification [30].
- Model based judges have known weaknesses. A model tends to favour its own output [31]. A controlled study over 64,800 verification calls reports that judges fail together where the task has no executable check, that a larger panel does not help, and that only a signal from the environment changes the picture [32]. An earlier paper found that GPT-4 as an evaluator could not tell clearly wrong completions from the tool supported agent [1].

### 3.4 Language models in the planning of energy systems and heat networks

- **The competence centre.** The public page of Fraunhofer IEG describes a programme: extending pandapipes into pandapipesAI, agent based systems as active assistance in planning, explainable AI, and use cases that include energy infrastructures with deep geothermal energy [33]. The page links a flyer and a poster in German, a book and project pages. It lists no scientific article on pandapipesAI or on agent systems [33]. Two further searches for such an article returned none. The page title says "Energy Applications" and the body says "Energy Infrastructures" [33].
- **pandapipes.** The reference paper describes the open source piping grid tool, its coupling with pandapower and a comparison with STANET [34]. It does not concern language models.
- **Power systems.** This is the most active neighbouring field. A survey covers 2020 to 2025 and names reliability and explainability as open problems [35]. Agent systems include Grid-Mind [37], Grid-Orch [38], PFAgent [39], PowerDAG [40] and X-GridAgent, which builds eight MCP servers on pandapower [41]. PFAgent names convergence validity, numerical consistency and explanation quality as evaluation dimensions [39]. Two papers address optimisation models: a pipeline from natural language to a solver ready formulation with validation and repair [42], and the MCP interface for capacity expansion planning [43].
- **Energy system models.** RE-LLM adds explanations written by a language model to optimisation scenarios for non expert stakeholders [44].
- **Geothermal.** GAIA couples an agent with a digital twin of classical and surrogate physics models for geothermal field development and reports a curated benchmark [45].
- **Heat networks.** The search found a project page for a tool that connects agents through MCP to district heating design, started in February 2026 with a small budget and no reported results [46], and a page for an operational district heating platform with a local language model assistant [47]. It found no evaluated, peer reviewed language model agent for the planning of heat networks.
- The search found no source in which an assistant works over a coupled calculation of geothermal source, plant and heat network.

### 3.5 Decision support under uncertainty, and how results are explained

- In geothermal exploration, uncertainty is often not quantified in practice. A review points to geostatistics and decision analysis, which the petroleum industry already uses to combine several kinds of uncertainty [48].
- Probabilistic techno economic studies of deep geothermal heat exist. A thesis runs 15,000 Monte Carlo iterations for a district heating doublet and reports the probability of a positive net present value and the most sensitive parameters [49]. A German study gives a procedure for net present value and levelised cost with sensitivity analyses, in the context of municipal heat plans [50].
- For energy system optimisation models, a review names four approaches (Monte Carlo analysis, stochastic programming, robust optimisation, modelling to generate alternatives) and warns that neglected uncertainty can mislead decision makers [51]. A best practice paper includes quantifying uncertainty and communicating insights among the steps of a study [52]. A book collects methods for decisions under deep uncertainty [53].
- Ranking under uncertainty has an established method. Stochastic multicriteria acceptability analysis explores the space of preference weights. It reports rank acceptability indices, which describe the range of preferences that give an alternative a certain rank, and confidence factors, which measure whether the data are accurate enough for an informed decision [54].
- For explanation: decision trees can reduce many scenarios to a small set of key decisions [55]. A system built on GPT-4 explains why an optimisation model is infeasible, using the minimal conflicting set of constraints from the solver [56]. RE-LLM writes stakeholder oriented explanations of model results [44]. A review of uncertainty communication finds the literature on its effects on trust and decisions limited [57].

### 3.6 Reliance of users on AI assistance, and how it is measured

- The base concept is appropriate reliance on automation, with trust as what guides reliance when the automation cannot be fully understood [58]. A review of about 60 papers covers how overreliance arises, how it is measured and how it is reduced [59].
- Explanations do not reliably help. In one study they raised acceptance of the AI's recommendation whether it was right or wrong [60]. In another, feature based explanations increased overreliance and example based ones improved outcomes [64]. A third argues that explanations reduce overreliance when they lower the cost of checking the AI [63].
- Interventions that force the user to think reduced overreliance, and users rated them worst [61].
- A two dimensional measure, Appropriateness of Reliance, has been proposed and tested with 200 participants [62].
- For language models, a pre registered study with 404 participants found that first person expressions of uncertainty lowered agreement with the system and raised accuracy [65].
- The tasks named in these abstracts are a maze task [63], two prediction tasks [64], medical questions [65] and three datasets on which an AI helps to solve a task [60]. None of the abstracts [60] to [65] mentions engineering planners or professional users of planning tools.

### 3.7 Full text of the four closest papers

These four were read in full on 17 November 2026 to test the gaps stated for C1 and C2. The summaries are in my own words.

| Aspect | Tutorial [36] | Grid-Mind [37] | PFAgent [39] | PowerDAG [40] |
|---|---|---|---|---|
| How the model reaches the engine | Tool calls around trusted solvers | Registry of eleven operators, and fixed routing of high-risk capacity questions | Generated Python code, checked and then run against the ANDES simulator | 108 tools, with prerequisite checks before each call |
| Check on what is reported | Each reported number against its tool value, with a tolerance per quantity | Pattern scan of the answer for numbers when no analytical tool ran in that turn | During evaluation only: output values against an independent recomputation | Executed workflow against the expert workflow |
| Failure classes | Solver status (optimal, infeasible, non-converged) scored as a confusion matrix | Rejection output names the limiting factor, divergence included | Execution scored as a run without an exception | Not addressed in the parts read |
| Statements other than numbers | Named as an open problem | The model may judge severity in words. Not checked | Not checked | Not checked |
| Revisions and stale state | Not mentioned | Memory entries labelled as earlier runs of the same session | Whether earlier edits carry forward across turns is scored | "Stale" means a tool state whose prerequisites have not run |
| Injected faults | No | No | No | No |
| Repeated trials | Defined as a metric (pass^k) | Temperature 0, one run | Not mentioned | One run per query |
| Held out tasks | Held out turbines in one case study | Two disjoint suites. A locked test protocol is named as future work | Not mentioned. The same suite is run again after self-evolution | 150 of 200 queries, split within task families |
| Uncertainty | Recommended for predictions that have no solver | Not addressed | Not addressed | Not addressed |

**Tutorial [36].**

- A verification function returns pass or fail and one residual per condition. The conditions are of two kinds: constraint satisfaction, and faithfulness of each reported number to its tool value.
- The evaluation scores whether the solver status is reported correctly. It calls a non-converged case reported as optimal the most damaging error and says it should be tracked separately.
- A safe-failure rate counts how often the system declines to report numbers after a failed verification and states the failure and its cause. The cause itself is not checked against a record.
- The authors name an open problem: an answer that combines numbers, explanations, diagnostics and recommendations can misreport a value, leave out a violation or overstate validity. As future work they propose reusable verification libraries behind a typed interface.
- Stated limits: off-the-shelf models, four tasks, and each system compared only with its own model-only counterpart.
- Its power flow case study is also called PFAgent and uses pandapower. It is a different system from [39], which runs ANDES.

**Grid-Mind [37].**

- A violation inspector records for each violation the element, the type, the observed value, the limit and the margin. Approval and rejection come only from the solver and the inspector. The model may add qualitative judgement, for example on how severe a margin is.
- The capacity search returns a structured explanation of a rejection: the limiting factor (divergence included), the failing stage and the violations.
- The third of three defences against invented numbers is a pattern scan applied per turn. The authors say it does not trace each number to its source.
- An ablation on small prompt sets found no invented numbers with or without the second and third defences. The authors say this gives no worst-case guarantee.
- Observed failures include running a study with assumed default values when parameters were missing, and inventing details in a follow-up instead of reading stored results. The memory was labelled as session-local after the model had presented earlier runs as historical studies.
- Stated limits: no blinded third-party benchmark, no locked test protocol, no check against utility-grade reference studies.

**PFAgent [39].**

- The model writes code, which passes static checks before it runs. The output is structured, and a session log links each statement to a script and a simulator call.
- A six-part score includes numerical agreement with an independent recomputation, and continuity: whether edits made in earlier turns are still present in later ones.
- "Convergence validity" in the abstract is scored in the paper as code that runs without an exception. The authors note that a failed run does not always mean wrong reasoning.
- The suite was generated for demonstration. Self-evolution is measured by running the same suite again.

**PowerDAG [40].**

- The problem addressed is silent failure: a tool that runs on a state its prerequisites have not prepared. A supervisor blocks such calls. Its rules are mined from expert traces and confirmed by a domain expert.
- The held out split is within task families: same families, but different feeders, parameters and limits. Each query is run once. Proprietary models run at their default decoding settings.
- The paper cites further systems that check preconditions of tool calls. They were not opened (section 8).

**What the full texts change.**

1. **C1.** Keeping numerical failure apart from physical failure is already an evaluation measure in [36], and [37] puts the cause of a rejection into the tool output. What is left for R3CHAIN is five failure classes in one result contract across a chain of engines, and faults injected at the ports. None of the four injects faults.
2. **C2.** The gap for typed claims beyond numbers holds against these four papers. But [36] names it as an open problem and as future work, so others may be working on it now.
3. **E1.** The continuity score of [39] is the nearest published measure to correction accuracy. None of the four tracks scenario revisions.
4. **Evaluation practice.** None of the four reports repeated trials with their variability, and only [40] has a held out set fixed in advance. Rules 3 and 4 of section 12.4 of the plan therefore matter. They are a point of method and not a contribution.
5. **Serious planning error.** The most damaging error of [36] supports class P1 of the draft definition. The omitted violation of [36] supports its question on omissions.

## 4. Position of each contribution

| Contribution | Already covered by prior work | Apparent gap | Experiment |
|---|---|---|---|
| C1 | Typed, validated tools beat free generation [6], [40], [36]. Fault injection for agents [26], [27]. Solver status scored apart from feasibility [36]. | Coupled chain of models, failure classes in one result contract, false feasibility under injected engineering faults | E2 |
| C2 | Report a number only from a trusted tool after a check [36], [37]. External check beats self check [19], [32]. | Typed claims beyond numbers, stale revisions, the residual left for a model based checker | E1, E3 |
| C3 | Monte Carlo techno economics [49], rank acceptability [54], uncertainty methods [51], [53] | Planner side: detection of assumptions and decision errors with professional users | E4, E5 |

### 4.1 C1. Reliable orchestration of several models

**What is known.** Agents that drive simulators through tools exist in many fields, several with MCP (section 3.1). Designs with typed and immediately validated contracts are the most reliable in a direct comparison [6]. Checking prerequisites before a tool call improves success by 6 to 50 points over baselines on held out queries [40]. Faults can be injected in a replayable way [26], and the type of fault governs how an agent fails [27]. Reliability must be measured over repeated trials [23], with held out tasks and cost reported [24].

**Closest prior work.** The open-darts-mcp study, for typed MCP tools on a subsurface simulator with measured failure modes, retries and rollback [6]. PowerDAG, for supervision before execution and a held out evaluation [40]. The fault studies [26] and [27]. For a direct comparison of a structured interface with unstructured access in energy planning, [43] and [36].

**Where prior work already covers C1.** The headline of E2, typed tool orchestration against direct prompting, would most likely repeat a known result in a new field [6], [36], [40]. Fault injection as a method is not new [26], [27]. Replay from stored traces is not new either: ToolMisuseBench is built on it [26]. Keeping numerical failure apart from physical failure is not new as an idea: [36] scores whether the solver status is reported correctly and calls a non-converged case reported as optimal the most damaging error, and [37] names divergence as a limiting factor in its rejection output.

**What R3CHAIN would add, if the experiment shows it.**

1. A chain of several coupled models (source, plant, network, economics). Each system opened here wraps one engine with its pre and post processing, or the tools of one field [2] to [6], [38], [41]. The search found no evaluated agent over a coupled geothermal and heat network calculation.
2. Five failure classes carried by one tool result contract across a chain of engines, above all numerical non convergence against physical infeasibility, with "false feasibility" as the outcome measure under injected engineering faults. [36] scores the solver status at the evaluation level, and [37] reports the limiting factor of a rejection. In the full text of [39], convergence validity is scored as code that runs without an exception. None of the four papers read in full injects faults (section 3.7). [27] shows that the kind of fault matters. The generic fault benchmarks inject interface faults such as timeouts, schema drift and denied access into file, database and web environments [26], [27]. The search found no experiment that injects engineering faults at the engine and measures whether the agent then reports a failed case as feasible.

**Experiment.** E2, with the fault catalogue. Given [6], [40] and [43], the baseline "direct prompting without typed tools" is weak. See question 2 in section 6.

### 4.2 C2. Reasoning bound to physics and evidence

**What is known.** Checking text against structured evidence is a mature task on which models still score poorly [10] to [13]. At the scale of a database it has to be done by executing programs and not by reading [13]. Breaking a text into atomic claims and checking each is an established evaluation method [16]. Sound external verification outperforms self critique [19]. In power systems, the rule that numbers come from tools and pass a gate is stated as a design principle and compared against a model alone [36], and a validation after the response is part of one published agent [37]. Model based judges share errors when no executable check exists [32] and favour their own output [31].

**Closest prior work.** The solver grounded principle and its verification gate [36]. The grounding validation of Grid-Mind [37]. The verification first monitor of [27]. For the method of typed claims and executable checks, FActScore [16] and ClaimDB [13]. For the architecture argument, [18] and [19]. For E3, the verifier study [32].

**Where prior work already covers C2.** That numbers must come from tools and be checked before they are shown is published for a neighbouring field [36], [37]. That a deterministic check is more dependable than a model based one is published [19], [32]. The likely direction of E3, a model based checker that adds little and fails in a correlated way, has direct precedent [31], [32].

**What R3CHAIN would add, if the experiment shows it.**

1. Claim types beyond numbers: a status, a cause of failure with the failed check, the observed value and the limit, a comparison that depends on eligibility, and an open assumption, each with a deterministic rule of acceptance. In the abstracts opened, the grounding checks concern numerical values and tool outputs [27], [36], [37]. The search found no published typed claim schema of this kind for engineering planning results. The full texts of the four closest papers confirm this for them: [36] checks each reported number against its tool value, [37] scans the answer for numbers per turn and its authors say it does not trace each number, [39] compares output values with a recomputation during evaluation only, and [40] checks the prerequisites of tool calls and not statements (section 3.7). [36] names misreported values, omitted violations and overstated validity as an open problem and proposes typed verification libraries as future work, so this gap may close soon. For all other papers the statement rests on abstracts.
2. Stale revisions and contradictions (E1). The survey of knowledge conflicts concerns a model's parameters against its context [21], and MemGPT is evaluated on chat and document tasks [22]. The search found no study that measures whether a planning agent reuses a result of an outdated scenario revision. In the full texts, [39] scores whether earlier edits carry forward across turns, which is close to correction accuracy. [37] labels memory entries as earlier runs of the same session after its model presented them as historical studies. [40] uses "stale" for a tool state whose prerequisites have not run. None of them tracks scenario revisions. This area was thin in the search and may exist under other names.
3. E3 in a setting where an executable check exists for most claims. According to [32], judges fail together where no executable check exists, and the signal from the environment is the binding constraint. The open question for R3CHAIN is then what remains for a model based checker once the deterministic claim check has run.
4. Fixed wording as the baseline for the assistant's wording. This is close to classical evaluation of text generated from data [17]. That literature was only touched by this search.

**Experiment.** E1 for context and E3 for checking. Neither isolates the deterministic claim check: it is present in both arms of E3, and E1 varies the context. See question 3 in section 6.

### 4.3 C3. Decisions under uncertainty that a planner can inspect

**What is known.** Monte Carlo techno economic analysis of geothermal heat projects is established [49]. Uncertainty methods for energy models are reviewed [51], [53], and guidance asks modellers to quantify and communicate uncertainty [52]. An existing method reports how widely each alternative takes a given rank, and a confidence factor for whether the data are accurate enough to decide [54]. This is close to the plan's share of samples in which a candidate ranks first, although [54] explores preference weights and the plan samples uncertain inputs. Explanations of model results by a language model exist [44], [56]. Explanations of an AI can raise acceptance regardless of correctness [60], and evidence on how communicated uncertainty affects decisions is limited [57].

**Closest prior work.** For the computation, probabilistic techno economics [49] and rank acceptability analysis [54]. For explanation to non experts, RE-LLM [44] and OptiChat [56]. For the user study, the measure of [62] and the designs of [60], [61] and [63].

**Where prior work already covers C3.** The uncertainty computation itself: sampling, probabilities of meeting a condition, sensitivity and ranking stability are established [49], [51], [54]. E4 as "point estimate against uncertainty" would mainly show a known advantage. Common random numbers and progressive allocation of samples are standard simulation techniques. Their literature (ranking and selection) was not searched.

**What R3CHAIN would add, if the experiment shows it.**

1. Site, route, connection and plant compared as one coupled calculation under uncertainty, with eligibility kept apart from ranking. The geothermal studies opened treat one project at a time [49], [50]. This part of the search was shallow.
2. A recorded outcome "no candidate is defensibly preferable", and decision drivers read from paired samples, bound to explanation records that an assistant may not overstate. The confidence factors of [54] are close in intent.
3. Measured reliance and decision errors of planners who use an assistant, against forms on the same backend (E5). The reliance literature supplies measures [62] and warnings [60], [61]. None of the abstracts opened mentions professional planners [60] to [65]. The search found no controlled reliance study with energy infrastructure planners.

**Experiment.** E4 for the computation and E5 for the planner. The words "that a planner can inspect" rest on E5, which needs participants and comes last. See question 5 in section 6.

## 5. Areas that turned out thin

1. **Published work of the competence centre on pandapipesAI and agents.** Only the programme page was found [33]. The flyer and the poster linked on it could not be opened (section 8).
2. **Language models for heat networks.** Two project or product pages [46], [47] and no evaluated study.
3. **Explanation of uncertainty results to planners.** Methods exist [44], [55], [56]. Studies of their effect on professional decisions were not found, and [57] calls that literature limited.
4. **Reliance of engineering professionals.** The measures come from studies whose abstracts name no professional planners [60] to [65].
5. **Stale context and revisions in agents (E1).** Only general sources [21], [22], and three near neighbours in the full texts of [37], [39] and [40].
6. **Not searched at all:** ranking and selection with common random numbers, classical generation of text from data beyond [17], German language sources on municipal heat planning tools, and work on the audit and replay of agent traces beyond [26].

## 6. Open questions for the supervisor (D1)

1. **Wording of the research question.** A "solver grounded" principle for agents is already named for power systems [36]. Does the broader wording of section 12.2 ("trustworthy, physics-grounded agentic AI") still set the work apart from it? The narrower wording of §21.3 stresses uncertain, incomplete and changing evidence, which is where the apparent gaps lie (stale revisions, missing inputs, failure classes).
2. **Is C1 a contribution or a replication in a new field?** Options: state C1 as failure classes and false feasibility under injected engineering faults, and add a third arm to E2 (tools without the result contract, or code written against the engine interface), so that E2 measures what the contract adds and not what tools add.
3. **Should an experiment isolate the deterministic claim check?** It is the part of C2 with the clearest apparent gap, and no arm of E1 to E3 switches it. The silent mode of the baseline configurations gives the rate of rejected claims. It does not give the effect of withholding an answer.
4. **Should E3 be stated as a question about the residual?** Given [32], the informative question may be what a model based checker still catches after the deterministic check, and at what cost.
5. **What is C3's contribution: the workflow or the finding about planners?** If it is the second, it depends on E5. What number of participants is realistic, and is a study with planted errors and expert reviewers acceptable in place of a comparison between groups?
6. **Relation to pandapipesAI (D2).** No public technical description was found. Does unpublished work of the centre change this positioning, and which interface would the tools of step 6.1 have to meet?
7. **Scope of the full review.** Narrative or systematic, with which databases, and who reads the full texts? Many of the closest sources are preprints of 2025 and 2026 [6], [27], [32], [36], [40]. The positioning should be checked again before each protocol is committed.
8. **Community and venue.** The closest work sits in power system journals and AI workshops [36], [38], [43]. Should the first paper address energy informatics, AI agents or human computer interaction?
9. **Generality.** The evidence would come from one synthetic study and one pilot. Which claim about other networks or other engineering fields is intended?
10. **Timing.** The tutorial [36] names typed verification of statements as future work. Should the claim check of step 6.1 and its evaluation on the synthetic study be written up early, before the assistant experiments of M7?

## 7. References (verified in this session)

Each entry was opened on 17 November 2026. Where a repository copy was read, it is named.

**Agents that call simulation and engineering tools**

1. Bran, A. M., Cox, S., Schilter, O., Baldassari, C., White, A. D., Schwaller, P. (2023). ChemCrow: Augmenting large-language models with chemistry tools. arXiv:2304.05376. https://arxiv.org/abs/2304.05376
2. Yue, L., Somasekharan, N., Zhang, T., Cao, Y., Pan, S. (2025). Foam-Agent 2.0: An End-to-End Composable Multi-Agent Framework for Automating CFD Simulation in OpenFOAM. arXiv:2509.18178. https://arxiv.org/abs/2509.18178
3. Dong, Z., Du, S., Lu, Z., Yang, Y. (2025). CFD-copilot: leveraging domain-adapted large language model and model context protocol to enhance simulation automation. arXiv:2512.07917. https://arxiv.org/abs/2512.07917
4. Ren, Y., Yu, S., Chen, K., Ma, J. (2025). Seismology modeling agent: A smart assistant for geophysical researchers. arXiv:2512.14429. https://arxiv.org/abs/2512.14429
5. Liang, J., Groll, N., Sin, G. (2026). Large Language Model Agent for User-friendly Chemical Process Simulations. arXiv:2601.11650. Related DOI: 10.1016/j.dche.2026.100312. https://arxiv.org/abs/2601.11650
6. Novikov, A., Serrao Seabra, G., Agrawal, N., Garcia Alvarez, M., Voskov, D. (2026). Design and evaluation of MCP-server for agentic reservoir simulation workflows. ECMOR 2026, European Association of Geoscientists and Engineers. https://doi.org/10.3997/2214-4609.202637158
7. Model Context Protocol (2025). Specification, version 2025-06-18. https://modelcontextprotocol.io/specification/2025-06-18
8. Wang, Z., Chang, Q., Patel, H., Biju, S., Wu, C.-E., Liu, Q., Ding, A., Rezazadeh, A., Shah, A., Bao, Y., Siow, E. (2025). MCP-Bench: Benchmarking Tool-Using LLM Agents with Complex Real-World Tasks via MCP Servers. arXiv:2508.20453. https://arxiv.org/abs/2508.20453
9. Luo, Z., Shen, Z., Yang, W., Zhao, Z., Jwalapuram, P., Saha, A., Sahoo, D., Savarese, S., Xiong, C., Li, J. (2025). MCP-Universe: Benchmarking Large Language Models with Real-World Model Context Protocol Servers. arXiv:2508.14704. https://arxiv.org/abs/2508.14704

**Checking statements against structured evidence**

10. Chen, W., Wang, H., Chen, J., Zhang, Y., Wang, H., Li, S., Zhou, X., Wang, W. Y. (2020). TabFact: A Large-scale Dataset for Table-based Fact Verification. ICLR 2020. arXiv:1909.02164. https://arxiv.org/abs/1909.02164
11. Aly, R., Guo, Z., Schlichtkrull, M., Thorne, J., Vlachos, A., Christodoulopoulos, C., Cocarascu, O., Mittal, A. (2021). FEVEROUS: Fact Extraction and VERification Over Unstructured and Structured information. NeurIPS 2021 Datasets and Benchmarks. arXiv:2106.05707. https://arxiv.org/abs/2106.05707
12. Lu, X., Pan, L., Liu, Q., Nakov, P., Kan, M.-Y. (2023). SCITAB: A Challenging Benchmark for Compositional Reasoning and Claim Verification on Scientific Tables. EMNLP 2023. arXiv:2305.13186. https://arxiv.org/abs/2305.13186
13. Theologitis, M., Dammu, P. P. S., Shah, C., Suciu, D. (2026). ClaimDB: A Fact Verification Benchmark over Large Structured Data. ACL 2026. arXiv:2601.14698. https://arxiv.org/abs/2601.14698
14. Rashkin, H., Nikolaev, V., Lamm, M., Aroyo, L., Collins, M., Das, D., Petrov, S., Tomar, G. S., Turc, I., Reitter, D. (2021). Measuring Attribution in Natural Language Generation Models. arXiv:2112.12870. https://arxiv.org/abs/2112.12870
15. Gao, T., Yen, H., Yu, J., Chen, D. (2023). Enabling Large Language Models to Generate Text with Citations. EMNLP 2023. arXiv:2305.14627. https://arxiv.org/abs/2305.14627
16. Min, S., Krishna, K., Lyu, X., Lewis, M., Yih, W., Koh, P. W., Iyyer, M., Zettlemoyer, L., Hajishirzi, H. (2023). FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation. EMNLP 2023. arXiv:2305.14251. https://arxiv.org/abs/2305.14251
17. Thomson, C., Reiter, E. (2020). A Gold Standard Methodology for Evaluating Accuracy in Data-To-Text Systems. INLG 2020. arXiv:2011.03992. https://arxiv.org/abs/2011.03992
18. Kambhampati, S., Valmeekam, K., Guan, L., Verma, M., Stechly, K., Bhambri, S., Saldyt, L., Murthy, A. (2024). LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks. arXiv:2402.01817. https://arxiv.org/abs/2402.01817
19. Stechly, K., Valmeekam, K., Kambhampati, S. (2024). On the Self-Verification Limitations of Large Language Models on Reasoning and Planning Tasks. arXiv:2402.08115. https://arxiv.org/abs/2402.08115
20. Groth, P., Moreau, L. (eds.) (2013). PROV-Overview: An Overview of the PROV Family of Documents. W3C Working Group Note, 30 April 2013. https://www.w3.org/TR/prov-overview/
21. Xu, R., Qi, Z., Guo, Z., Wang, C., Wang, H., Zhang, Y., Xu, W. (2024). Knowledge Conflicts for LLMs: A Survey. arXiv:2403.08319. https://arxiv.org/abs/2403.08319
22. Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., Gonzalez, J. E. (2023). MemGPT: Towards LLMs as Operating Systems. arXiv:2310.08560. https://arxiv.org/abs/2310.08560

**Benchmarks for agents and injected faults**

23. Yao, S., Shinn, N., Razavi, P., Narasimhan, K. (2024). τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains. arXiv:2406.12045. https://arxiv.org/abs/2406.12045
24. Kapoor, S., Stroebl, B., Siegel, Z. S., Nadgir, N., Narayanan, A. (2024). AI Agents That Matter. arXiv:2407.01502. https://arxiv.org/abs/2407.01502
25. Miller, E. (2024). Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations. arXiv:2411.00640. https://arxiv.org/abs/2411.00640
26. Sigdel, A., Baral, R. (2026). ToolMisuseBench: An Offline Deterministic Benchmark for Tool Misuse and Recovery in Agentic Systems. arXiv:2604.01508. https://arxiv.org/abs/2604.01508
27. Kaynar, G., Yan, B., Kingsford, C. (2026). When Tools Fail, Agents Fabricate: Silent Failure and Verification-First Oversight in Tool-Using LLM Agents. Poster, NeurIPS 2026 workshop "Who Verifies the Agents? Toward Reliable Agent Development". https://neurips.cc/virtual/2026/173038
28. Debenedetti, E., Zhang, J., Balunović, M., Beurer-Kellner, L., Fischer, M., Tramèr, F. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. arXiv:2406.13352. https://arxiv.org/abs/2406.13352
29. Zhan, Q., Liang, Z., Ying, Z., Kang, D. (2024). InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents. Findings of ACL 2024. arXiv:2403.02691. https://arxiv.org/abs/2403.02691
30. Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., Chopra, B., Tiwari, R., Keutzer, K., Parameswaran, A., Klein, D., Ramchandran, K., Zaharia, M., Gonzalez, J. E., Stoica, I. (2025). Why Do Multi-Agent LLM Systems Fail? arXiv:2503.13657. https://arxiv.org/abs/2503.13657
31. Panickssery, A., Bowman, S. R., Feng, S. (2024). LLM Evaluators Recognize and Favor Their Own Generations. arXiv:2404.13076. https://arxiv.org/abs/2404.13076
32. Kozman, J., Waddepalli, A., Parajuli, T. (2026). Neither Blinding Nor a Jury, Neither Capability Nor Strategy: What Brings a Verifier to Reliability. Poster, NeurIPS 2026 workshop "Can We Trust AI Evaluation?". https://neurips.cc/virtual/2026/loc/sydney/178251

**Language models in energy system and heat network planning**

33. Fraunhofer IEG (2026). User-Centered AI Methods for Energy Applications. Web page of the competence centre. https://www.ieg.fraunhofer.de/en/research-topics/energy-system-analysis-infrastructure-and-district-planning/user-centered-ai-methods-for-energy-applications.html
34. Lohmeier, D., Cronbach, D., Drauz, S. R., Braun, M., Kneiske, T. M. (2020). Pandapipes: An Open-Source Piping Grid Calculation Package for Multi-Energy Grid Simulations. Sustainability 12(23), 9899. https://doi.org/10.3390/su12239899
35. Sarwar, M., Rizwan, M., Aziz, M., Sudais, A. R. (2025). Large Language Models for Power System Applications: A Comprehensive Literature Survey. arXiv:2512.13004. https://arxiv.org/abs/2512.13004
36. Rojas, D., Albassam, A., Leung, A. G., Ngo, J., Luo, R., Quawas, P. R., Kim, J., Liang, K., Nanavati, M., Mai, J., Tsai, M.-C., Tsai, Y.-T., Chen, Y., Shi, Y. (2026). LLMs and Agentic AI Systems for Smart Grids: A Tutorial on Architectures and Applications. arXiv:2607.18147. https://arxiv.org/abs/2607.18147
37. Shamseldein, M. (2026). Grid-Mind: An LLM-Orchestrated Multi-Fidelity Agent for Automated Connection Impact Assessment. arXiv:2602.20683. https://arxiv.org/abs/2602.20683
38. Liu, B., Dong, J., Lian, J. (2026). Grid-Orch: An LLM-Powered Orchestrator for Distribution Grid Simulation and Analytics. IEEE Open Access Journal of Power and Energy 13, 727-738. DOI: 10.1109/OAJPE.2026.3721083. Read as arXiv:2605.12728. https://arxiv.org/abs/2605.12728
39. She, B., Chen, B., Guo, L., Li, F. (2026). PFAgent: A Tractable and Self-Evolving Power-Flow Agent for Interactive Grid Analysis. arXiv:2604.10846. https://arxiv.org/abs/2604.10846
40. Badmus, E. O., Pandey, A. (2026). PowerDAG: Supervisory Agentic AI System for Automating Distribution Grid Analysis. arXiv:2603.17418. https://arxiv.org/abs/2603.17418
41. Wen, Y., Chen, X. (2025). X-GridAgent: An LLM-Powered Agentic AI System for Assisting Power Grid Analysis. arXiv:2512.20789. https://arxiv.org/abs/2512.20789
42. Hu, Y., Zhao, T., Yue, M. (2025). From Natural Language to Solver-Ready Power System Optimization: An LLM-Assisted, Validation-in-the-Loop Framework. arXiv:2508.08147. https://arxiv.org/abs/2508.08147
43. Bugosen, S., Valencia Zuluaga, T., Watson, J.-P., Gounaris, C. E., Laird, C. (2026). Integrating Large Language Models with Optimization for Interactive Power Systems Planning. Poster, NeurIPS 2026 workshop MLxOR. https://neurips.cc/virtual/2026/171750
44. Forootani, A., Sadr, M., Esmaeili Aliabadi, D., Thraen, D. (2025). RE-LLM: Integrating Large Language Models into Renewable Energy Systems. arXiv:2512.01392. https://arxiv.org/abs/2512.01392
45. Harsuko, R., Bi, Z., Chen, G., Nakata, N. (2025). Advancing Subsurface Discovery and Geothermal Monitoring with an Agentic Artificial Intelligence Framework. arXiv:2511.03852. https://arxiv.org/abs/2511.03852
46. ZHAW Institute of Computational Physics with PLANETO SA (2026). Smart Heat, Smarter Tools: Unlocking District Energy Design with AI Agents (KI-Planeto). Project page, project started February 2026. https://www.zhaw.ch/en/research/project/79842
47. SDU Center for Energy Informatics (2026). DistrictHeat+. Software page. https://www.sdu.dk/en/forskning/centreforenergyinformatics/software-solutions/districtheat

**Decision support under uncertainty and its explanation**

48. Witter, J. B., Trainor-Guitton, W. J., Siler, D. L. (2019). Uncertainty and risk evaluation during the exploration stage of geothermal development: A review. Geothermics 78, 233-242. https://doi.org/10.1016/j.geothermics.2018.12.011 Read as the USGS record: https://pubs.usgs.gov/publication/70201781
49. van Dongen, B. (2019). The economic potential of deep, direct use geothermal systems in the Netherlands. Master thesis, TU Delft. https://resolver.tudelft.nl/uuid:4b1f0f67-f990-4e7e-a4be-104bdf07ebdc
50. Schlagermann, P., Reinicke, K. M. (2023). Modeling and economic evaluation of deep geothermal heat supply systems using the example of the Wealden near Hannover, Germany. Geothermal Energy 11, 33. https://doi.org/10.1186/s40517-023-00276-0
51. Yue, X., Pye, S., DeCarolis, J., Li, F. G. N., Rogan, F., Ó Gallachóir, B. (2018). A review of approaches to uncertainty assessment in energy system optimization models. Energy Strategy Reviews 21, 204-217. https://doi.org/10.1016/j.esr.2018.06.003 Read as the UCL Discovery record: https://discovery.ucl.ac.uk/id/eprint/10054384/
52. DeCarolis, J., Daly, H., Dodds, P., Keppo, I., Li, F., McDowall, W., Pye, S., and others including Zeyringer, M. (2017). Formalizing best practice for energy system optimization modelling. Applied Energy 194, 184-198. https://doi.org/10.1016/j.apenergy.2017.03.001 Read as the UCL Discovery record, which shortens the author list: https://discovery.ucl.ac.uk/id/eprint/1546818/
53. Marchau, V. A. W. J., Walker, W. E., and others including Popper, S. W. (eds.) (2019). Decision Making under Deep Uncertainty: From Theory to Practice. Springer, open access. The publisher page shortens the editor list. https://doi.org/10.1007/978-3-030-05252-2
54. Tervonen, T., Lahdelma, R. (2007). Implementing stochastic multicriteria acceptability analysis. European Journal of Operational Research 178(2), 500-513. https://doi.org/10.1016/j.ejor.2005.12.037 Read as the repository record: https://hdl.handle.net/10316/5491
55. Baader, F. J., Moret, S., Wiesemann, W., Staffell, I., Bardow, A. (2023). Streamlining Energy Transition Scenarios to Key Policy Decisions. arXiv:2311.06625. https://arxiv.org/abs/2311.06625
56. Chen, H., Constante-Flores, G. E., Li, C. (2023). Diagnosing Infeasible Optimization Problems Using Large Language Models. arXiv:2308.12923. https://arxiv.org/abs/2308.12923
57. van der Bles, A. M., van der Linden, S., Freeman, A. L. J., Mitchell, J., Galvao, A. B., Zaval, L., Spiegelhalter, D. J. (2019). Communicating uncertainty about facts, numbers and science. Royal Society Open Science 6(5), 181870. https://doi.org/10.1098/rsos.181870 Read as the PMC copy: https://pmc.ncbi.nlm.nih.gov/articles/PMC6549952/

**Reliance on AI assistance**

58. Lee, J. D., See, K. A. (2004). Trust in Automation: Designing for Appropriate Reliance. Human Factors 46(1), 50-80. https://doi.org/10.1518/hfes.46.1.50_30392
59. Passi, S., Vorvoreanu, M. (2022). Overreliance on AI: Literature Review. Microsoft Technical Report MSR-TR-2022-12. https://www.microsoft.com/en-us/research/publication/overreliance-on-ai-literature-review/
60. Bansal, G., Wu, T., Zhou, J., Fok, R., Nushi, B., Kamar, E., Ribeiro, M. T., Weld, D. S. (2021). Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance. CHI 2021. arXiv:2006.14779. https://arxiv.org/abs/2006.14779
61. Buçinca, Z., Malaya, M. B., Gajos, K. Z. (2021). To Trust or to Think: Cognitive Forcing Functions Can Reduce Overreliance on AI in AI-assisted Decision-making. DOI: 10.1145/3449287. arXiv:2102.09692. https://arxiv.org/abs/2102.09692
62. Schemmer, M., Kühl, N., Benz, C., Bartos, A., Satzger, G. (2023). Appropriate Reliance on AI Advice: Conceptualization and the Effect of Explanations. IUI 2023. DOI: 10.1145/3581641.3584066. arXiv:2302.02187. https://arxiv.org/abs/2302.02187
63. Vasconcelos, H., Jörke, M., Grunde-McLaughlin, M., Gerstenberg, T., Bernstein, M., Krishna, R. (2023). Explanations Can Reduce Overreliance on AI Systems During Decision-Making. CSCW 2023. arXiv:2212.06823. https://arxiv.org/abs/2212.06823
64. Chen, V., Liao, Q. V., Wortman Vaughan, J., Bansal, G. (2023). Understanding the Role of Human Intuition on Reliance in Human-AI Decision-Making with Explanations. CSCW 2023. arXiv:2301.07255. https://arxiv.org/abs/2301.07255
65. Kim, S. S. Y., Liao, Q. V., Vorvoreanu, M., Ballard, S., Wortman Vaughan, J. (2024). "I'm Not Sure, But...": Examining the Impact of Large Language Models' Uncertainty Expression on User Reliance and Trust. FAccT 2024. arXiv:2405.00623. https://arxiv.org/abs/2405.00623

## 8. Sources not verified

These came up in the search and look relevant. They were not opened, or could not be opened, so nothing in this note rests on them. Their details are as the search results gave them and are not checked.

| Source | Why not verified |
|---|---|
| Fraunhofer IEG flyer "KI Integrierte Energieplanung" and poster on AI and the heat transition, both linked on [33] | The PDF files did not open in the browser. They are the only documents of the centre on that page besides the programme text. |
| Machlev and others (2022), a review of explainable AI techniques for energy and power systems, Energy and AI | The publisher page refused access (HTTP 403). Title and authors are from memory and are not confirmed. |
| A Monte Carlo study of geothermal investment risk with an option to abandon after the first well, manuscript on the UHasselt document server | The server was closed to outside users (HTTP 503). Authors not confirmed. |
| Tervonen and Figueira (2008), a survey of stochastic multicriteria acceptability analysis methods, Journal of Multi-Criteria Decision Analysis 15, 1-14, DOI 10.1002/mcda.407 | Seen in search results only. |
| IntegrCiTy PandaThermal, described on a catalogue page as a district heating simulation tool that agents can call | The catalogue page refused access (HTTP 405). No primary source found. |
| BENCH2ROBUST and ToolMaze (arXiv:2606.05806), two further fault injection benchmarks | Seen in search summaries only. |
| MCP-AgentBench (arXiv:2509.09734) | Not opened. |
| GridMind (arXiv:2509.02494), a multi agent system for power system analysis | Not opened. |
| Two 2026 reviews of language models in energy systems, one in Applied Energy and one in Energies | Seen in search results only. |
| F\|Heat (2026), a GIS based tool for municipal heat planning in Germany, International Journal of Sustainable Energy Planning and Management | Not opened. It does not use a language model. |
| The 2024 Microsoft synthesis on appropriate reliance on generative AI, mentioned on the page of [59] | Not opened. |
| ToolGate, Pro2Guard and AgentSpec, systems that check tool calls before they run, cited in the full text of [40] | Not opened. Seen only as citations in [40]. |
