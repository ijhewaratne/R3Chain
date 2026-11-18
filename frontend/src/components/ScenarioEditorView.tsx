import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { UIStrings } from "../i18n/strings";
import type {
  DetailLevel,
  EditorBundleData,
  RevisionItem,
} from "../types/api";
import { attachAriaValidationSync } from "../utils/ariaForm";
import {
  buildPatchFromFieldEdit,
  extractSchemaFormFields,
  type SchemaFormField,
} from "../utils/schemaForm";
import { AssumptionsPanel } from "./AssumptionsPanel";

interface ScenarioEditorViewProps {
  t: UIStrings;
  bundle: EditorBundleData | null;
  assumptions: Array<{
    field_path: string;
    status: "active" | "unknown" | "conflict";
    value: unknown;
    unit: string | null;
    provenance_type: string | null;
    source_id: string | null;
    locator: string | null;
    note: string;
    owner?: string | null;
  }>;
  missingInputs: Array<{
    field_path: string;
    status: string;
    reason: string;
    owner: string | null;
  }>;
  revisions: RevisionItem[];
  activeRevision: string;
  detail: DetailLevel;
  announcerRef: React.RefObject<HTMLDivElement>;
  onSelectRevision: (revisionId: string) => void;
  onCreateRevision: (
    parentRevision: string,
    patches: Array<{ op: string; path: string; value?: unknown }>,
    author: string,
    rationale: string
  ) => Promise<string | null>;
  onNavigateToRunner: () => void;
}

const SECTION_ORDER = [
  "economic_scenarios",
  "well_scenarios",
  "operating_scenarios",
  "sites",
  "connections",
  "plants",
  "uncertainty_models",
  "study",
];

function sanitizeId(raw: string): string {
  return `field-${raw.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export function ScenarioEditorView(props: ScenarioEditorViewProps) {
  const {
    t,
    bundle,
    assumptions,
    missingInputs,
    revisions,
    activeRevision,
    detail,
    announcerRef,
    onSelectRevision,
    onCreateRevision,
    onNavigateToRunner,
  } = props;

  const formRef = useRef<HTMLFormElement | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string | boolean>>({});
  const [author, setAuthor] = useState("planner");
  const [rationale, setRationale] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [createdRevisionId, setCreatedRevisionId] = useState<string | null>(null);
  const [sectionFilter, setSectionFilter] = useState<string>("economic_scenarios");

  const schemaFields = useMemo<SchemaFormField[]>(() => {
    if (!bundle) return [];
    return extractSchemaFormFields(bundle.schema, bundle.scenario);
  }, [bundle]);

  useEffect(() => {
    setDrafts({});
    setCreatedRevisionId(null);
    setSubmitError(null);
  }, [bundle?.revision.revision_id]);

  useEffect(() => {
    if (!formRef.current) return;
    return attachAriaValidationSync(formRef.current, announcerRef.current);
  }, [schemaFields, sectionFilter, announcerRef]);

  const sections = useMemo(() => {
    const unique = Array.from(new Set(schemaFields.map((f) => f.section)));
    return unique.sort((a, b) => {
      const ia = SECTION_ORDER.indexOf(a);
      const ib = SECTION_ORDER.indexOf(b);
      if (ia !== -1 && ib !== -1) return ia - ib;
      if (ia !== -1) return -1;
      if (ib !== -1) return 1;
      return a.localeCompare(b);
    });
  }, [schemaFields]);

  useEffect(() => {
    if (sections.length > 0 && !sections.includes(sectionFilter) && sectionFilter !== "all") {
      setSectionFilter(sections[0]);
    }
  }, [sections, sectionFilter]);

  const visibleFields = useMemo(() => {
    if (sectionFilter === "all") return schemaFields;
    return schemaFields.filter((f) => f.section === sectionFilter);
  }, [schemaFields, sectionFilter]);

  const pendingPatches = useMemo(() => {
    const byPath = new Map(schemaFields.map((f) => [f.path, f]));
    const patches: Array<{
      op: "set" | "set_known";
      path: string;
      value: unknown;
      before: string | boolean;
      after: string | boolean;
    }> = [];
    for (const [path, newVal] of Object.entries(drafts)) {
      const field = byPath.get(path);
      if (!field) continue;
      if (newVal === field.originalValue) continue;
      const patch = buildPatchFromFieldEdit(field, newVal);
      if (
        (field.inputKind === "number" || field.inputKind === "integer") &&
        (typeof patch.value !== "number" || !Number.isFinite(patch.value))
      ) {
        continue;
      }
      patches.push({
        ...patch,
        before: field.originalValue,
        after: newVal,
      });
    }
    return patches;
  }, [drafts, schemaFields]);

  if (!bundle) {
    return <p>{t.common.loading}</p>;
  }

  const handleFieldChange = (field: SchemaFormField, next: string | boolean) => {
    setDrafts((prev) => ({
      ...prev,
      [field.path]: next,
    }));
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitError(null);
    const form = e.currentTarget;
    if (!form.checkValidity()) {
      const firstInvalid = form.querySelector<HTMLElement>(":invalid, [aria-invalid='true']");
      firstInvalid?.focus();
      form.reportValidity();
      return;
    }
    if (pendingPatches.length === 0) {
      setSubmitError("Change at least one parameter before creating a new revision.");
      return;
    }
    setSubmitting(true);
    try {
      const newRev = await onCreateRevision(
        bundle.revision.revision_id,
        pendingPatches.map(({ op, path, value }) => ({ op, path, value })),
        author.trim() || "planner",
        rationale.trim()
      );
      if (newRev) {
        setCreatedRevisionId(newRev);
        setDrafts({});
        setRationale("");
      }
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="form-stack">
      <div className="panel-surface">
        <AssumptionsPanel
          t={t}
          assumptions={assumptions}
          missingInputs={missingInputs}
          detail={detail}
        />
      </div>

      <section className="panel-surface" aria-labelledby="schema-editor-heading">
        <h2 id="schema-editor-heading">{t.editor.heading}</h2>
        <p>{t.editor.schemaHint}</p>

        <div className="btn-row" style={{ marginBlockEnd: "1rem" }}>
          <div className="control-group">
            <label htmlFor="editor-section-select">{t.editor.schemaHeading}</label>
            <select
              id="editor-section-select"
              value={sectionFilter}
              onChange={(e) => setSectionFilter(e.target.value)}
            >
              {sections.map((sec) => (
                <option key={sec} value={sec}>
                  {sec} ({schemaFields.filter((f) => f.section === sec).length})
                </option>
              ))}
              <option value="all">All sections ({schemaFields.length})</option>
            </select>
          </div>
        </div>

        <form ref={formRef} onSubmit={handleSubmit} className="form-stack" noValidate>
          <fieldset>
            <legend>
              {sectionFilter} ({visibleFields.length})
            </legend>
            <div className="two-col-grid">
              {visibleFields.map((field) => {
                const inputId = sanitizeId(field.path);
                const hintId = `${inputId}-hint`;
                const errorId = `${inputId}-error`;
                const currentVal =
                  field.path in drafts ? drafts[field.path] : field.value;
                const unitLabel = field.unit ? ` [${field.unit}]` : "";
                const evTag = field.evidenceState ? ` (${field.evidenceState})` : "";

                if (field.inputKind === "boolean") {
                  return (
                    <div key={field.path} className="checkbox-item">
                      <input
                        id={inputId}
                        name={field.path}
                        type="checkbox"
                        checked={Boolean(currentVal)}
                        onChange={(ev) => handleFieldChange(field, ev.target.checked)}
                      />
                      <label htmlFor={inputId}>
                        {field.subgroup}: {field.label}
                        {unitLabel}
                      </label>
                    </div>
                  );
                }

                if (field.inputKind === "enum" && field.enumValues.length <= 5) {
                  return (
                    <fieldset key={field.path}>
                      <legend>
                        {field.subgroup}: {field.label}
                        {unitLabel}
                      </legend>
                      <div className="checkbox-row">
                        {field.enumValues.map((opt) => {
                          const rId = `${inputId}-${opt}`;
                          return (
                            <label key={opt} htmlFor={rId} className="checkbox-item">
                              <input
                                id={rId}
                                type="radio"
                                name={field.path}
                                value={opt}
                                checked={String(currentVal) === opt}
                                onChange={(ev) => handleFieldChange(field, ev.target.value)}
                              />
                              <span>{opt}</span>
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>
                  );
                }

                return (
                  <div key={field.path} className="field">
                    <label htmlFor={inputId}>
                      {field.subgroup}: {field.label}
                      {unitLabel}
                      {evTag}
                    </label>
                    <span id={hintId} className="hint">
                      <code>{field.path}</code>
                      {field.description ? ` — ${field.description}` : ""}
                    </span>
                    {field.inputKind === "enum" ? (
                      <select
                        id={inputId}
                        name={field.path}
                        value={String(currentVal)}
                        required={field.required}
                        aria-describedby={hintId}
                        data-error={errorId}
                        onChange={(ev) => handleFieldChange(field, ev.target.value)}
                      >
                        {field.enumValues.map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    ) : field.inputKind === "number" || field.inputKind === "integer" ? (
                      <input
                        id={inputId}
                        name={field.path}
                        type="number"
                        step={field.inputKind === "integer" ? "1" : "any"}
                        min={
                          field.min !== undefined
                            ? field.min
                            : field.exclusiveMin !== undefined
                              ? field.exclusiveMin
                              : undefined
                        }
                        max={
                          field.max !== undefined
                            ? field.max
                            : field.exclusiveMax !== undefined
                              ? field.exclusiveMax
                              : undefined
                        }
                        required={field.required}
                        value={String(currentVal)}
                        aria-describedby={hintId}
                        data-error={errorId}
                        onChange={(ev) => handleFieldChange(field, ev.target.value)}
                      />
                    ) : (
                      <input
                        id={inputId}
                        name={field.path}
                        type="text"
                        required={field.required}
                        minLength={field.minLength}
                        pattern={field.pattern}
                        value={String(currentVal)}
                        aria-describedby={hintId}
                        data-error={errorId}
                        onChange={(ev) => handleFieldChange(field, ev.target.value)}
                      />
                    )}
                    <div id={errorId} className="error-msg">
                      <span role="img" aria-label="Error">
                        ✕
                      </span>{" "}
                      {t.editor.numericError}
                    </div>
                  </div>
                );
              })}
            </div>
          </fieldset>

          <fieldset>
            <legend>{t.editor.pendingChangesHeading}</legend>
            {pendingPatches.length === 0 ? (
              <p className="hint">{t.common.none}</p>
            ) : (
              <ul>
                {pendingPatches.map((p) => (
                  <li key={p.path}>
                    <code>{p.path}</code> ({p.op}):{" "}
                    <span>{String(p.before)}</span> → <strong>{String(p.after)}</strong>
                  </li>
                ))}
              </ul>
            )}

            <div className="two-col-grid" style={{ marginBlockStart: "0.75rem" }}>
              <div className="field">
                <label htmlFor="rev-author-input">{t.editor.authorLabel}</label>
                <span id="rev-author-hint" className="hint">
                  {t.editor.authorHint}
                </span>
                <input
                  id="rev-author-input"
                  name="author"
                  type="text"
                  required
                  value={author}
                  aria-describedby="rev-author-hint"
                  data-error="rev-author-error"
                  onChange={(ev) => setAuthor(ev.target.value)}
                />
                <div id="rev-author-error" className="error-msg">
                  <span role="img" aria-label="Error">
                    ✕
                  </span>{" "}
                  {t.editor.authorLabel}
                </div>
              </div>

              <div className="field">
                <label htmlFor="rev-rationale-input">{t.editor.rationaleLabel}</label>
                <span id="rev-rationale-hint" className="hint">
                  {t.editor.rationaleHint}
                </span>
                <input
                  id="rev-rationale-input"
                  name="rationale"
                  type="text"
                  required
                  minLength={3}
                  value={rationale}
                  aria-describedby="rev-rationale-hint"
                  data-error="rev-rationale-error"
                  onChange={(ev) => setRationale(ev.target.value)}
                />
                <div id="rev-rationale-error" className="error-msg">
                  <span role="img" aria-label="Error">
                    ✕
                  </span>{" "}
                  {t.editor.rationaleRequiredError}
                </div>
              </div>
            </div>
          </fieldset>

          {submitError ? (
            <div className="alert alert--fail" role="alert">
              <strong>
                <span aria-hidden="true">✕ </span>
                {t.common.errorPrefix}:
              </strong>{" "}
              {submitError}
            </div>
          ) : null}

          {createdRevisionId ? (
            <div className="alert alert--pass" role="status">
              <span aria-hidden="true">✓ </span>
              {t.editor.savedRevisionSuccess} <code>{createdRevisionId}</code>{" "}
              <button type="button" className="btn btn--primary" onClick={onNavigateToRunner}>
                {t.editor.runNowButton}
              </button>
            </div>
          ) : null}

          <div className="btn-row">
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? t.editor.savingRevision : t.editor.saveRevisionButton}
            </button>
          </div>
        </form>
      </section>

      <section className="panel-surface" aria-labelledby="branch-history-heading">
        <h3 id="branch-history-heading">{t.editor.branchHistoryHeading}</h3>
        <div
          tabIndex={0}
          role="region"
          aria-labelledby="revision-history-caption"
          className="table-wrapper responsive-stacked"
        >
          <table>
            <caption id="revision-history-caption">{t.editor.branchHistoryHeading}</caption>
            <thead>
              <tr>
                <th scope="col">{t.editor.colRevision}</th>
                <th scope="col">{t.editor.colParent}</th>
                <th scope="col">{t.editor.colAuthor}</th>
                <th scope="col">{t.editor.colRationale}</th>
                <th scope="col">{t.editor.colChanges}</th>
                <th scope="col">{t.editor.colRuns}</th>
              </tr>
            </thead>
            <tbody>
              {revisions.map((rev) => (
                <tr key={rev.revision_id}>
                  <th scope="row">
                    <button
                      type="button"
                      className={`btn ${rev.revision_id === activeRevision ? "btn--primary" : ""}`}
                      onClick={() => onSelectRevision(rev.revision_id)}
                    >
                      {rev.revision_id}
                    </button>
                  </th>
                  <td data-label={t.editor.colParent}>
                    <code>{rev.parent_revision_id || "root"}</code>
                  </td>
                  <td data-label={t.editor.colAuthor}>{rev.author}</td>
                  <td data-label={t.editor.colRationale}>{rev.rationale}</td>
                  <td data-label={t.editor.colChanges}>
                    {rev.changes.length === 0
                      ? t.common.none
                      : rev.changes
                          .map((c) => `${c.field_path}: ${String(c.before)} -> ${String(c.after)}`)
                          .join("; ")}
                  </td>
                  <td data-label={t.editor.colRuns}>
                    {rev.runs.length === 0
                      ? t.common.none
                      : rev.runs.map((r) => (
                          <div key={r.run_id}>
                            <code>{r.run_id}</code> ({r.status})
                            {r.is_stale ? (
                              <span className="badge badge--warn" style={{ marginInlineStart: "0.35rem" }}>
                                <span aria-hidden="true">⚠</span>
                                <span>stale</span>
                              </span>
                            ) : null}
                          </div>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
