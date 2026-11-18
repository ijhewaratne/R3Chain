import type { Language } from "../types/api";

export interface UIStrings {
  skipToMain: string;
  appTitle: string;
  studyLabel: string;
  revisionLabel: string;
  areaLabel: string;
  baselineLabel: string;
  dataStateSynthetic: string;
  dataStatePilot: string;
  languageLabel: string;
  detailLabel: string;
  detailSummary: string;
  detailTechnical: string;
  energyUnitLabel: string;
  accessProfileLabel: string;
  staleBanner: string;
  tabListLabel: string;
  tabs: {
    map: string;
    editor: string;
    runner: string;
    compare: string;
    evidence: string;
  };
  common: {
    refresh: string;
    loading: string;
    none: string;
    statusPass: string;
    statusWarn: string;
    statusFail: string;
    statusUnknown: string;
    errorPrefix: string;
    syntheticNotice: string;
    close: string;
  };
  map: {
    heading: string;
    layersLegend: string;
    layerNames: Record<string, string>;
    inspectorHeading: string;
    inspectorEmpty: string;
    textTableCaption: string;
    colKind: string;
    colId: string;
    colLabel: string;
    colStatus: string;
    colDetails: string;
    hiddenLayersNote: string;
  };
  assumptions: {
    heading: string;
    subtitle: string;
    searchLabel: string;
    searchPlaceholder: string;
    statusFilterLabel: string;
    allStatuses: string;
    statusActive: string;
    statusUnknown: string;
    statusConflict: string;
    tableCaption: string;
    colField: string;
    colStatus: string;
    colValue: string;
    colSourceKind: string;
    colEvidence: string;
    colOwnerNote: string;
  };
  editor: {
    heading: string;
    schemaHeading: string;
    schemaHint: string;
    authorLabel: string;
    authorHint: string;
    rationaleLabel: string;
    rationaleHint: string;
    rationaleRequiredError: string;
    numericError: string;
    pendingChangesHeading: string;
    saveRevisionButton: string;
    savingRevision: string;
    savedRevisionSuccess: string;
    runNowButton: string;
    branchHistoryHeading: string;
    colRevision: string;
    colParent: string;
    colAuthor: string;
    colRationale: string;
    colChanges: string;
    colRuns: string;
  };
  runner: {
    heading: string;
    precheckHeading: string;
    policyLabel: string;
    seedLabel: string;
    startButton: string;
    cancelButton: string;
    progressLabel: string;
    stageLabel: string;
    elapsedLabel: string;
    statusLabel: string;
    sourceDiagHeading: string;
    sourceSelectLabel: string;
    flowLabel: string;
    injTempLabel: string;
    evaluateSourceButton: string;
  };
  compare: {
    heading: string;
    runSelectLabel: string;
    eligibilityHeading: string;
    eligibilityNote: string;
    eligibilityCaption: string;
    costRankingHeading: string;
    costCaption: string;
    crossCompareHeading: string;
    crossCompareButton: string;
    chartTitle: string;
    driversHeading: string;
    resilienceHeading: string;
    resilienceCaption: string;
    colCandidate: string;
    colTechnical: string;
    colEvaluation: string;
    colEligible: string;
    colReasons: string;
    colMetric: string;
    colLcohGeo: string;
    colLcohSys: string;
    colCapex: string;
    colDeltaBaseline: string;
    colGeoHeat: string;
    colBackupHeat: string;
    colUncertainty: string;
    colDriver: string;
    colRho: string;
    colSwitches: string;
  };
  evidence: {
    heading: string;
    explanationsHeading: string;
    claimVerified: string;
    claimRejected: string;
    inspectEvidence: string;
    modalTitle: string;
    searchHeading: string;
    searchLabel: string;
    searchButton: string;
    exportHeading: string;
    exportMarkdownButton: string;
    exportJsonButton: string;
    downloadButton: string;
    replayHeading: string;
    replayButton: string;
    replayPassed: string;
  };
}

export const STRINGS: Record<Language, UIStrings> = {
  en: {
    skipToMain: "Skip to main workspace",
    appTitle: "R3CHAIN Planner Workspace",
    studyLabel: "Active study",
    revisionLabel: "Revision",
    areaLabel: "Study area",
    baselineLabel: "Baseline",
    dataStateSynthetic: "SYNTHETIC TEST DATA — Not pilot evidence",
    dataStatePilot: "PILOT STUDY",
    languageLabel: "Language",
    detailLabel: "Presentation detail",
    detailSummary: "Summary (key assumptions retained)",
    detailTechnical: "Technical detail",
    energyUnitLabel: "Heat cost display",
    accessProfileLabel: "Access scope",
    staleBanner:
      "Stale result warning: The displayed evaluation belongs to an older revision than the current scenario revision. Re-run or select the latest revision.",
    tabListLabel: "Workspace views",
    tabs: {
      map: "Map & Sites",
      editor: "Assumptions & Editor",
      runner: "Evaluate & Jobs",
      compare: "Eligibility & Comparison",
      evidence: "Explanations & Export",
    },
    common: {
      refresh: "Refresh",
      loading: "Loading study workspace…",
      none: "None",
      statusPass: "Pass",
      statusWarn: "Warning",
      statusFail: "Failed",
      statusUnknown: "Unknown",
      errorPrefix: "Error",
      syntheticNotice:
        "Synthetic label active: Every result uses test assumptions/models and cannot enter pilot rankings.",
      close: "Close",
    },
    map: {
      heading: "Spatial overview and screening map",
      layersLegend: "Map layers",
      layerNames: {
        study_area: "Study area",
        sites: "Candidate sites & pads",
        targets: "Geological targets",
        restrictions: "Restrictions",
        corridors: "Corridors",
        barriers: "Crossing barriers",
        demand: "Demand points",
        connections: "Network connections",
        routes: "Generated routes",
        candidates: "Evaluated candidates",
      },
      inspectorHeading: "Selected map feature",
      inspectorEmpty: "Select a site, route, target, or connection on the map or in the text table below.",
      textTableCaption: "Text explanation of map entities and screening results",
      colKind: "Kind",
      colId: "Identifier",
      colLabel: "Label",
      colStatus: "Status",
      colDetails: "Key details & limiting notes",
      hiddenLayersNote: "spatial layer(s) withheld under current dataset access permissions.",
    },
    assumptions: {
      heading: "Mandatory Assumptions & Open Gaps Register",
      subtitle:
        "Shown automatically regardless of presentation mode. Distinguishes synthetic/imported/guideline provenance, unknowns (with owner), and conflicts.",
      searchLabel: "Filter assumptions by path, source, or note",
      searchPlaceholder: "e.g. temperature, discount_rate, tar...",
      statusFilterLabel: "Assumption status",
      allStatuses: "All statuses",
      statusActive: "Active / Known",
      statusUnknown: "Unknown (gap)",
      statusConflict: "Conflict",
      tableCaption: "Engineering assumptions and unresolved gaps for this revision",
      colField: "Field path",
      colStatus: "Status",
      colValue: "Value & unit",
      colSourceKind: "Evidence type",
      colEvidence: "Source version & locator",
      colOwnerNote: "Owner / note",
    },
    editor: {
      heading: "Schema-Generated Scenario Editor & Revision Branching",
      schemaHeading: "Editable parameters (generated from Scenario JSON Schema)",
      schemaHint:
        "Forms inspect the exported Scenario JSON Schema (types, units, bounds). Any change creates a new immutable revision and flags prior runs as stale.",
      authorLabel: "Editor / reviewer name",
      authorHint: "Recorded in the immutable revision audit trail.",
      rationaleLabel: "Reason for revision change",
      rationaleHint: "State briefly why you changed these inputs (required).",
      rationaleRequiredError: "Enter a short rationale explaining this scenario revision.",
      numericError: "Enter a valid finite number within schema bounds.",
      pendingChangesHeading: "Pending field patches before commit",
      saveRevisionButton: "Create new immutable revision",
      savingRevision: "Creating revision…",
      savedRevisionSuccess: "Created new scenario revision:",
      runNowButton: "Evaluate new revision now",
      branchHistoryHeading: "Revision history & attached runs",
      colRevision: "Revision",
      colParent: "Parent",
      colAuthor: "Author",
      colRationale: "Rationale",
      colChanges: "Field changes",
      colRuns: "Runs & staleness",
    },
    runner: {
      heading: "Pre-Calculation Review, Job Runner & Cancellation",
      precheckHeading: "Pre-execution check before launching evaluation",
      policyLabel: "Uncertainty / mode policy",
      seedLabel: "Random seed (common random numbers)",
      startButton: "Start background evaluation",
      cancelButton: "Cancel running job",
      progressLabel: "Evaluation progress",
      stageLabel: "Active / last stage",
      elapsedLabel: "Elapsed time",
      statusLabel: "Job state",
      sourceDiagHeading: "Single-source physical vs numerical diagnostic (Port check)",
      sourceSelectLabel: "Well / source scenario",
      flowLabel: "Requested mass flow (kg/s)",
      injTempLabel: "Injection temperature (K)",
      evaluateSourceButton: "Evaluate single source point",
    },
    compare: {
      heading: "Candidate Eligibility Before Cost Ranking",
      runSelectLabel: "Completed evaluation run",
      eligibilityHeading: "1. Eligibility, screening, and failure reasons (shown first)",
      eligibilityNote:
        "Candidates that fail physical checks, screening, or numerical convergence never appear in cost rankings.",
      eligibilityCaption: "All candidates with technical status, evaluation status, eligibility, and failure checks",
      costRankingHeading: "2. Comparable Cost & Technical Performance Ranking (Eligible Only)",
      costCaption: "Eligible candidates ordered by the declared comparable boundary metric",
      crossCompareHeading: "3. Compare across runs / scenario revisions",
      crossCompareButton: "Compare selected run vs previous run",
      chartTitle: "LCOH comparison across candidates (with complete table above)",
      driversHeading: "Uncertainty stability, decision drivers & resilience",
      resilienceHeading: "Deterministic outage resilience cases (not probabilities)",
      resilienceCaption: "Unserved heat, affected hours, and peak deficit across outage scenarios",
      colCandidate: "Candidate",
      colTechnical: "Technical status",
      colEvaluation: "Evaluation status",
      colEligible: "Ranking eligibility",
      colReasons: "Reasons / limiting check",
      colMetric: "Primary metric",
      colLcohGeo: "LCOH Geo delivered",
      colLcohSys: "LCOH System delivered",
      colCapex: "CAPEX",
      colDeltaBaseline: "PV delta vs baseline",
      colGeoHeat: "Geo heat delivered",
      colBackupHeat: "Backup heat",
      colUncertainty: "Uncertainty / stability",
      colDriver: "Uncertain variable",
      colRho: "Spearman rho",
      colSwitches: "Switches winner?",
    },
    evidence: {
      heading: "Verified Explanations, Evidence Search & Replay",
      explanationsHeading: "Claim-checked explanations (fixed template wording, zero LLM)",
      claimVerified: "Verified against stored records",
      claimRejected: "Rejected by claim check",
      inspectEvidence: "Inspect linked records",
      modalTitle: "Linked Record & Claim Audit",
      searchHeading: "Permission-filtered project evidence search",
      searchLabel: "Search accessible documents and assumption locators",
      searchButton: "Search evidence",
      exportHeading: "Authorised Evidence Report Export",
      exportMarkdownButton: "Generate Markdown report",
      exportJsonButton: "Generate JSON report",
      downloadButton: "Download exported file",
      replayHeading: "Independent Replay Verification (A17)",
      replayButton: "Verify deterministic replay without assistant",
      replayPassed: "Replay verified identically against frozen scenario inputs.",
    },
  },
  de: {
    skipToMain: "Zum Hauptarbeitsbereich springen",
    appTitle: "R3CHAIN Planungsarbeitsbereich",
    studyLabel: "Aktive Studie",
    revisionLabel: "Revision",
    areaLabel: "Untersuchungsgebiet",
    baselineLabel: "Referenzfall (Baseline)",
    dataStateSynthetic: "SYNTHETISCHE TESTDATEN — Keine Pilotnachweise",
    dataStatePilot: "PILOTSTUDIE",
    languageLabel: "Sprache",
    detailLabel: "Darstellungsdetail",
    detailSummary: "Zusammenfassung (Pflichtannahmen bleiben sichtbar)",
    detailTechnical: "Technisches Detail",
    energyUnitLabel: "Wärmekosten-Einheit",
    accessProfileLabel: "Zugriffsprofil",
    staleBanner:
      "Hinweis zu veralteten Ergebnissen: Die angezeigte Berechnung gehört zu einer älteren Szenariorevision. Bitte neu berechnen oder neueste Revision wählen.",
    tabListLabel: "Arbeitsbereiche",
    tabs: {
      map: "Karte & Standorte",
      editor: "Annahmen & Editor",
      runner: "Berechnung & Jobs",
      compare: "Zulässigkeit & Vergleich",
      evidence: "Erklärungen & Export",
    },
    common: {
      refresh: "Aktualisieren",
      loading: "Studienarbeitsbereich wird geladen…",
      none: "Keine",
      statusPass: "Bestanden",
      statusWarn: "Warnung",
      statusFail: "Fehlgeschlagen",
      statusUnknown: "Unbekannt",
      errorPrefix: "Fehler",
      syntheticNotice:
        "Synthetische Kennzeichnung aktiv: Alle Ergebnisse beruhen auf Testdaten/-modellen und dürfen nicht in das Pilot-Ranking eingehen.",
      close: "Schließen",
    },
    map: {
      heading: "Räumliche Übersicht und Standort-Screening",
      layersLegend: "Kartenebenen",
      layerNames: {
        study_area: "Untersuchungsgebiet",
        sites: "Standorte & Bohrplätze",
        targets: "Geologische Zielgebiete",
        restrictions: "Restriktionen",
        corridors: "Trassenkorridore",
        barriers: "Querungshindernisse",
        demand: "Wärmeabnehmer",
        connections: "Netzanschlusspunkte",
        routes: "Generierte Trassen",
        candidates: "Bewertete Varianten",
      },
      inspectorHeading: "Ausgewähltes Kartenobjekt",
      inspectorEmpty:
        "Wählen Sie einen Standort, eine Trasse oder ein Zielgebiet auf der Karte oder in der Texttabelle.",
      textTableCaption: "Textbeschreibung aller Kartenobjekte und Screening-Ergebnisse",
      colKind: "Typ",
      colId: "Kennung",
      colLabel: "Bezeichnung",
      colStatus: "Status",
      colDetails: "Wesentliche Angaben & Grenzen",
      hiddenLayersNote: "räumliche Ebene(n) durch aktuelle Zugriffsrechte ausgeblendet.",
    },
    assumptions: {
      heading: "Verpflichtendes Annahmen- & Lückenregister",
      subtitle:
        "Wird in jeder Darstellungsstufe automatisch gezeigt. Unterscheidet synthetische/importierte/Richtwert-Quellen, offene Lücken (mit Zuständigkeit) und Konflikte.",
      searchLabel: "Annahmen nach Pfad, Quelle oder Hinweis filtern",
      searchPlaceholder: "z. B. temperature, discount_rate, tar...",
      statusFilterLabel: "Status der Annahme",
      allStatuses: "Alle Status",
      statusActive: "Aktiv / Bekannt",
      statusUnknown: "Unbekannt (Datenlücke)",
      statusConflict: "Konflikt",
      tableCaption: "Technische und wirtschaftliche Annahmen dieser Revision",
      colField: "Feldpfad",
      colStatus: "Status",
      colValue: "Wert & Einheit",
      colSourceKind: "Nachweisart",
      colEvidence: "Quellenversion & Fundstelle",
      colOwnerNote: "Zuständigkeit / Hinweis",
    },
    editor: {
      heading: "Schemagenerierter Szenario-Editor & Revisionsverzweigung",
      schemaHeading: "Bearbeitbare Parameter (aus dem Szenario-JSON-Schema generiert)",
      schemaHint:
        "Formulare werden aus dem exportierten Szenario-JSON-Schema (Typen, Einheiten, Grenzen) erzeugt. Jede Änderung erzeugt eine unveränderliche neue Revision.",
      authorLabel: "Name Bearbeiter / Prüfer",
      authorHint: "Wird im Revisionsprotokoll festgehalten.",
      rationaleLabel: "Begründung der Szenarioänderung",
      rationaleHint: "Kurze Begründung angeben, warum diese Eingaben geändert wurden (Pflichtfeld).",
      rationaleRequiredError: "Bitte eine kurze Begründung für die neue Revision angeben.",
      numericError: "Bitte eine gültige endliche Zahl innerhalb der Schemagrenzen eingeben.",
      pendingChangesHeading: "Vorgemerkte Feldänderungen vor Speicherung",
      saveRevisionButton: "Neue unveränderliche Revision speichern",
      savingRevision: "Revision wird gespeichert…",
      savedRevisionSuccess: "Neue Szenariorevision gespeichert:",
      runNowButton: "Neue Revision jetzt berechnen",
      branchHistoryHeading: "Revisionsverlauf & verknüpfte Läufe",
      colRevision: "Revision",
      colParent: "Vorgänger",
      colAuthor: "Autor",
      colRationale: "Begründung",
      colChanges: "Feldänderungen",
      colRuns: "Läufe & Aktualität",
    },
    runner: {
      heading: "Vorprüfung, Hintergrundberechnung & Abbruch",
      precheckHeading: "Prüfbericht vor Rechenstart",
      policyLabel: "Unsicherheits- / Modus-Policy",
      seedLabel: "Zufalls-Seed (Common Random Numbers)",
      startButton: "Hintergrundberechnung starten",
      cancelButton: "Laufenden Job abbrechen",
      progressLabel: "Berechnungsfortschritt",
      stageLabel: "Aktuelle / letzte Stufe",
      elapsedLabel: "Laufzeit",
      statusLabel: "Job-Status",
      sourceDiagHeading: "Einzelquellen-Diagnose: Physikalisch vs. Numerisch (Port-Test)",
      sourceSelectLabel: "Quellenszenario",
      flowLabel: "Angeforderter Massenstrom (kg/s)",
      injTempLabel: "Injektionstemperatur (K)",
      evaluateSourceButton: "Quellenbetriebspunkt prüfen",
    },
    compare: {
      heading: "Zulässigkeitsprüfung vor Kosten-Ranking",
      runSelectLabel: "Abgeschlossener Berechnungslauf",
      eligibilityHeading: "1. Zulässigkeit, Screening und Ausschlussgründe (zuerst angezeigt)",
      eligibilityNote:
        "Varianten mit fehlgeschlagenen physikalischen Prüfungen, Screening-Ausschluss oder Nichtkonvergenz erscheinen nie im Kosten-Ranking.",
      eligibilityCaption:
        "Alle Varianten mit technischem Status, Auswertungsstatus, Zulässigkeit und Fehlergründen",
      costRankingHeading: "2. Vergleichbares Kosten- und Leistungs-Ranking (nur zulässige Varianten)",
      costCaption: "Zulässige Varianten nach vereinbarter Vergleichskennzahl und Systemgrenze",
      crossCompareHeading: "3. Vergleich über verschiedene Läufe / Revisionen",
      crossCompareButton: "Ausgewählten Lauf mit Vorgängerlauf vergleichen",
      chartTitle: "Wärmegestehungskosten (LCOH) nach Variante (mit vollständiger Datentabelle)",
      driversHeading: "Unsicherheitsstabilität, Entscheidungstreiber & Resilienz",
      resilienceHeading: "Deterministische Ausfallfälle (keine Wahrscheinlichkeiten)",
      resilienceCaption: "Ungedeckte Wärme, Ausfallstunden und maximale Unterdeckung je Ausfallfall",
      colCandidate: "Variante",
      colTechnical: "Technischer Status",
      colEvaluation: "Auswertungsstatus",
      colEligible: "Ranking-Zulässigkeit",
      colReasons: "Gründe / begrenzende Prüfung",
      colMetric: "Leitkennzahl",
      colLcohGeo: "LCOH Geo geliefert",
      colLcohSys: "LCOH System geliefert",
      colCapex: "Investition (CAPEX)",
      colDeltaBaseline: "Barwert-Delta vs. Baseline",
      colGeoHeat: "Geo-Wärme geliefert",
      colBackupHeat: "Backup-Wärme",
      colUncertainty: "Unsicherheit / Stabilität",
      colDriver: "Unsichere Größe",
      colRho: "Spearman-Rho",
      colSwitches: "Gewinnerwechsel?",
    },
    evidence: {
      heading: "Geprüfte Erklärungen, Quellensuche & Replay",
      explanationsHeading: "Anspruchsgeprüfte Erklärungen (feste Textbausteine ohne LLM)",
      claimVerified: "Gegen gespeicherte Ergebnisse geprüft",
      claimRejected: "Von Anspruchs-Check abgelehnt",
      inspectEvidence: "Verknüpfte Nachweise öffnen",
      modalTitle: "Verknüpfte Nachweise & Prüfprotokoll",
      searchHeading: "Rechtegefilterte Projektevidenz-Suche",
      searchLabel: "Zugängliche Dokumente und Annahmen durchsuchen",
      searchButton: "Evidenz durchsuchen",
      exportHeading: "Autorisierter Evidenzbericht-Export",
      exportMarkdownButton: "Markdown-Bericht erzeugen",
      exportJsonButton: "JSON-Bericht erzeugen",
      downloadButton: "Exportierte Datei herunterladen",
      replayHeading: "Unabhängige Replay-Verifikation (A17)",
      replayButton: "Deterministisches Replay ohne Assistent prüfen",
      replayPassed: "Replay reproduziert die gespeicherten Ergebnisse identisch.",
    },
  },
};
