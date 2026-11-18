import { useRef, useState, type FormEvent } from "react";
import type { R3ChainClient } from "../api/client";
import type { UIStrings } from "../i18n/strings";
import type { ExplanationRecordData, Language } from "../types/api";

interface EvidenceViewProps {
  t: UIStrings;
  lang: Language;
  client: R3ChainClient;
  studyId: string;
  activeRevision: string;
  selectedRunId: string;
  runEvidence: Record<string, unknown> | null;
}

export function EvidenceView(props: EvidenceViewProps) {
  const {
    t,
    lang,
    client,
    studyId,
    activeRevision,
    selectedRunId,
    runEvidence,
  } = props;

  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [modalRecord, setModalRecord] = useState<ExplanationRecordData | null>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [passages, setPassages] = useState<Array<Record<string, unknown>>>([]);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [exportedText, setExportedText] = useState<string>("");
  const [exportFormat, setExportFormat] = useState<"markdown" | "json">("markdown");
  const [exportError, setExportError] = useState<string | null>(null);

  const [replayStatus, setReplayStatus] = useState<Record<string, unknown> | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  const expObj = (runEvidence?.explanation as Record<string, unknown>) || {};
  const records = (expObj.records as ExplanationRecordData[]) || [];
  const rendered = (expObj.rendered as Record<string, string>) || {};

  const openCitationDialog = (rec: ExplanationRecordData) => {
    setModalRecord(rec);
    dialogRef.current?.showModal();
  };

  const handleSearch = async (e: FormEvent) => {
    e.preventDefault();
    setSearchError(null);
    try {
      const res = await client.searchEvidence(studyId, {
        query: searchQuery,
        revision: activeRevision || undefined,
      });
      setPassages((res.data.passages as Array<Record<string, unknown>>) || []);
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleExport = async (fmt: "markdown" | "json") => {
    if (!selectedRunId) return;
    setExportError(null);
    setExportFormat(fmt);
    try {
      const res = await client.exportReport({
        run_id: selectedRunId,
        format: fmt,
        lang,
      });
      setExportedText(res.data.content);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDownload = () => {
    if (!exportedText) return;
    const blob = new Blob([exportedText], {
      type: exportFormat === "json" ? "application/json" : "text/markdown",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedRunId || "r3chain-report"}.${exportFormat === "json" ? "json" : "md"}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleReplay = async () => {
    if (!selectedRunId) return;
    setReplayError(null);
    try {
      const res = await client.replayRun(selectedRunId);
      setReplayStatus((res.data.replay as Record<string, unknown>) || res.data);
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="form-stack">
      <section className="panel-surface" aria-labelledby="evidence-explanations-heading">
        <h2 id="evidence-explanations-heading">{t.evidence.heading}</h2>
        <h3>{t.evidence.explanationsHeading}</h3>

        {records.length === 0 ? (
          <p>{t.common.none}</p>
        ) : (
          <div className="two-col-grid">
            {records.map((rec) => (
              <article key={rec.explanation_id} className="panel-surface">
                <div className="btn-row" style={{ justifyContent: "space-between", marginBlockEnd: "0.5rem" }}>
                  <strong>
                    {rec.kind}: <code>{rec.subject}</code>
                  </strong>
                  <span className={`badge ${rec.all_claims_accepted ? "badge--pass" : "badge--fail"}`}>
                    <span aria-hidden="true">{rec.all_claims_accepted ? "✓" : "✕"}</span>
                    <span>
                      {rec.all_claims_accepted
                        ? t.evidence.claimVerified
                        : t.evidence.claimRejected}
                    </span>
                  </span>
                </div>
                <pre className="code-block" tabIndex={0} role="region" aria-label={rec.subject}>
                  <code>{rendered[rec.explanation_id] || ""}</code>
                </pre>
                <div className="btn-row" style={{ marginBlockStart: "0.5rem" }}>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => openCitationDialog(rec)}
                  >
                    {t.evidence.inspectEvidence} ({rec.claims.length})
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="panel-surface" aria-labelledby="evidence-search-heading">
        <h3 id="evidence-search-heading">{t.evidence.searchHeading}</h3>
        <form role="search" onSubmit={handleSearch} className="btn-row" style={{ alignItems: "end" }}>
          <div className="field" style={{ flex: "1 1 280px" }}>
            <label htmlFor="evidence-search-input">{t.evidence.searchLabel}</label>
            <input
              id="evidence-search-input"
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn--primary">
            {t.evidence.searchButton}
          </button>
        </form>

        {searchError ? (
          <div className="alert alert--fail" role="alert" style={{ marginBlockStart: "0.75rem" }}>
            <span aria-hidden="true">✕ </span>
            {searchError}
          </div>
        ) : null}

        {passages.length > 0 ? (
          <ul style={{ marginBlockStart: "0.75rem" }}>
            {passages.map((p, idx) => (
              <li key={idx}>
                <strong>{String(p.dataset_id)}</strong> (v{String(p.version)} —{" "}
                <code>{String(p.locator)}</code>): {String(p.excerpt)}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="panel-surface" aria-labelledby="evidence-export-heading">
        <h3 id="evidence-export-heading">{t.evidence.exportHeading}</h3>
        <div className="btn-row" style={{ marginBlockEnd: "0.75rem" }}>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!selectedRunId}
            onClick={() => handleExport("markdown")}
          >
            {t.evidence.exportMarkdownButton}
          </button>
          <button
            type="button"
            className="btn"
            disabled={!selectedRunId}
            onClick={() => handleExport("json")}
          >
            {t.evidence.exportJsonButton}
          </button>
          {exportedText ? (
            <button type="button" className="btn" onClick={handleDownload}>
              {t.evidence.downloadButton}
            </button>
          ) : null}
        </div>

        {exportError ? (
          <div className="alert alert--fail" role="alert">
            <span aria-hidden="true">✕ </span>
            {exportError}
          </div>
        ) : null}

        {exportedText ? (
          <pre tabIndex={0} role="region" aria-label={t.evidence.exportHeading} className="code-block">
            <code>{exportedText}</code>
          </pre>
        ) : null}

        <h3 style={{ marginBlockStart: "1rem" }}>{t.evidence.replayHeading}</h3>
        <div className="btn-row">
          <button type="button" className="btn" disabled={!selectedRunId} onClick={handleReplay}>
            {t.evidence.replayButton}
          </button>
        </div>

        {replayError ? (
          <div className="alert alert--fail" role="alert" style={{ marginBlockStart: "0.5rem" }}>
            <span aria-hidden="true">✕ </span>
            {replayError}
          </div>
        ) : null}

        {replayStatus ? (
          <div
            className={`alert ${replayStatus.identical ? "alert--pass" : "alert--fail"}`}
            role="status"
            style={{ marginBlockStart: "0.5rem" }}
          >
            <span aria-hidden="true">{replayStatus.identical ? "✓ " : "✕ "}</span>
            {replayStatus.identical ? t.evidence.replayPassed : "Replay mismatch"}{" "}
            (<code>{String(replayStatus.replay_run_id || "")}</code>)
          </div>
        ) : null}
      </section>

      <dialog ref={dialogRef} aria-labelledby="citation-dialog-title">
        <h2 id="citation-dialog-title">{t.evidence.modalTitle}</h2>
        {modalRecord ? (
          <div>
            <p>
              <strong>{modalRecord.explanation_id}</strong> — Run <code>{modalRecord.run_id}</code>{" "}
              (Revision <code>{modalRecord.scenario_revision}</code>)
            </p>
            <div tabIndex={0} role="region" aria-label={t.evidence.modalTitle} className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Claim</th>
                    <th scope="col">Type</th>
                    <th scope="col">Result / Check / Source ref</th>
                    <th scope="col">Checked</th>
                  </tr>
                </thead>
                <tbody>
                  {modalRecord.claims.map((c, idx) => {
                    const out = modalRecord.check_outcomes[idx];
                    return (
                      <tr key={c.claim_id}>
                        <th scope="row">
                          <code>{c.claim_id}</code>
                        </th>
                        <td>{c.claim_type}</td>
                        <td>
                          {[c.result_id, c.check_id, c.field_path, c.source_ref]
                            .filter(Boolean)
                            .join(" | ")}
                        </td>
                        <td>{out?.accepted ? "✓ pass" : `✕ ${out?.reasons?.join(", ")}`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
        <form method="dialog" style={{ marginBlockStart: "1rem" }}>
          <button type="submit" className="btn btn--primary">
            {t.common.close}
          </button>
        </form>
      </dialog>
    </div>
  );
}
