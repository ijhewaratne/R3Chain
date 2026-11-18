import type {
  AccessProfile,
  EditorBundleData,
  FailureClass,
  JobRecordData,
  Language,
  MapBundleData,
  RevisionItem,
  StudySummary,
  ToolResult,
} from "../types/api";

export const ACCESS_PROFILES: readonly AccessProfile[] = [
  {
    id: "local",
    label_en: "Local Synthetic Caller (default, no restricted data)",
    label_de: "Lokaler Synthetik-Zugriff (Standard, keine vertraulichen Daten)",
    headers: {},
  },
  {
    id: "admin",
    label_en: "Authorised Project Owner (full access)",
    label_de: "Autorisierte Projektleitung (Vollzugriff)",
    headers: {
      "X-R3CHAIN-Principal": "project-owner",
      "X-R3CHAIN-Projects": "*",
      "X-R3CHAIN-Groups": "*",
      "X-R3CHAIN-Allow-Restricted": "true",
      "X-R3CHAIN-Allow-Synthetic": "true",
    },
  },
  {
    id: "outsider",
    label_en: "External Project Caller (restricted / denied access check)",
    label_de: "Externer Zugriff (Rechte- & Exportprüfung)",
    headers: {
      "X-R3CHAIN-Principal": "outsider",
      "X-R3CHAIN-Projects": "unrelated-project",
      "X-R3CHAIN-Groups": "public",
      "X-R3CHAIN-Allow-Restricted": "false",
      "X-R3CHAIN-Allow-Synthetic": "false",
    },
  },
];

export class ApiRequestError extends Error {
  status: number;
  failureClass: FailureClass | null;
  payload: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    failureClass: FailureClass | null,
    payload: Record<string, unknown>
  ) {
    super(message);
    this.status = status;
    this.failureClass = failureClass;
    this.payload = payload;
  }
}

export class R3ChainClient {
  private baseUrl: string;
  private headers: Record<string, string>;

  constructor(baseUrl = "", headers: Record<string, string> = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.headers = headers;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const mergedHeaders: Record<string, string> = {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...this.headers,
      ...((init.headers as Record<string, string> | undefined) ?? {}),
    };
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: mergedHeaders,
    });
    const text = await res.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = { message: text };
    }
    if (!res.ok) {
      const msg =
        (typeof parsed.message === "string" && parsed.message) ||
        (typeof parsed.detail === "string" && parsed.detail) ||
        `HTTP ${res.status}`;
      const fClass =
        (typeof parsed.failure_class === "string" ? (parsed.failure_class as FailureClass) : null);
      throw new ApiRequestError(msg, res.status, fClass, parsed);
    }
    return parsed as T;
  }

  listStudies(): Promise<ToolResult<{ studies: StudySummary[] }>> {
    return this.request("/studies");
  }

  getStudyContext(
    studyId: string,
    revision?: string
  ): Promise<ToolResult<Record<string, unknown>>> {
    const qs = revision ? `?revision=${encodeURIComponent(revision)}` : "";
    return this.request(`/studies/${encodeURIComponent(studyId)}${qs}`);
  }

  listRevisions(
    studyId: string
  ): Promise<ToolResult<{ latest_revision: string; revisions: RevisionItem[] }>> {
    return this.request(`/studies/${encodeURIComponent(studyId)}/revisions`);
  }

  getEditorBundle(
    studyId: string,
    revision?: string
  ): Promise<ToolResult<EditorBundleData>> {
    const qs = revision ? `?revision=${encodeURIComponent(revision)}` : "";
    return this.request(`/studies/${encodeURIComponent(studyId)}/editor${qs}`);
  }

  getStudyMap(
    studyId: string,
    revision?: string,
    runId?: string
  ): Promise<ToolResult<MapBundleData>> {
    const params = new URLSearchParams();
    if (revision) params.set("revision", revision);
    if (runId) params.set("run_id", runId);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return this.request(`/studies/${encodeURIComponent(studyId)}/map${qs}`);
  }

  createRevision(
    studyId: string,
    body: {
      parent_revision: string;
      explicit_patch: Array<{ op: string; path: string; value?: unknown }>;
      rationale: string;
      author: string;
    }
  ): Promise<ToolResult<Record<string, unknown>>> {
    return this.request(`/studies/${encodeURIComponent(studyId)}/revisions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  startEvaluation(
    studyId: string,
    body: {
      revision?: string;
      uncertainty_policy_id?: string | null;
      seed?: number;
      use_cache?: boolean;
      wait?: boolean;
    }
  ): Promise<ToolResult<{ job: JobRecordData; run?: Record<string, unknown> }>> {
    return this.request(`/studies/${encodeURIComponent(studyId)}/evaluations`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  getJob(jobId: string): Promise<ToolResult<{ job: JobRecordData; result_ids: string[] }>> {
    return this.request(`/jobs/${encodeURIComponent(jobId)}`);
  }

  listJobs(studyId?: string): Promise<ToolResult<{ jobs: JobRecordData[] }>> {
    const qs = studyId ? `?study_id=${encodeURIComponent(studyId)}` : "";
    return this.request(`/jobs${qs}`);
  }

  cancelJob(jobId: string, wait = false): Promise<ToolResult<{ job: JobRecordData }>> {
    return this.request(`/jobs/${encodeURIComponent(jobId)}/cancel?wait=${wait ? "true" : "false"}`, {
      method: "POST",
    });
  }

  evaluateSource(body: {
    source_scenario_id: string;
    operating_point: { requested_mass_flow_kg_s: number; injection_temperature_k: number };
    study_id: string;
    revision?: string;
  }): Promise<ToolResult<Record<string, unknown>>> {
    return this.request("/sources/evaluate", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  getRunEvidence(
    runId: string,
    lang: Language
  ): Promise<ToolResult<Record<string, unknown>>> {
    return this.request(`/runs/${encodeURIComponent(runId)}/evidence?lang=${lang}`);
  }

  replayRun(runId: string): Promise<ToolResult<Record<string, unknown>>> {
    return this.request(`/runs/${encodeURIComponent(runId)}/replay`, {
      method: "POST",
    });
  }

  compareResults(body: {
    result_ids: string[];
    metric_policy_id?: string | null;
    lang: Language;
  }): Promise<ToolResult<Record<string, unknown>>> {
    return this.request("/comparisons", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  searchEvidence(
    studyId: string,
    body: { query: string; revision?: string; filters?: Record<string, unknown> }
  ): Promise<ToolResult<Record<string, unknown>>> {
    return this.request(`/studies/${encodeURIComponent(studyId)}/evidence/search`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  exportReport(body: {
    run_id: string;
    format: "markdown" | "json";
    lang: Language;
  }): Promise<ToolResult<{ content: string; format: string; lang: Language }>> {
    return this.request("/exports", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
}
