export type Language = "en" | "de";
export type DetailLevel = "summary" | "technical";
export type EnergyUnitDisplay = "MWh" | "GJ";

export type FailureClass =
  | "invalid_request"
  | "denied_access"
  | "physical_check_failed"
  | "numerical_non_convergence"
  | "service_fault";

export interface ToolResult<TData = Record<string, unknown>> {
  schema_version: string;
  status: string;
  failure_class: FailureClass | null;
  message: string;
  study_id: string | null;
  scenario_revision: string | null;
  run_id: string | null;
  job_id: string | null;
  input_hash: string | null;
  model_versions: Record<string, string>;
  values: Record<string, unknown>;
  checks: Array<Record<string, unknown>>;
  warnings: string[];
  assumptions_used: AssumptionRow[];
  missing_inputs: MissingInputRow[];
  artifact_refs: string[];
  synthetic: boolean;
  explanation: Record<string, unknown> | null;
  data: TData;
}

export interface StudySummary {
  study_id: string;
  project_id: string;
  title: string;
  objective: string;
  baseline_id: string;
  planning_date: string;
  crs: string;
  synthetic: boolean;
  latest_revision: string;
  runs_count: number;
}

export interface AssumptionRow {
  field_path: string;
  status: "active" | "unknown" | "conflict";
  value: unknown;
  unit: string | null;
  provenance_type: string | null;
  source_id: string | null;
  locator: string | null;
  note: string;
  owner?: string | null;
}

export interface MissingInputRow {
  field_path: string;
  status: string;
  reason: string;
  owner: string | null;
}

export interface DatasetMeta {
  dataset_id: string;
  version: string;
  kind: string;
  title: string;
  source_uri: string;
  checksum: string;
  owner: string;
  allowed_use: string;
  access_group: string;
  quality: string;
  quality_note: string;
  synthetic: boolean;
}

export interface RevisionRunSummary {
  run_id: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  result_hash: string | null;
  is_stale: boolean;
  seed: number;
}

export interface FieldChangeItem {
  field_path: string;
  kind: "added" | "removed" | "changed";
  before: unknown;
  after: unknown;
}

export interface RevisionItem {
  revision_id: string;
  scenario_id: string;
  scenario_hash: string;
  parent_revision_id: string | null;
  created_at: string;
  author: string;
  rationale: string;
  changes: FieldChangeItem[];
  is_latest: boolean;
  runs: RevisionRunSummary[];
}

export interface PolicyOption {
  policy_id: string;
  version: string;
  kind: string;
  status: string;
  policy_hash: string;
  parameters: Record<string, unknown>;
}

export interface EditorBundleData {
  revision: RevisionItem;
  scenario: Record<string, unknown>;
  validation: {
    executable: boolean;
    contains_synthetic_values: boolean;
    issues: Array<{ path: string; severity: string; code: string; message: string }>;
    missing_inputs: MissingInputRow[];
  };
  schema: Record<string, unknown>;
  available_policies: PolicyOption[];
}

export interface MapSummaryRow {
  kind: string;
  id: string;
  label: string;
  status: string;
  details: string;
}

export interface MapFeatureCollection {
  type: "FeatureCollection";
  name: string;
  features: Array<{
    type: "Feature";
    properties: Record<string, unknown>;
    geometry: Record<string, unknown>;
  }>;
}

export interface MapBundleData {
  bbox: [number, number, number, number] | null;
  layers: Record<string, MapFeatureCollection>;
  text_summary: MapSummaryRow[];
  hidden_spatial_layers_count: number;
}

export interface JobRecordData {
  job_id: string;
  study_id: string;
  project_id: string;
  scenario_revision: string;
  principal_id: string;
  access_scope: string;
  status: "queued" | "running" | "completed" | "cancelled" | "failed";
  cancel_requested: boolean;
  current_stage: string;
  completed_stages: number;
  total_stages: number;
  progress_fraction: number;
  run_id: string | null;
  result_ids: string[];
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  error_message: string;
  failure_class: FailureClass | null;
  diagnostics: string[];
}

export interface ExplanationClaimItem {
  claim_id: string;
  claim_type: string;
  subject: string;
  source_ref?: string | null;
  result_id?: string | null;
  check_id?: string | null;
  field_path?: string | null;
  metric_id?: string | null;
  value?: number | null;
  unit?: string | null;
  boundary?: string | null;
  limit?: number | null;
  observed?: number | null;
  technical_status?: string | null;
  evaluation_status?: string | null;
  eligible?: boolean | null;
  assumption_state?: string | null;
  synthetic?: boolean;
  [key: string]: unknown;
}

export interface ExplanationRecordData {
  explanation_id: string;
  kind: string;
  subject: string;
  study_id: string;
  scenario_revision: string;
  run_id: string;
  synthetic: boolean;
  all_claims_accepted: boolean;
  claims: ExplanationClaimItem[];
  check_outcomes: Array<{
    claim_id: string;
    accepted: boolean;
    reasons: string[];
  }>;
}

export interface AccessProfile {
  id: string;
  label_en: string;
  label_de: string;
  headers: Record<string, string>;
}
