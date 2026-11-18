import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ACCESS_PROFILES, ApiRequestError, R3ChainClient } from "./api/client";
import { AssumptionsPanel } from "./components/AssumptionsPanel";
import { ComparisonView } from "./components/ComparisonView";
import { EvidenceView } from "./components/EvidenceView";
import { JobRunnerView } from "./components/JobRunnerView";
import { MapView } from "./components/MapView";
import { ScenarioEditorView } from "./components/ScenarioEditorView";
import { StudyHeader } from "./components/StudyHeader";
import { WorkspaceTabs, type TabDefinition } from "./components/WorkspaceTabs";
import { STRINGS } from "./i18n/strings";
import type {
  AssumptionRow,
  DetailLevel,
  EditorBundleData,
  EnergyUnitDisplay,
  JobRecordData,
  Language,
  MapBundleData,
  MissingInputRow,
  RevisionItem,
  RevisionRunSummary,
  StudySummary,
} from "./types/api";

interface WorkspaceAppProps {
  apiBaseUrl?: string;
}

export function App({ apiBaseUrl = "" }: WorkspaceAppProps) {
  const announcerRef = useRef<HTMLDivElement | null>(null);

  const [lang, setLang] = useState<Language>("en");
  const [detail, setDetail] = useState<DetailLevel>("summary");
  const [energyUnit, setEnergyUnit] = useState<EnergyUnitDisplay>("MWh");
  const [accessProfileId, setAccessProfileId] = useState<string>("local");
  const [activeTab, setActiveTab] = useState<string>("map");

  const [studies, setStudies] = useState<StudySummary[]>([]);
  const [selectedStudyId, setSelectedStudyId] = useState<string>("");
  const [activeRevision, setActiveRevision] = useState<string>("");
  const [latestRevision, setLatestRevision] = useState<string>("");

  const [revisions, setRevisions] = useState<RevisionItem[]>([]);
  const [studyContext, setStudyContext] = useState<Record<string, unknown> | null>(null);
  const [editorBundle, setEditorBundle] = useState<EditorBundleData | null>(null);
  const [mapBundle, setMapBundle] = useState<MapBundleData | null>(null);

  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [runEvidence, setRunEvidence] = useState<Record<string, unknown> | null>(null);
  const [activeJob, setActiveJob] = useState<JobRecordData | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const t = STRINGS[lang];

  const client = useMemo(() => {
    const profile =
      ACCESS_PROFILES.find((p) => p.id === accessProfileId) || ACCESS_PROFILES[0];
    return new R3ChainClient(apiBaseUrl, profile.headers);
  }, [apiBaseUrl, accessProfileId]);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = selectedStudyId
      ? `${selectedStudyId} | ${t.appTitle}`
      : t.appTitle;
  }, [lang, selectedStudyId, t.appTitle]);

  const loadStudyState = useCallback(
    async (studyId: string, requestedRev?: string) => {
      if (!studyId) return;
      setErrorBanner(null);
      try {
        const [revsRes, ctxRes, edRes] = await Promise.all([
          client.listRevisions(studyId),
          client.getStudyContext(studyId, requestedRev),
          client.getEditorBundle(studyId, requestedRev),
        ]);
        const targetRev = ctxRes.scenario_revision || revsRes.data.latest_revision || "";
        setLatestRevision(revsRes.data.latest_revision);
        setActiveRevision(targetRev);
        setRevisions(revsRes.data.revisions);
        setStudyContext(ctxRes as unknown as Record<string, unknown>);
        setEditorBundle(edRes.data);

        const allRuns: RevisionRunSummary[] = revsRes.data.revisions.flatMap((r) => r.runs);
        const revRuns =
          revsRes.data.revisions.find((r) => r.revision_id === targetRev)?.runs || [];
        const completedRevRuns = revRuns.filter((r) => r.status === "completed");
        const completedAny = allRuns.filter((r) => r.status === "completed");
        const chosenRun =
          completedRevRuns.length > 0
            ? completedRevRuns[completedRevRuns.length - 1].run_id
            : completedAny.length > 0
              ? completedAny[completedAny.length - 1].run_id
              : "";

        setSelectedRunId(chosenRun);
        const mapRes = await client.getStudyMap(
          studyId,
          targetRev || undefined,
          chosenRun || undefined
        );
        setMapBundle(mapRes.data);
      } catch (err) {
        if (err instanceof ApiRequestError) {
          setErrorBanner(
            `${t.common.errorPrefix} (${err.failureClass || err.status}): ${err.message}`
          );
        } else {
          setErrorBanner(err instanceof Error ? err.message : String(err));
        }
        setStudyContext(null);
        setEditorBundle(null);
        setMapBundle(null);
        setRunEvidence(null);
      }
    },
    [client, t.common.errorPrefix]
  );

  useEffect(() => {
    let active = true;
    (async () => {
      setErrorBanner(null);
      try {
        const res = await client.listStudies();
        if (!active) return;
        const list = res.data.studies || [];
        setStudies(list);
        const targetStudy =
          list.find((s) => s.study_id === selectedStudyId)?.study_id ||
          list[0]?.study_id ||
          "";
        setSelectedStudyId(targetStudy);
        if (targetStudy) {
          await loadStudyState(targetStudy);
        } else {
          setStudyContext(null);
          setEditorBundle(null);
          setMapBundle(null);
          setRevisions([]);
        }
      } catch (err) {
        if (!active) return;
        setErrorBanner(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      active = false;
    };
    // Intentionally reload study catalog when client/access scope changes.
  }, [client]);

  useEffect(() => {
    let active = true;
    if (!selectedRunId) {
      setRunEvidence(null);
      return;
    }
    (async () => {
      try {
        const res = await client.getRunEvidence(selectedRunId, lang);
        if (!active) return;
        setRunEvidence(res as unknown as Record<string, unknown>);
      } catch (err) {
        if (!active) return;
        if (err instanceof ApiRequestError) {
          setErrorBanner(
            `${t.common.errorPrefix} (${err.failureClass || err.status}): ${err.message}`
          );
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [selectedRunId, lang, client, t.common.errorPrefix]);

  const handleSelectStudy = (studyId: string) => {
    setSelectedStudyId(studyId);
    setSelectedRunId("");
    setActiveJob(null);
    void loadStudyState(studyId);
  };

  const handleSelectRevision = (revisionId: string) => {
    setActiveRevision(revisionId);
    void loadStudyState(selectedStudyId, revisionId);
  };

  const handleCreateRevision = async (
    parentRevision: string,
    patches: Array<{ op: string; path: string; value?: unknown }>,
    author: string,
    rationale: string
  ): Promise<string | null> => {
    const res = await client.createRevision(selectedStudyId, {
      parent_revision: parentRevision,
      explicit_patch: patches,
      author,
      rationale,
    });
    const revData = res.data.revision as { revision_id?: string } | undefined;
    const nextRev = revData?.revision_id || res.scenario_revision || null;
    if (nextRev) {
      const revsRes = await client.listRevisions(selectedStudyId);
      setRevisions(revsRes.data.revisions);
      setLatestRevision(revsRes.data.latest_revision);
      setActiveRevision(nextRev);
      const [ctxRes, edRes, mapRes] = await Promise.all([
        client.getStudyContext(selectedStudyId, nextRev),
        client.getEditorBundle(selectedStudyId, nextRev),
        client.getStudyMap(selectedStudyId, nextRev),
      ]);
      setStudyContext(ctxRes as unknown as Record<string, unknown>);
      setEditorBundle(edRes.data);
      setMapBundle(mapRes.data);
      // Intentionally keep `selectedRunId` pointing at prior run until recalculated so stale warning displays!
    }
    return nextRev;
  };

  const handleRunCompleted = useCallback(
    async (runId: string) => {
      if (!selectedStudyId) return;
      const revsRes = await client.listRevisions(selectedStudyId);
      setRevisions(revsRes.data.revisions);
      setLatestRevision(revsRes.data.latest_revision);
      setSelectedRunId(runId);
      const mapRes = await client.getStudyMap(
        selectedStudyId,
        activeRevision || undefined,
        runId
      );
      setMapBundle(mapRes.data);
    },
    [client, selectedStudyId, activeRevision]
  );

  const studyMeta = ((studyContext?.data as Record<string, unknown>)?.study || {}) as Record<
    string,
    unknown
  >;
  const assumptions =
    (studyContext?.assumptions_used as AssumptionRow[]) || [];
  const missingInputs =
    (studyContext?.missing_inputs as MissingInputRow[]) || [];
  const wellScenarioIds =
    ((studyContext?.data as Record<string, unknown>)?.well_scenarios as string[]) || [];
  const synthetic = Boolean(studyContext?.synthetic ?? true);
  const studyAreaObj = (studyMeta.study_area as Record<string, unknown>) || {};
  const areaLabel = selectedStudyId
    ? `${String(studyMeta.title || selectedStudyId)} (${String(studyAreaObj.crs || "")})`
    : "";

  const allCompletedRuns = useMemo(
    () => revisions.flatMap((r) => r.runs).filter((r) => r.status === "completed"),
    [revisions]
  );
  const currentRevObj =
    revisions.find((r) => r.revision_id === activeRevision) || null;

  const isStaleResult = Boolean(
    selectedRunId &&
      runEvidence &&
      activeRevision &&
      typeof runEvidence.scenario_revision === "string" &&
      runEvidence.scenario_revision !== activeRevision
  );

  const tabs: TabDefinition[] = [
    {
      id: "map",
      label: t.tabs.map,
      content: (
        <div className="form-stack">
          <MapView t={t} mapBundle={mapBundle} />
          <div className="panel-surface">
            <AssumptionsPanel
              t={t}
              assumptions={assumptions}
              missingInputs={missingInputs}
              detail={detail}
            />
          </div>
        </div>
      ),
    },
    {
      id: "editor",
      label: t.tabs.editor,
      content: (
        <ScenarioEditorView
          t={t}
          bundle={editorBundle}
          assumptions={assumptions}
          missingInputs={missingInputs}
          revisions={revisions}
          activeRevision={activeRevision}
          detail={detail}
          announcerRef={announcerRef}
          onSelectRevision={handleSelectRevision}
          onCreateRevision={handleCreateRevision}
          onNavigateToRunner={() => setActiveTab("runner")}
        />
      ),
    },
    {
      id: "runner",
      label: t.tabs.runner,
      content: (
        <JobRunnerView
          t={t}
          client={client}
          studyId={selectedStudyId}
          activeRevision={activeRevision}
          revisionObj={currentRevObj}
          areaLabel={areaLabel}
          missingInputs={missingInputs}
          availablePolicies={editorBundle?.available_policies || []}
          wellScenarioIds={wellScenarioIds}
          activeJob={activeJob}
          onJobUpdated={setActiveJob}
          onRunCompleted={handleRunCompleted}
        />
      ),
    },
    {
      id: "compare",
      label: t.tabs.compare,
      content: (
        <ComparisonView
          t={t}
          lang={lang}
          client={client}
          runs={allCompletedRuns}
          selectedRunId={selectedRunId}
          onSelectRun={(rid) => {
            setSelectedRunId(rid);
            if (selectedStudyId) {
              void client
                .getStudyMap(selectedStudyId, activeRevision || undefined, rid)
                .then((res) => setMapBundle(res.data))
                .catch(() => undefined);
            }
          }}
          runEvidence={runEvidence}
          energyUnit={energyUnit}
          detail={detail}
        />
      ),
    },
    {
      id: "evidence",
      label: t.tabs.evidence,
      content: (
        <EvidenceView
          t={t}
          lang={lang}
          client={client}
          studyId={selectedStudyId}
          activeRevision={activeRevision}
          selectedRunId={selectedRunId}
          runEvidence={runEvidence}
        />
      ),
    },
  ];

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link visually-hidden">
        {t.skipToMain}
      </a>
      <div ref={announcerRef} aria-live="polite" className="visually-hidden" />

      <StudyHeader
        t={t}
        studies={studies}
        selectedStudyId={selectedStudyId}
        onSelectStudy={handleSelectStudy}
        activeRevision={activeRevision}
        latestRevision={latestRevision}
        areaLabel={areaLabel}
        baselineId={String(studyMeta.baseline_id || "")}
        synthetic={synthetic}
        isStaleResult={isStaleResult}
        lang={lang}
        onChangeLang={setLang}
        detail={detail}
        onChangeDetail={setDetail}
        energyUnit={energyUnit}
        onChangeEnergyUnit={setEnergyUnit}
        accessProfiles={ACCESS_PROFILES}
        accessProfileId={accessProfileId}
        onChangeAccessProfile={setAccessProfileId}
      />

      <main className="main-workspace">
        <h1 id="main-content" tabIndex={-1} style={{ marginBlock: "0 0.75rem", fontSize: "1.4rem" }}>
          {areaLabel || t.appTitle}
        </h1>
        {errorBanner ? (
          <div className="alert alert--fail" role="alert">
            <strong>
              <span aria-hidden="true">✕ </span>
            </strong>
            {errorBanner}
          </div>
        ) : null}

        <WorkspaceTabs
          ariaLabel={t.tabListLabel}
          tabs={tabs}
          activeTab={activeTab}
          onChangeTab={setActiveTab}
        />
      </main>

      <footer className="app-footer">
        <span>{t.common.syntheticNotice}</span>
      </footer>
    </div>
  );
}

export default App;
