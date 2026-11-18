import type { UIStrings } from "../i18n/strings";
import type {
  AccessProfile,
  DetailLevel,
  EnergyUnitDisplay,
  Language,
  StudySummary,
} from "../types/api";

interface StudyHeaderProps {
  t: UIStrings;
  studies: StudySummary[];
  selectedStudyId: string;
  onSelectStudy: (studyId: string) => void;
  activeRevision: string;
  latestRevision: string;
  areaLabel: string;
  baselineId: string;
  synthetic: boolean;
  isStaleResult: boolean;
  lang: Language;
  onChangeLang: (lang: Language) => void;
  detail: DetailLevel;
  onChangeDetail: (detail: DetailLevel) => void;
  energyUnit: EnergyUnitDisplay;
  onChangeEnergyUnit: (unit: EnergyUnitDisplay) => void;
  accessProfiles: readonly AccessProfile[];
  accessProfileId: string;
  onChangeAccessProfile: (id: string) => void;
}

export function StudyHeader(props: StudyHeaderProps) {
  const {
    t,
    studies,
    selectedStudyId,
    onSelectStudy,
    activeRevision,
    latestRevision,
    areaLabel,
    baselineId,
    synthetic,
    isStaleResult,
    lang,
    onChangeLang,
    detail,
    onChangeDetail,
    energyUnit,
    onChangeEnergyUnit,
    accessProfiles,
    accessProfileId,
    onChangeAccessProfile,
  } = props;

  return (
    <header className="study-header">
      <div className="header-grid">
        <div className="header-brand">
          <div style={{ fontSize: "1.35rem", fontWeight: 700, lineHeight: 1.25 }}>
            {t.appTitle}
          </div>
          <div className="header-meta">
            <span>
              <strong>{t.areaLabel}:</strong> {areaLabel || "—"}
            </span>
            <span>
              <strong>{t.baselineLabel}:</strong> {baselineId || "—"}
            </span>
            <span>
              <strong>{t.revisionLabel}:</strong>{" "}
              <code>{activeRevision || latestRevision || "—"}</code>
            </span>
          </div>
        </div>

        <div className="header-controls">
          <div className="control-group">
            <label htmlFor="header-study-select">{t.studyLabel}</label>
            <select
              id="header-study-select"
              value={selectedStudyId}
              onChange={(e) => onSelectStudy(e.target.value)}
            >
              {studies.length === 0 ? (
                <option value="">{t.common.none}</option>
              ) : (
                studies.map((s) => (
                  <option key={s.study_id} value={s.study_id}>
                    {s.title} ({s.study_id})
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="control-group">
            <label htmlFor="header-lang-select">{t.languageLabel}</label>
            <select
              id="header-lang-select"
              value={lang}
              onChange={(e) => onChangeLang(e.target.value as Language)}
            >
              <option value="en">English (EN)</option>
              <option value="de">Deutsch (DE)</option>
            </select>
          </div>

          <div className="control-group">
            <label htmlFor="header-detail-select">{t.detailLabel}</label>
            <select
              id="header-detail-select"
              value={detail}
              onChange={(e) => onChangeDetail(e.target.value as DetailLevel)}
            >
              <option value="summary">{t.detailSummary}</option>
              <option value="technical">{t.detailTechnical}</option>
            </select>
          </div>

          <div className="control-group">
            <label htmlFor="header-unit-select">{t.energyUnitLabel}</label>
            <select
              id="header-unit-select"
              value={energyUnit}
              onChange={(e) => onChangeEnergyUnit(e.target.value as EnergyUnitDisplay)}
            >
              <option value="MWh">EUR / MWh</option>
              <option value="GJ">EUR / GJ</option>
            </select>
          </div>

          <div className="control-group">
            <label htmlFor="header-access-select">{t.accessProfileLabel}</label>
            <select
              id="header-access-select"
              value={accessProfileId}
              onChange={(e) => onChangeAccessProfile(e.target.value)}
            >
              {accessProfiles.map((prof) => (
                <option key={prof.id} value={prof.id}>
                  {lang === "de" ? prof.label_de : prof.label_en}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="banner-strip">
        <span className={`badge ${synthetic ? "badge--synthetic" : "badge--pass"}`}>
          <span aria-hidden="true">{synthetic ? "◆" : "✓"}</span>
          <span>{synthetic ? t.dataStateSynthetic : t.dataStatePilot}</span>
        </span>
        {activeRevision && latestRevision && activeRevision !== latestRevision ? (
          <span className="badge badge--warn">
            <span aria-hidden="true">⚠</span>
            <span>
              {activeRevision} != {latestRevision}
            </span>
          </span>
        ) : null}
      </div>

      {isStaleResult ? (
        <div className="banner-strip">
          <div className="alert alert--warn" role="status">
            <span aria-hidden="true">⚠ </span>
            {t.staleBanner}
          </div>
        </div>
      ) : null}
    </header>
  );
}
