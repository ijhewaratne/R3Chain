import { useId, useMemo, useState } from "react";
import type { UIStrings } from "../i18n/strings";
import type { AssumptionRow, DetailLevel, MissingInputRow } from "../types/api";
import { formatAssumptionValue } from "../utils/units";

interface AssumptionsPanelProps {
  t: UIStrings;
  assumptions: AssumptionRow[];
  missingInputs: MissingInputRow[];
  detail: DetailLevel;
}

export function AssumptionsPanel({
  t,
  assumptions,
  missingInputs,
  detail,
}: AssumptionsPanelProps) {
  const uid = useId();
  const headingId = `${uid}-assumptions-heading`;
  const queryId = `${uid}-assump-filter-query`;
  const statusId = `${uid}-assump-filter-status`;
  const captionId = `${uid}-assumptions-caption`;

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return assumptions.filter((row) => {
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (!q) return true;
      const hay = `${row.field_path} ${row.source_id || ""} ${row.locator || ""} ${row.note || ""} ${row.owner || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [assumptions, query, statusFilter]);

  // Even in "summary" mode (W26), never hide unresolved gaps, conflicts, or key material inputs.
  const displayed = useMemo(() => {
    if (detail === "technical" || query.trim() || statusFilter !== "all") {
      return filtered;
    }
    const gapsAndConflicts = filtered.filter((r) => r.status !== "active");
    const coreActive = filtered.filter((r) => r.status === "active").slice(0, 12);
    return [...gapsAndConflicts, ...coreActive];
  }, [filtered, detail, query, statusFilter]);

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId}>{t.assumptions.heading}</h2>
      <p>{t.assumptions.subtitle}</p>

      {missingInputs.length > 0 ? (
        <div className="alert alert--warn" role="status">
          <strong>
            <span aria-hidden="true">⚠ </span>
            {missingInputs.length}
          </strong>{" "}
          {t.assumptions.statusUnknown}
        </div>
      ) : null}

      <form
        role="search"
        onSubmit={(e) => e.preventDefault()}
        className="two-col-grid"
        style={{ marginBlockEnd: "0.75rem" }}
      >
        <div className="field">
          <label htmlFor={queryId}>{t.assumptions.searchLabel}</label>
          <input
            id={queryId}
            name="q"
            type="search"
            placeholder={t.assumptions.searchPlaceholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={statusId}>{t.assumptions.statusFilterLabel}</label>
          <select
            id={statusId}
            name="status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">{t.assumptions.allStatuses}</option>
            <option value="active">{t.assumptions.statusActive}</option>
            <option value="unknown">{t.assumptions.statusUnknown}</option>
            <option value="conflict">{t.assumptions.statusConflict}</option>
          </select>
        </div>
      </form>

      <div
        tabIndex={0}
        role="region"
        aria-labelledby={captionId}
        className="table-wrapper responsive-stacked"
      >
        <table>
          <caption id={captionId}>
            {t.assumptions.tableCaption} ({displayed.length} / {assumptions.length})
          </caption>
          <thead>
            <tr>
              <th scope="col">{t.assumptions.colField}</th>
              <th scope="col">{t.assumptions.colStatus}</th>
              <th scope="col">{t.assumptions.colValue}</th>
              <th scope="col">{t.assumptions.colSourceKind}</th>
              <th scope="col">{t.assumptions.colEvidence}</th>
              <th scope="col">{t.assumptions.colOwnerNote}</th>
            </tr>
          </thead>
          <tbody>
            {displayed.map((row) => {
              const badgeCls =
                row.status === "active"
                  ? "badge--pass"
                  : row.status === "conflict"
                    ? "badge--fail"
                    : "badge--warn";
              const icon = row.status === "active" ? "✓" : row.status === "conflict" ? "✕" : "⚠";
              return (
                <tr key={row.field_path}>
                  <th scope="row">
                    <code>{row.field_path}</code>
                  </th>
                  <td data-label={t.assumptions.colStatus}>
                    <span className={`badge ${badgeCls}`}>
                      <span aria-hidden="true">{icon}</span>
                      <span>{row.status}</span>
                    </span>
                  </td>
                  <td data-label={t.assumptions.colValue}>
                    {formatAssumptionValue(row.value, row.unit)}
                  </td>
                  <td data-label={t.assumptions.colSourceKind}>
                    {row.provenance_type || "—"}
                  </td>
                  <td data-label={t.assumptions.colEvidence}>
                    {row.source_id ? (
                      <span>
                        <code>{row.source_id}</code>
                        {row.locator ? ` (${row.locator})` : ""}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td data-label={t.assumptions.colOwnerNote}>
                    {row.owner ? `${row.owner}: ` : ""}
                    {row.note || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
