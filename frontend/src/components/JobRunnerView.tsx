import { useEffect, useState, type FormEvent } from "react";
import type { R3ChainClient } from "../api/client";
import type { UIStrings } from "../i18n/strings";
import type {
  JobRecordData,
  MissingInputRow,
  PolicyOption,
  RevisionItem,
} from "../types/api";

interface JobRunnerViewProps {
  t: UIStrings;
  client: R3ChainClient;
  studyId: string;
  activeRevision: string;
  revisionObj: RevisionItem | null;
  areaLabel: string;
  missingInputs: MissingInputRow[];
  availablePolicies: PolicyOption[];
  wellScenarioIds: string[];
  activeJob: JobRecordData | null;
  onJobUpdated: (job: JobRecordData) => void;
  onRunCompleted: (runId: string) => void;
}

export function JobRunnerView(props: JobRunnerViewProps) {
  const {
    t,
    client,
    studyId,
    activeRevision,
    revisionObj,
    areaLabel,
    missingInputs,
    availablePolicies,
    wellScenarioIds,
    activeJob,
    onJobUpdated,
    onRunCompleted,
  } = props;

  const uncertaintyPolicies = availablePolicies.filter((p) => p.kind === "uncertainty");
  const [selectedPolicyId, setSelectedPolicyId] = useState<string>("");
  const [seed, setSeed] = useState<number>(0);
  const [elapsedS, setElapsedS] = useState<number>(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  // Single-source port diagnostic controls
  const [sourceId, setSourceId] = useState<string>(wellScenarioIds[0] || "source-alpha");
  const [massFlow, setMassFlow] = useState<string>("12");
  const [injTemp, setInjTemp] = useState<string>("313.15");
  const [sourceDiagResult, setSourceDiagResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (wellScenarioIds.length > 0 && !wellScenarioIds.includes(sourceId)) {
      setSourceId(wellScenarioIds[0]);
    }
  }, [wellScenarioIds, sourceId]);

  useEffect(() => {
    if (!activeJob) {
      setElapsedS(0);
      return;
    }
    const running = activeJob.status === "queued" || activeJob.status === "running";
    if (!running) {
      return;
    }
    const startMs = Date.parse(activeJob.created_at);
    const timer = setInterval(async () => {
      if (Number.isFinite(startMs)) {
        setElapsedS(Math.max(0, Math.round((Date.now() - startMs) / 1000)));
      }
      try {
        const res = await client.getJob(activeJob.job_id);
        const updated = res.data.job;
        onJobUpdated(updated);
        if (updated.status === "completed" && updated.run_id) {
          onRunCompleted(updated.run_id);
        }
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : String(err));
      }
    }, 350);
    return () => clearInterval(timer);
  }, [activeJob, client, onJobUpdated, onRunCompleted]);

  const handleStartJob = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setLaunching(true);
    try {
      const res = await client.startEvaluation(studyId, {
        revision: activeRevision || undefined,
        uncertainty_policy_id: selectedPolicyId || null,
        seed,
        use_cache: true,
        wait: false,
      });
      const job = res.data.job;
      onJobUpdated(job);
      if (job.status === "completed" && job.run_id) {
        onRunCompleted(job.run_id);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setLaunching(false);
    }
  };

  const handleCancelJob = async () => {
    if (!activeJob) return;
    setErrorMsg(null);
    try {
      const res = await client.cancelJob(activeJob.job_id, false);
      onJobUpdated(res.data.job);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  };

  const handleSourceEval = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    try {
      const res = await client.evaluateSource({
        source_scenario_id: sourceId,
        operating_point: {
          requested_mass_flow_kg_s: Number.parseFloat(massFlow),
          injection_temperature_k: Number.parseFloat(injTemp),
        },
        study_id: studyId,
        revision: activeRevision || undefined,
      });
      setSourceDiagResult(res as unknown as Record<string, unknown>);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  };

  const isRunning = activeJob?.status === "queued" || activeJob?.status === "running";
  const pct = activeJob ? Math.round((activeJob.progress_fraction || 0) * 100) : 0;

  return (
    <div className="form-stack">
      <section className="panel-surface" aria-labelledby="runner-heading">
        <h2 id="runner-heading">{t.runner.heading}</h2>

        <div className="alert alert--info">
          <h3 style={{ marginBlockStart: 0 }}>{t.runner.precheckHeading}</h3>
          <ul>
            <li>
              <strong>{t.areaLabel}:</strong> {areaLabel || "—"}
            </li>
            <li>
              <strong>{t.revisionLabel}:</strong> <code>{activeRevision || "—"}</code>
            </li>
            <li>
              <strong>Unresolved gaps:</strong> {missingInputs.length}
            </li>
            {revisionObj && revisionObj.changes.length > 0 ? (
              <li>
                <strong>Changes from parent:</strong>{" "}
                {revisionObj.changes.map((c) => c.field_path).join(", ")}
              </li>
            ) : null}
          </ul>
        </div>

        <form onSubmit={handleStartJob} className="two-col-grid">
          <div className="field">
            <label htmlFor="runner-policy-select">{t.runner.policyLabel}</label>
            <select
              id="runner-policy-select"
              value={selectedPolicyId}
              onChange={(e) => setSelectedPolicyId(e.target.value)}
            >
              <option value="">Scenario default uncertainty policy</option>
              {uncertaintyPolicies.map((pol) => (
                <option key={`${pol.policy_id}-${pol.version}`} value={pol.policy_id}>
                  {pol.policy_id} (v{pol.version}) — {String(pol.parameters.mode || "")}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="runner-seed-input">{t.runner.seedLabel}</label>
            <input
              id="runner-seed-input"
              type="number"
              step="1"
              min={0}
              value={seed}
              onChange={(e) => setSeed(Number.parseInt(e.target.value || "0", 10))}
            />
          </div>

          <div className="btn-row" style={{ gridColumn: "1 / -1" }}>
            <button type="submit" className="btn btn--primary" disabled={launching || isRunning}>
              {t.runner.startButton}
            </button>
            <button
              type="button"
              className="btn btn--danger"
              disabled={!isRunning}
              onClick={handleCancelJob}
            >
              {t.runner.cancelButton}
            </button>
          </div>
        </form>

        {activeJob ? (
          <div style={{ marginBlockStart: "1rem" }}>
            <label htmlFor="job-progress-bar">
              {t.runner.progressLabel}: <strong>{pct}%</strong>
            </label>
            <progress id="job-progress-bar" value={pct} max={100}>
              {pct}%
            </progress>
            <div className="header-meta" style={{ marginBlockStart: "0.5rem" }}>
              <span>
                <strong>Job ID:</strong> <code>{activeJob.job_id}</code>
              </span>
              <span>
                <strong>{t.runner.statusLabel}:</strong>{" "}
                <span
                  className={`badge ${
                    activeJob.status === "completed"
                      ? "badge--pass"
                      : activeJob.status === "cancelled" || activeJob.status === "failed"
                        ? "badge--fail"
                        : "badge--warn"
                  }`}
                >
                  <span aria-hidden="true">
                    {activeJob.status === "completed"
                      ? "✓"
                      : activeJob.status === "cancelled" || activeJob.status === "failed"
                        ? "✕"
                        : "•"}
                  </span>
                  <span>{activeJob.status}</span>
                </span>
              </span>
              <span>
                <strong>{t.runner.stageLabel}:</strong>{" "}
                <code>{activeJob.current_stage || "queued"}</code> ({activeJob.completed_stages}/
                {activeJob.total_stages})
              </span>
              <span>
                <strong>{t.runner.elapsedLabel}:</strong> {elapsedS}s
              </span>
              {activeJob.run_id ? (
                <span>
                  <strong>Run ID:</strong> <code>{activeJob.run_id}</code>
                </span>
              ) : null}
            </div>
          </div>
        ) : null}

        {errorMsg ? (
          <div className="alert alert--fail" role="alert" style={{ marginBlockStart: "0.75rem" }}>
            <span aria-hidden="true">✕ </span>
            {errorMsg}
          </div>
        ) : null}
      </section>

      <section className="panel-surface" aria-labelledby="source-diag-heading">
        <h3 id="source-diag-heading">{t.runner.sourceDiagHeading}</h3>
        <form onSubmit={handleSourceEval} className="two-col-grid">
          <div className="field">
            <label htmlFor="diag-source-select">{t.runner.sourceSelectLabel}</label>
            <select
              id="diag-source-select"
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
            >
              {(wellScenarioIds.length ? wellScenarioIds : ["source-alpha"]).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="diag-flow-input">{t.runner.flowLabel}</label>
            <input
              id="diag-flow-input"
              type="number"
              step="any"
              value={massFlow}
              onChange={(e) => setMassFlow(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="diag-temp-input">{t.runner.injTempLabel}</label>
            <input
              id="diag-temp-input"
              type="number"
              step="any"
              value={injTemp}
              onChange={(e) => setInjTemp(e.target.value)}
            />
          </div>
          <div className="btn-row" style={{ alignSelf: "end" }}>
            <button type="submit" className="btn">
              {t.runner.evaluateSourceButton}
            </button>
          </div>
        </form>

        {sourceDiagResult ? (
          <div style={{ marginBlockStart: "0.75rem" }}>
            <span
              className={`badge ${
                sourceDiagResult.failure_class ? "badge--fail" : "badge--pass"
              }`}
            >
              <span aria-hidden="true">{sourceDiagResult.failure_class ? "✕" : "✓"}</span>
              <span>
                {String(sourceDiagResult.status)}
                {sourceDiagResult.failure_class
                  ? ` (${String(sourceDiagResult.failure_class)})`
                  : ""}
              </span>
            </span>
            <p>{String(sourceDiagResult.message || "")}</p>
            <pre role="region" aria-label="Source diagnostic output" tabIndex={0} className="code-block">
              <code>{JSON.stringify(sourceDiagResult.values || sourceDiagResult.checks, null, 2)}</code>
            </pre>
          </div>
        ) : null}
      </section>
    </div>
  );
}
