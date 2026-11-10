/** API client for the local backend. */


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReviewFieldValue = string | boolean | number | null;

export interface ReviewEvidence {
  file?: string | null;
  page?: number | null;
  quote?: string | null;
  quote_verified?: boolean;
  extractor_note?: string | null;
}

export interface ReviewFieldItem {
  question_id: string;
  label: string;
  expected_type?: string | null;
  answer_strategy?: string | null;
  value: ReviewFieldValue;
  confidence: number;
  needs_review: boolean;
  review_reasons: string[];
  facts_used: string[];
  evidence: ReviewEvidence[];
  options?: string[] | null;
  description?: string | null;
  presentation_hints: Record<string, unknown>;
  ai_review?: Record<string, unknown> | null;
}

export interface ReviewTab {
  tab: string;
  items: ReviewFieldItem[];
}

export interface ReviewRunPayload {
  manifest: {
    run_id: string;
    created_at?: string;
    document_count?: number;
    use_ai_review?: boolean;
    model?: string;
    client_name?: string;
    total_facts?: number;
  };
  status?: "pending" | "running" | "completed" | "failed";
  progress_pct?: number;
  progress_status?: string;
  error_message?: string;
  ai_enhanced?: boolean;
  ai_activated?: boolean;
  client_name: string;
  matter: {
    client_name: string;
    volume_folio: string;
    property_address: string;
    matter_ref?: string | null;
    matter_id?: string | null;
  };
  run_dir: string;
  corpus_path?: string;
  chat_history?: {
    summary?: string;
    turns?: Array<{ role: "user" | "assistant"; content: string }>;
  };
  summary_text: string;
  agent_context?: {
    fact_snapshot: Array<{
      path: string;
      value: ReviewFieldValue;
      confidence: number;
      extractor: string;
      file?: string | null;
      page?: number | null;
    }>;
    unresolved_conflicts: Array<{
      path: string;
      reason?: string | null;
      candidates: Array<{
        value: ReviewFieldValue;
        extractor: string;
        file?: string | null;
        page?: number | null;
        confidence: number;
      }>;
    }>;
  };
  tabs: ReviewTab[];
  metrics: {
    total_questions: number;
    auto_ready: number;
    needs_review: number;
    action_count: number;
    review_gate_required: boolean;
    filled: number;
    failed: number;
    pending_review: number;
  };
  action_plan: Record<string, unknown>;
  execution_report: Record<string, unknown>;
}

export interface RecentRunPayload {
  run_id: string;
  status: string;
  created_at?: string | null;
  completed_at?: string | null;
  time_taken_seconds?: number | null;
  client_name: string;
  volume_folio: string;
  property_address: string;
  matter_id?: string | null;
  matter_ref?: string | null;
  summary_text?: string | null;
  local_only?: boolean;
}

export interface ChatCitation {
  file?: string | null;
  page?: number | null;
  quote?: string | null;
}

export interface ProposedPatch {
  question_id: string;
  new_value: string;
  reason: string;
  status: "pending_approval" | "applied" | "dismissed";
}

export interface FieldAnswerSuggestion {
  question_id: string;
  value: ReviewFieldValue;
  confidence: number;
}

export interface ReasoningStep {
  tool: string;
  input: Record<string, unknown>;
  summary: string;
}

export interface ChatAnswerPayload {
  answer: string;
  citations: ChatCitation[];
  proposed_patches?: ProposedPatch[];
  field_answers?: FieldAnswerSuggestion[];
  reasoning_steps?: ReasoningStep[];
  tool_calls_made?: number;
  confidence_note?: string | null;
  critic_applied?: boolean;
  opening_message?: boolean;
  agent_runs?: Array<{
    provider: string;
    model: string;
    role?: string;
    status: string;
    error?: string;
  }>;
  summary_model?: string | null;
  summary_provider?: string | null;
}

export interface AutofillJobPayload {
  job_id: string;
  run_id: string;
  status: "queued" | "running" | "cancelling" | "awaiting_user" | "completed" | "failed" | "cancelled";
  dry_run: boolean;
  skip_review_gate: boolean;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  result?: ReviewRunPayload | null;
  manual_action?: {
    action: string;
    message: string;
    cta?: string;
  } | null;
}

export interface AutofillActivityEvent {
  index: number;
  ts?: string | null;
  kind: string;
  summary: string;
  payload: Record<string, unknown>;
}

export interface AutofillActivityPayload {
  job_id: string;
  status: AutofillJobPayload["status"];
  cursor: number;
  events: AutofillActivityEvent[];
  latest_screenshot_url?: string | null;
  latest_screenshot_name?: string | null;
}

export interface AnswerUpdatePayload {
  value: ReviewFieldValue;
  needs_review?: boolean;
}

export interface LocalSettingsPayload {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  googleApiKey?: string;
  openRouterApiKey?: string;
  aiProvider: "openai" | "anthropic" | "hybrid" | "google" | "openrouter";
  aiMode: "cost_efficient" | "all_time_best" | "turbo";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
  updateRepository: string;
  includePrereleaseUpdates: boolean;
  autoCheckForUpdates: boolean;
  cloudSyncEnabled: boolean;
  theme: "light" | "dark" | "auto";
}

export interface CopyRulePayload {
  id: string;
  rule_type: string;
  authority_name: string;
  annual_amount: number;
  notes?: string | null;
  is_active: boolean;
  created_by: string;
  created_at?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface CopyRuleUpsertPayload {
  rule_type?: string;
  authority_name: string;
  annual_amount: number;
  notes?: string | null;
  is_active?: boolean;
}

export interface AppInfoPayload {
  name: string;
  publisher: string;
  version: string;
}

export interface CloudSyncStatusPayload {
  enabled: boolean;
  connected: boolean;
  configured: boolean;
  cloud_sync_url: string;
  client_slug?: string | null;
  worker_running: boolean;
  pending_sync_events?: number | null;
  last_synced_at?: string | null;
  detail: string;
}

export interface UpdateStatusPayload {
  enabled: boolean;
  current_version: string;
  update_repository: string;
  checked_at: string;
  update_available: boolean;
  latest_version?: string | null;
  release_name?: string | null;
  release_url?: string | null;
  published_at?: string | null;
  prerelease: boolean;
  installer_asset?: {
    name: string;
    download_url: string;
    size_bytes?: number | null;
  } | null;
  notes: string;
  error?: string | null;
}

export interface UpdateDownloadPayload {
  release: UpdateStatusPayload;
  download: {
    installer_path: string;
    installer_name: string;
    latest_version: string;
    downloaded_at: string;
  };
}

export interface LocalUser {
  user_id: string;
  email: string;
  name: string;
  role: "admin" | "reviewer" | "viewer";
  client_id: string;
  client_slug: string;
  client_name: string;
}

// ---------------------------------------------------------------------------
// Base URL
// ---------------------------------------------------------------------------

function resolveApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (configured) return configured;
  if (typeof window === "undefined") return "http://127.0.0.1:8765/api";
  const origin = window.location.origin;
  if (origin.endsWith(":3000") || origin.endsWith(":5173")) return "http://127.0.0.1:8765/api";
  return `${origin}/api`;
}

export const API_BASE = resolveApiBase();
// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);

  if (!response.ok) {
    let message = response.statusText;
    try {
      const raw = await response.text();
      const payload = raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
      message = (payload?.detail as string) ?? (payload?.message as string) ?? raw ?? message;
    } catch {
      // keep statusText
    }
    throw new Error(message || "Request failed.");
  }

  const raw = await response.text();
  if (!raw) return null as T;
  return JSON.parse(raw) as T;
}

async function apiRequestBlob(path: string, init: RequestInit = {}): Promise<{ blob: Blob; filename?: string }> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const raw = await response.text();
      const payload = raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
      message = (payload?.detail as string) ?? (payload?.message as string) ?? raw ?? message;
    } catch {
      // keep statusText
    }
    throw new Error(message || "Request failed.");
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i);
  const filename = match?.[1] ? decodeURIComponent(match[1]) : match?.[2] || undefined;
  return { blob, filename };
}

// ---------------------------------------------------------------------------
// Current user
// ---------------------------------------------------------------------------

export async function whoami(): Promise<LocalUser> {
  return apiRequest<LocalUser>("/auth/whoami");
}

// ---------------------------------------------------------------------------
// Run endpoints
// ---------------------------------------------------------------------------

export async function createRun(
  files: File[],
  options?: { useAiReview?: boolean; model?: string; triconveyExe?: string | null; reanalyseRunId?: string },
): Promise<ReviewRunPayload> {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  body.append("use_ai_review", String(options?.useAiReview ?? false));
  body.append("model", options?.model ?? "nvidia/nemotron-3-super-120b-a12b:free");
  if (options?.triconveyExe) body.append("triconvey_exe", options.triconveyExe);
  if (options?.reanalyseRunId) body.append("reanalyse_run_id", options.reanalyseRunId);
  return apiRequest<ReviewRunPayload>("/runs", { method: "POST", body });
}

export async function getRun(runId: string): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}`);
}

export async function activateAiAnswers(runId: string): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}/activate-ai`, { method: "POST" });
}

export async function getRecentRuns(limit = 10): Promise<RecentRunPayload[]> {
  return apiRequest<RecentRunPayload[]>(`/runs/recent?limit=${encodeURIComponent(String(limit))}`);
}

export async function getAllRuns(): Promise<RecentRunPayload[]> {
  return apiRequest<RecentRunPayload[]>(`/runs/recent?limit=1000`);
}

export async function deleteRun(runId: string): Promise<void> {
  await apiRequest<null>(`/runs/${runId}`, { method: "DELETE" });
}

export async function saveAnswers(
  runId: string,
  updates: Record<string, AnswerUpdatePayload>,
): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}/answers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ updates }),
  });
}

export async function autofillTriConvey(
  runId: string,
  options?: { dryRun?: boolean; triconveyExe?: string; skipReviewGate?: boolean },
): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}/autofill`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      triconvey_exe: options?.triconveyExe ?? null,
      skip_review_gate: options?.skipReviewGate ?? false,
    }),
  });
}

export async function startAutofillJob(
  runId: string,
  options?: { dryRun?: boolean; triconveyExe?: string; skipReviewGate?: boolean },
): Promise<AutofillJobPayload> {
  return apiRequest<AutofillJobPayload>(`/runs/${runId}/autofill/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      triconvey_exe: options?.triconveyExe ?? null,
      skip_review_gate: options?.skipReviewGate ?? false,
    }),
  });
}

export async function getAutofillJob(jobId: string): Promise<AutofillJobPayload> {
  return apiRequest<AutofillJobPayload>(`/autofill-jobs/${jobId}`);
}

export async function cancelAutofillJob(jobId: string): Promise<AutofillJobPayload> {
  return apiRequest<AutofillJobPayload>(`/autofill-jobs/${jobId}/cancel`, { method: "POST" });
}

export async function continueAutofillJob(jobId: string): Promise<AutofillJobPayload> {
  return apiRequest<AutofillJobPayload>(`/autofill-jobs/${jobId}/continue`, { method: "POST" });
}

export async function getAutofillActivity(jobId: string, cursor = 0): Promise<AutofillActivityPayload> {
  return apiRequest<AutofillActivityPayload>(`/autofill-jobs/${jobId}/activity?cursor=${cursor}`);
}

export async function askRunQuestion(
  runId: string,
  question: string,
  options?: {
    model?: string;
    history?: Array<{ role: "user" | "assistant"; content: string }>;
    mode?: "quick" | "standard" | "thorough";
    aiMode?: "cost_efficient" | "all_time_best" | "turbo";
    sessionId?: string;
    signal?: AbortSignal;
  },
): Promise<ChatAnswerPayload> {
  return apiRequest<ChatAnswerPayload>(`/runs/${runId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: options?.signal,
    body: JSON.stringify({
      question,
      model: options?.model ?? null,
      history: options?.history ?? [],
      mode: options?.mode ?? "standard",
      aiMode: options?.aiMode ?? null,
    }),
  });
}

export async function uploadChatFiles(
  runId: string,
  files: File[],
): Promise<{ uploaded: string[]; message: string; corpus_extraction_started?: boolean }> {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  return apiRequest<{ uploaded: string[]; message: string; corpus_extraction_started?: boolean }>(`/runs/${runId}/chat-files`, {
    method: "POST",
    body,
  });
}

export async function reprocessRun(runId: string): Promise<unknown> {
  return apiRequest(`/runs/${runId}/reprocess`, { method: "POST" });
}

export interface CorpusPendingEntry {
  document_id: string;
  filename: string;
  document_type: string;
  page_count: number;
  pages_processed: number;
  highlights: string[];
}

export interface CorpusState {
  matter_id: string;
  run_id: string;
  document_count: number;
  pending_count: number;
  documents: unknown[];
  pending: CorpusPendingEntry[];
  updated_at: string;
  context_preview: string;
}

export async function getRunCorpus(runId: string): Promise<CorpusState> {
  return apiRequest<CorpusState>(`/runs/${runId}/corpus`);
}

export async function confirmCorpusEntry(
  runId: string,
  documentId: string,
): Promise<{ confirmed: boolean; document_id: string; filename: string; message: string }> {
  return apiRequest(`/runs/${runId}/corpus/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId }),
  });
}

export interface DocumentValidation {
  is_valid: boolean;
  is_suspicious: boolean;
  confidence: number;
  reason: string;
  document_type_guess: string;
  warning_message?: string | null;
}

export interface ProposedDocumentChange {
  question_id: string;
  question_label: string;
  current_value: unknown;
  proposed_value: unknown;
  confidence: number;
  source: string;
  ui_section: string;
}

export interface ProcessDocumentResult {
  document_id: string;
  filename: string;
  doc_path: string | null;
  validation: DocumentValidation;
  corpus_entry: unknown | null;
  proposed_changes: ProposedDocumentChange[];
  error?: string;
  already_exists?: boolean;
  message?: string;
}

export async function processNewChatDocument(
  runId: string,
  file: File,
): Promise<ProcessDocumentResult> {
  const body = new FormData();
  body.append("file", file);
  body.append("filename", file.name);
  return apiRequest<ProcessDocumentResult>(`/runs/${runId}/chat-documents/process`, {
    method: "POST",
    body,
  });
}

export async function applyDocumentChanges(
  runId: string,
  payload: {
    document_id: string;
    doc_path: string;
    corpus_entry: unknown | null;
    approved_change_ids: string[];
    all_changes: ProposedDocumentChange[];
  },
): Promise<{ ok: boolean; applied_changes: string[]; corpus_confirmed: boolean }> {
  return apiRequest(`/runs/${runId}/chat-documents/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function applyAnswerPatches(
  runId: string,
  patches: Array<{ question_id: string; new_value: string; reason: string }>,
): Promise<unknown> {
  return apiRequest(`/runs/${runId}/apply-patches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patches }),
  });
}

export async function resolveTriconveyReference(
  payloadText: string,
): Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }> {
  return apiRequest(`/triconvey/resolve-reference`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload_text: payloadText }),
  });
}

// ---------------------------------------------------------------------------
// Settings (local)
// ---------------------------------------------------------------------------

export async function getSettings(): Promise<LocalSettingsPayload> {
  return apiRequest<LocalSettingsPayload>("/settings");
}

export async function saveSettings(settings: LocalSettingsPayload): Promise<LocalSettingsPayload> {
  return apiRequest<LocalSettingsPayload>("/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

export async function listCopyRules(ruleType = "water_authority"): Promise<CopyRulePayload[]> {
  return apiRequest<CopyRulePayload[]>(`/copy-rules?rule_type=${encodeURIComponent(ruleType)}`);
}

export async function createCopyRule(payload: CopyRuleUpsertPayload): Promise<CopyRulePayload> {
  return apiRequest<CopyRulePayload>("/copy-rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateCopyRule(ruleId: string, payload: CopyRuleUpsertPayload): Promise<CopyRulePayload> {
  return apiRequest<CopyRulePayload>(`/copy-rules/${encodeURIComponent(ruleId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteCopyRule(ruleId: string): Promise<{ ok: boolean; id: string }> {
  return apiRequest<{ ok: boolean; id: string }>(`/copy-rules/${encodeURIComponent(ruleId)}`, {
    method: "DELETE",
  });
}

export interface UseCopyRuleResult {
  ok: boolean;
  row_num: number;
  authority_name: string;
  matched_rule: string;
  amount: string;
  match_score: number;
}

export async function useCopyRuleForRow(runId: string, rowNum: number): Promise<UseCopyRuleResult> {
  return apiRequest<UseCopyRuleResult>(`/runs/${encodeURIComponent(runId)}/authority-rows/${rowNum}/use-copy-rule`, {
    method: "POST",
  });
}

export async function getAppInfo(): Promise<AppInfoPayload> {
  return apiRequest<AppInfoPayload>("/app/info");
}

export async function getCloudSyncStatus(): Promise<CloudSyncStatusPayload> {
  return apiRequest<CloudSyncStatusPayload>("/cloud-sync/status");
}

export async function bootstrapCloudSync(): Promise<CloudSyncStatusPayload> {
  return apiRequest<CloudSyncStatusPayload>("/cloud-sync/bootstrap", {
    method: "POST",
  });
}

export async function checkForUpdates(options?: {
  includePrerelease?: boolean;
  updateRepository?: string;
}): Promise<UpdateStatusPayload> {
  return apiRequest<UpdateStatusPayload>("/app/update/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      include_prerelease: options?.includePrerelease ?? null,
      update_repository: options?.updateRepository ?? null,
    }),
  });
}

export async function downloadUpdateInstaller(options?: {
  includePrerelease?: boolean;
  updateRepository?: string;
}): Promise<UpdateDownloadPayload> {
  return apiRequest<UpdateDownloadPayload>("/app/update/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      include_prerelease: options?.includePrerelease ?? null,
      update_repository: options?.updateRepository ?? null,
    }),
  });
}

export async function reinstallCurrentVersion(options?: {
  updateRepository?: string;
}): Promise<UpdateDownloadPayload> {
  return apiRequest<UpdateDownloadPayload>("/app/update/reinstall", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      update_repository: options?.updateRepository ?? null,
    }),
  });
}

export async function downloadBrainELogs(runId: string): Promise<void> {
  const { blob, filename } = await apiRequestBlob(`/runs/${runId}/brain-e-logs`, {
    method: "GET",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || `brain_e_logs_${runId}.zip`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Smokeball direct-push ─────────────────────────────────────────────────────

export interface SmokeballPushResult {
  matter_id: string;
  success: boolean;
  method: string | null;
  fields_pushed: number;
  warning: string | null;
  error: string | null;
}

export interface SmokeballMatter {
  id: string;
  matterNumber: string;
  description: string;
  matterType?: { name: string } | null;
  clients?: { name: { firstName: string; lastName: string } }[];
  status?: string;
  matterSettlementDate?: string | null;
}

/**
 * Push Section 32 conveyancing fields directly into Smokeball/TriConvey.
 * triConvey.exe must be running and logged in.
 */
export async function pushS32ToSmokeball(
  matterNumber: string,
  answers: Record<string, unknown>,
  runId?: string,
): Promise<SmokeballPushResult> {
  return apiRequest<SmokeballPushResult>("/smokeball/push-s32", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ matter_number: matterNumber, answers, run_id: runId ?? null }),
  });
}

export interface SmokeballMatterSuggestion {
  MatterNumber: string | null;
  Client: string | null;
  OtherSide: string | null;
  Description: string | null;
  Status: string | null;
  DateOpened: string;
}

export interface SmokeballSuggestResponse {
  phrase: string;
  db_exists: boolean;
  error: string | null;
  results: SmokeballMatterSuggestion[];
}

/**
 * Search the local Smokeball SQLite DB for matters matching a volume/folio pair.
 * Does NOT require triConvey.exe to be running.
 */
export async function suggestSmokeballMatters(
  volume: string,
  folio: string,
): Promise<SmokeballSuggestResponse> {
  const params = new URLSearchParams({ volume, folio });
  return apiRequest<SmokeballSuggestResponse>(`/smokeball/suggest-matter?${params}`);
}

/**
 * List all Smokeball matters visible to the logged-in triConvey user.
 * triConvey.exe must be running and logged in.
 */
export async function listSmokeballMatters(): Promise<SmokeballMatter[]> {
  return apiRequest<SmokeballMatter[]>("/smokeball/matters");
}
