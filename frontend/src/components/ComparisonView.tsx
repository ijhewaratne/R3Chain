import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { R3ChainClient } from "../api/client";
import type { UIStrings } from "../i18n/strings";
import type { DetailLevel, EnergyUnitDisplay, Language, RevisionRunSummary } from "../types/api";
import { formatCurrency, formatEnergy, formatHeatCost, JOULES_PER_GJ, JOULES_PER_MWH } from "../utils/units";

interface ComparisonViewProps {
  t: UIStrings;
  lang: Language;
  client: R3ChainClient;
  runs: RevisionRunSummary[];
  selectedRunId: string;
  onSelectRun: (runId: string) => void;
  runEvidence: Record<string, unknown> | null;
  energyUnit: EnergyUnitDisplay;
  detail: DetailLevel;
}

export function ComparisonView(props: ComparisonViewProps) {
  const {
    t,
    lang,
    client,
    runs,
    selectedRunId,
    onSelectRun,
    runEvidence,
    energyUnit,
    detail,
  } = props;

  const [crossCompareOut, setCrossCompareOut] = useState<Record<string, unknown> | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);

  const stagePayloads = useMemo(() => {
    const out: Record<string, Record<string, unknown>> = {};
    const evData = (runEvidence?.data as Record<string, unknown>) || {};
    const results = ((evData.results ?? runEvidence?.results) as Array<Record<string, unknown>>) || [];
    const study = results.find((r) => r.subject === "study");
    const outputs = (study?.stage_outputs as Array<Record<string, unknown>>) || [];
    for (const o of outputs) {
      out[String(o.stage)] = (o.payload as Record<string, unknown>) || {};
    }
    return out;
  }, [runEvidence]);

  const assess = stagePayloads.assess || {};
  const operate = stagePayloads.operate || {};
  const cost = stagePayloads.cost || {};
  const compare = stagePayloads.compare || {};

  const assessments = (assess.assessments as Array<Record<string, unknown>>) || [];
  const rejectedPreEval = (assess.rejected as Array<Record<string, unknown>>) || [];
  const candCosts = (cost.candidates as Record<string, Record<string, unknown>>) || {};
  const opCands = (operate.candidates as Record<string, Record<string, unknown>>) || {};
  const uComp = (compare.comparison as Record<string, unknown>) || {};
  const stabilityList = (uComp.ranking_stability as Array<Record<string, unknown>>) || [];
  const stabilityById = new Map(stabilityList.map((item) => [String(item.candidate_id), item]));
  const uCands = (uComp.candidates as Record<string, Record<string, unknown>>) || {};
  const drivers = (uComp.decision_drivers as Array<Record<string, unknown>>) || [];
  const resilienceMap = (compare.resilience as Record<string, Array<Record<string, unknown>>>) || {};

  // Eligible-only ranked rows, preserving spec §18.1 rule: eligibility precedes cost ranking.
  const eligibleRows = useMemo(() => {
    const rows = assessments
      .filter((a) => Boolean((a.ranking_eligibility as Record<string, unknown>)?.eligible))
      .map((a) => {
        const cid = String(a.candidate_id);
        const cEntry = candCosts[cid] || {};
        const bounds = (cEntry.boundaries as Record<string, Record<string, unknown>>) || {};
        const geo = bounds.geothermal_delivered || {};
        const sys = bounds.system_delivered || {};
        const comp = (cEntry.baseline_comparison as Record<string, unknown>) || {};
        const opTotals = ((opCands[cid] || {}).totals as Record<string, unknown>) || {};
        const stab = stabilityById.get(cid);
        const uRow = uCands[cid];
        const acc = (uRow?.sample_account as Record<string, unknown>) || {};

        const geoLcoh = typeof geo.lcoh_currency_per_j === "number" ? geo.lcoh_currency_per_j : null;
        const sysLcoh = typeof sys.lcoh_currency_per_j === "number" ? sys.lcoh_currency_per_j : null;
        return {
          candidate_id: cid,
          label: String(a.label || cid),
          geoLcoh,
          sysLcoh,
          capex: typeof geo.capex_currency === "number" ? geo.capex_currency : null,
          deltaPv:
            typeof comp.incremental_discounted_cost_currency === "number"
              ? comp.incremental_discounted_cost_currency
              : null,
          geoHeatJ:
            typeof opTotals.geothermal_delivered_heat_j === "number"
              ? opTotals.geothermal_delivered_heat_j
              : null,
          backupHeatJ:
            typeof opTotals.backup_delivered_heat_j === "number"
              ? opTotals.backup_delivered_heat_j
              : null,
          currency: String(geo.currency || "EUR"),
          stabilityText: stab
            ? `${String(stab.first_rank_count)}/${String(stab.denominator)}`
            : acc.denominator
              ? `feasible ${String(acc.feasible_samples)}/${String(acc.denominator)}`
              : "—",
        };
      });
    rows.sort((x, y) => (x.geoLcoh ?? Number.POSITIVE_INFINITY) - (y.geoLcoh ?? Number.POSITIVE_INFINITY));
    return rows;
  }, [assessments, candCosts, opCands, stabilityById, uCands]);

  const chartData = useMemo(() => {
    const factor = energyUnit === "GJ" ? JOULES_PER_GJ : JOULES_PER_MWH;
    return eligibleRows.map((r) => ({
      name: r.candidate_id,
      geoLcoh: r.geoLcoh !== null ? Number((r.geoLcoh * factor).toFixed(2)) : 0,
      sysLcoh: r.sysLcoh !== null ? Number((r.sysLcoh * factor).toFixed(2)) : 0,
    }));
  }, [eligibleRows, energyUnit]);

  const handleCompareCurrentRunResults = async () => {
    setCompareError(null);
    const evData = (runEvidence?.data as Record<string, unknown>) || {};
    const results = ((evData.results ?? runEvidence?.results) as Array<Record<string, unknown>>) || [];
    const candIds = results.filter((r) => r.subject !== "study").map((r) => String(r.result_id));
    if (candIds.length === 0) {
      setCompareError("No candidate result records available for selected run.");
      return;
    }
    try {
      const res = await client.compareResults({
        result_ids: candIds,
        lang,
      });
      setCrossCompareOut(res as unknown as Record<string, unknown>);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="form-stack">
      <section className="panel-surface" aria-labelledby="compare-heading">
        <h2 id="compare-heading">{t.compare.heading}</h2>

        <div className="btn-row" style={{ marginBlockEnd: "1rem" }}>
          <div className="control-group">
            <label htmlFor="compare-run-select">{t.compare.runSelectLabel}</label>
            <select
              id="compare-run-select"
              value={selectedRunId}
              onChange={(e) => onSelectRun(e.target.value)}
            >
              {runs.length === 0 ? (
                <option value="">{t.common.none}</option>
              ) : (
                runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id} ({r.status}){r.is_stale ? " [stale]" : ""}
                  </option>
                ))
              )}
            </select>
          </div>
          <button type="button" className="btn" onClick={handleCompareCurrentRunResults}>
            {t.compare.crossCompareButton}
          </button>
        </div>

        {/* 1. Eligibility & Failure Reasons FIRST (Plan v2 / §18.1) */}
        <h3>{t.compare.eligibilityHeading}</h3>
        <p>{t.compare.eligibilityNote}</p>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="eligibility-caption"
          className="table-wrapper responsive-stacked"
          style={{ marginBlockEnd: "1.25rem" }}
        >
          <table>
            <caption id="eligibility-caption">{t.compare.eligibilityCaption}</caption>
            <thead>
              <tr>
                <th scope="col">{t.compare.colCandidate}</th>
                <th scope="col">{t.compare.colTechnical}</th>
                <th scope="col">{t.compare.colEvaluation}</th>
                <th scope="col">{t.compare.colEligible}</th>
                <th scope="col">{t.compare.colReasons}</th>
              </tr>
            </thead>
            <tbody>
              {assessments.map((item) => {
                const cid = String(item.candidate_id);
                const eligObj = (item.ranking_eligibility as Record<string, unknown>) || {};
                const eligible = Boolean(eligObj.eligible);
                const conditional = Boolean(eligObj.conditional);
                const reasons = [
                  ...((eligObj.reasons as string[]) || []),
                  ...((item.explanation as string[]) || []),
                ];
                const badgeCls = eligible
                  ? "badge--pass"
                  : conditional
                    ? "badge--warn"
                    : "badge--fail";
                const icon = eligible ? "✓" : conditional ? "⚠" : "✕";
                return (
                  <tr key={cid}>
                    <th scope="row">
                      {String(item.label || cid)} (<code>{cid}</code>)
                    </th>
                    <td data-label={t.compare.colTechnical}>
                      {String(item.technical_status || "—")}
                    </td>
                    <td data-label={t.compare.colEvaluation}>
                      {String(item.evaluation_status || "—")}
                    </td>
                    <td data-label={t.compare.colEligible}>
                      <span className={`badge ${badgeCls}`}>
                        <span aria-hidden="true">{icon}</span>
                        <span>{eligible ? t.common.statusPass : conditional ? t.common.statusWarn : t.common.statusFail}</span>
                      </span>
                    </td>
                    <td data-label={t.compare.colReasons}>
                      {Array.from(new Set(reasons)).join("; ") || t.common.none}
                    </td>
                  </tr>
                );
              })}
              {rejectedPreEval.map((rej, idx) => (
                <tr key={`rej-${idx}`}>
                  <th scope="row">{JSON.stringify(rej.option || {})}</th>
                  <td data-label={t.compare.colTechnical}>rejected ({String(rej.stage)})</td>
                  <td data-label={t.compare.colEvaluation}>excluded</td>
                  <td data-label={t.compare.colEligible}>
                    <span className="badge badge--fail">
                      <span aria-hidden="true">✕</span>
                      <span>{t.common.statusFail}</span>
                    </span>
                  </td>
                  <td data-label={t.compare.colReasons}>{String(rej.reason || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 2. Comparable Cost & Technical Ranking (Eligible Only) */}
        <h3>{t.compare.costRankingHeading}</h3>
        <div
          tabIndex={0}
          role="region"
          aria-labelledby="cost-ranking-caption"
          className="table-wrapper responsive-stacked"
          style={{ marginBlockEnd: "1.25rem" }}
        >
          <table>
            <caption id="cost-ranking-caption">{t.compare.costCaption}</caption>
            <thead>
              <tr>
                <th scope="col">{t.compare.colCandidate}</th>
                <th scope="col">{t.compare.colLcohGeo}</th>
                <th scope="col">{t.compare.colLcohSys}</th>
                <th scope="col">{t.compare.colCapex}</th>
                <th scope="col">{t.compare.colDeltaBaseline}</th>
                <th scope="col">{t.compare.colGeoHeat}</th>
                <th scope="col">{t.compare.colBackupHeat}</th>
                <th scope="col">{t.compare.colUncertainty}</th>
              </tr>
            </thead>
            <tbody>
              {eligibleRows.map((row) => (
                <tr key={row.candidate_id}>
                  <th scope="row">
                    {row.label} (<code>{row.candidate_id}</code>)
                  </th>
                  <td data-label={t.compare.colLcohGeo}>
                    {formatHeatCost(row.geoLcoh, energyUnit, row.currency)}
                  </td>
                  <td data-label={t.compare.colLcohSys}>
                    {formatHeatCost(row.sysLcoh, energyUnit, row.currency)}
                  </td>
                  <td data-label={t.compare.colCapex}>
                    {formatCurrency(row.capex, row.currency)}
                  </td>
                  <td data-label={t.compare.colDeltaBaseline}>
                    {formatCurrency(row.deltaPv, row.currency)}
                  </td>
                  <td data-label={t.compare.colGeoHeat}>
                    {formatEnergy(row.geoHeatJ, energyUnit)}
                  </td>
                  <td data-label={t.compare.colBackupHeat}>
                    {formatEnergy(row.backupHeatJ, energyUnit)}
                  </td>
                  <td data-label={t.compare.colUncertainty}>{row.stabilityText}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {chartData.length > 0 ? (
          <figure className="chart-figure">
            <figcaption>{t.compare.chartTitle} ({energyUnit})</figcaption>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={260} minWidth={240}>
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="geoLcoh" name={t.compare.colLcohGeo} fill="#0b4f8a" />
                  <Bar dataKey="sysLcoh" name={t.compare.colLcohSys} fill="#0f766e" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </figure>
        ) : null}

        {compareError ? (
          <div className="alert alert--fail" role="alert" style={{ marginBlockStart: "0.75rem" }}>
            <span aria-hidden="true">✕ </span>
            {compareError}
          </div>
        ) : null}

        {crossCompareOut ? (
          <div className="alert alert--info" style={{ marginBlockStart: "0.75rem" }}>
            <strong>{t.compare.crossCompareHeading}:</strong>{" "}
            {String(
              ((crossCompareOut.explanation as Record<string, unknown>) || {}).rendered || ""
            )}
          </div>
        ) : null}
      </section>

      {detail === "technical" || drivers.length > 0 ? (
        <section className="panel-surface" aria-labelledby="drivers-heading">
          <h3 id="drivers-heading">{t.compare.driversHeading}</h3>
          {uComp.preference_reason ? (
            <p>
              <strong>Preference ({String(uComp.preference_outcome || "—")}):</strong>{" "}
              {String(uComp.preference_reason)}
            </p>
          ) : null}

          {drivers.length > 0 ? (
            <div
              tabIndex={0}
              role="region"
              aria-labelledby="drivers-table-caption"
              className="table-wrapper responsive-stacked"
              style={{ marginBlockEnd: "1rem" }}
            >
              <table>
                <caption id="drivers-table-caption">{t.compare.driversHeading}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t.compare.colDriver}</th>
                    <th scope="col">{t.compare.colRho}</th>
                    <th scope="col">{t.compare.colSwitches}</th>
                  </tr>
                </thead>
                <tbody>
                  {drivers.map((drv) => (
                    <tr key={String(drv.variable_id)}>
                      <th scope="row">
                        <code>{String(drv.variable_id)}</code>
                      </th>
                      <td data-label={t.compare.colRho}>
                        {typeof drv.spearman_rank_correlation === "number"
                          ? drv.spearman_rank_correlation.toFixed(3)
                          : "—"}
                      </td>
                      <td data-label={t.compare.colSwitches}>
                        {drv.switches_winner ? "Yes" : "No"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {Object.keys(resilienceMap).length > 0 ? (
            <>
              <h4>{t.compare.resilienceHeading}</h4>
              <div
                tabIndex={0}
                role="region"
                aria-labelledby="resilience-caption"
                className="table-wrapper responsive-stacked"
              >
                <table>
                  <caption id="resilience-caption">{t.compare.resilienceCaption}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t.compare.colCandidate}</th>
                      <th scope="col">Outage case</th>
                      <th scope="col">Unserved heat ({energyUnit})</th>
                      <th scope="col">Hours</th>
                      <th scope="col">Largest deficit (MW)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(resilienceMap).flatMap(([cid, rows]) =>
                      rows.map((r) => (
                        <tr key={`${cid}-${String(r.case_id)}`}>
                          <th scope="row">{cid}</th>
                          <td data-label="Outage case">{String(r.case_id)}</td>
                          <td data-label={`Unserved heat (${energyUnit})`}>
                            {formatEnergy(Number(r.unserved_heat_j || 0), energyUnit)}
                          </td>
                          <td data-label="Hours">{String(r.unserved_hours ?? 0)}</td>
                          <td data-label="Largest deficit (MW)">
                            {(Number(r.largest_deficit_w || 0) / 1e6).toFixed(2)}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
