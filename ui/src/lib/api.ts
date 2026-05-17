/**
 * API client.
 *
 * Every request automatically attaches the stored Bearer token.
 * On 401, the token is cleared and an `auth:logout` custom event is
 * dispatched so the AuthContext can redirect to login.
 */

import { clearAuth, DEFAULT_AUTH_LIFETIME_SECONDS, getClientSlug, getToken, saveAuth } from "./auth";
import type { AuthUser } from "./auth";

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

export interface LoginPayload {
  access_token: string;
  token_type: string;
  expires_in_seconds: number;
  user: AuthUser;
}

export interface OAuthProviders {
  google: { configured: boolean };
  microsoft: { configured: boolean };
}

export interface PersistedAuthState {
  access_token: string;
  expires_at: number;
  user: AuthUser;
}

interface PendingOAuthLogin {
  provider: "google" | "microsoft";
  pollKey: string;
  createdAt: number;
  token?: string;
  user?: AuthUser;
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
const REMOTE_AUTH_BASE = ((import.meta.env.VITE_REMOTE_AUTH_BASE_URL as string | undefined) ?? "https://convey-ai-agent-production.up.railway.app/api").replace(/\/$/, "");
const OAUTH_PENDING_KEY = "convey_pending_oauth";

async function remoteAuthRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${REMOTE_AUTH_BASE}${path}`, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = (payload as Record<string, string>).detail ?? (payload as Record<string, string>).message ?? message;
    } catch {
      try {
        message = await response.text();
      } catch {
        // keep default
      }
    }
    throw new Error(message || "Remote auth request failed.");
  }
  return response.json() as Promise<T>;
}

function getStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    try {
      return window.sessionStorage;
    } catch {
      return null;
    }
  }
}

function normalizeLoopbackHost(hostname: string): string {
  if (hostname === "127.0.0.1" || hostname === "localhost") {
    return "loopback";
  }
  return hostname;
}

function isTrustedPopupOrigin(origin: string): boolean {
  try {
    const expected = new URL(API_BASE).origin;
    const actualUrl = new URL(origin);
    const expectedUrl = new URL(expected);
    return (
      actualUrl.protocol === expectedUrl.protocol &&
      actualUrl.port === expectedUrl.port &&
      normalizeLoopbackHost(actualUrl.hostname) === normalizeLoopbackHost(expectedUrl.hostname)
    );
  } catch {
    return false;
  }
}

function loadPendingOAuth(): PendingOAuthLogin | null {
  const storage = getStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(OAUTH_PENDING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingOAuthLogin>;
    if (
      (parsed.provider !== "google" && parsed.provider !== "microsoft") ||
      typeof parsed.pollKey !== "string" ||
      !parsed.pollKey ||
      typeof parsed.createdAt !== "number"
    ) {
      storage.removeItem(OAUTH_PENDING_KEY);
      return null;
    }
    return {
      provider: parsed.provider,
      pollKey: parsed.pollKey,
      createdAt: parsed.createdAt,
      token: typeof parsed.token === "string" ? parsed.token : undefined,
      user: parsed.user as AuthUser | undefined,
    };
  } catch {
    storage.removeItem(OAUTH_PENDING_KEY);
    return null;
  }
}

function savePendingOAuth(pending: PendingOAuthLogin): void {
  const storage = getStorage();
  if (!storage) return;
  storage.setItem(OAUTH_PENDING_KEY, JSON.stringify(pending));
}

function clearPendingOAuth(): void {
  const storage = getStorage();
  if (!storage) return;
  storage.removeItem(OAUTH_PENDING_KEY);
}

// ---------------------------------------------------------------------------
// Core fetch wrapper — injects Bearer token, handles 401 globally
// ---------------------------------------------------------------------------

/** Fired globally when the server returns 401. AuthContext listens for this. */
export const AUTH_LOGOUT_EVENT = "auth:logout";

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  // Desktop OAuth can race the very first authenticated request after the
  // browser callback. If we still have a pending OAuth marker, give the
  // backend one brief retry window before treating a 401 as a real logout.
  if (response.status === 401 && token && loadPendingOAuth()) {
    await new Promise((resolve) => window.setTimeout(resolve, 800));
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  }

  if (response.status === 401) {
    const isLoginAttempt = path === "/auth/login";
    const shouldForceLogout = !isLoginAttempt && Boolean(token);

    let message = "Session expired. Please log in again.";
    try {
      const raw = await response.text();
      const payload = raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
      message = (payload?.detail as string) ?? (payload?.message as string) ?? raw ?? message;
    } catch {
      // keep default
    }

    if (shouldForceLogout) {
      clearAuth();
      window.dispatchEvent(new CustomEvent(AUTH_LOGOUT_EVENT));
    }

    throw new Error(message || (shouldForceLogout ? "Session expired. Please log in again." : "Login failed."));
  }

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

  if (token && loadPendingOAuth()) {
    clearPendingOAuth();
  }

  const raw = await response.text();
  if (!raw) return null as T;
  return JSON.parse(raw) as T;
}

async function apiRequestBlob(path: string, init: RequestInit = {}): Promise<{ blob: Blob; filename?: string }> {
  const token = getToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (response.status === 401) {
    const isLoginAttempt = path === "/auth/login";
    const shouldForceLogout = !isLoginAttempt && Boolean(token);
    if (shouldForceLogout) {
      clearAuth();
      window.dispatchEvent(new CustomEvent(AUTH_LOGOUT_EVENT));
    }
    throw new Error("Session expired. Please log in again.");
  }
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
// Auth endpoints
// ---------------------------------------------------------------------------

export async function login(
  email: string,
  password: string,
  clientSlug = "",
): Promise<LoginPayload> {
  const payload = await apiRequest<LoginPayload>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_slug: clientSlug, email, password }),
  });
  saveAuth(payload.access_token, payload.user);
  return payload;
}

export async function register(
  name: string,
  companyName: string,
  email: string,
  password: string,
  activationKey: string,
): Promise<LoginPayload> {
  const payload = await apiRequest<LoginPayload>("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, company_name: companyName, email, password, activation_key: activationKey }),
  });
  saveAuth(payload.access_token, payload.user);
  return payload;
}

export async function logout(): Promise<void> {
  try {
    await apiRequest("/auth/logout", { method: "POST" });
  } finally {
    try {
      await fetch(`${API_BASE}/auth/state`, { method: "DELETE" });
    } catch {
      // Best-effort cleanup for local desktop auth restore state.
    }
    clearAuth();
  }
}

export async function whoami(): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/whoami");
}

export async function getOAuthProviders(): Promise<OAuthProviders> {
  return remoteAuthRequest<OAuthProviders>("/auth/oauth/providers");
}

async function getOAuthStart(
  provider: "google" | "microsoft",
  clientSlug: string,
): Promise<{ auth_url: string; provider: string; poll_key: string }> {
  const params = new URLSearchParams();
  if (clientSlug) params.set("client_slug", clientSlug);
  if (typeof window !== "undefined" && window.location.origin) {
    params.set("opener_origin", window.location.origin);
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return remoteAuthRequest<{ auth_url: string; provider: string; poll_key: string }>(
    `/auth/oauth/${provider}/start${suffix}`,
  );
}

async function pollOAuthResult(
  provider: "google" | "microsoft",
  pollKey: string,
): Promise<LoginPayload | null> {
  const result = await remoteAuthRequest<{ ready: boolean; token?: string; user?: AuthUser }>(
    `/auth/oauth/${provider}/poll?key=${encodeURIComponent(pollKey)}`,
  );
  if (!result.ready || !result.token || !result.user) return null;
  return {
    access_token: result.token,
    token_type: "bearer",
    expires_in_seconds: DEFAULT_AUTH_LIFETIME_SECONDS,
    user: result.user,
  };
}

async function acceptRemoteLogin(remoteAccessToken: string): Promise<LoginPayload> {
  const payload = await apiRequest<LoginPayload>("/auth/remote-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remote_access_token: remoteAccessToken }),
  });
  saveAuth(payload.access_token, payload.user);
  return payload;
}

async function waitForAuthReady(payload: LoginPayload): Promise<boolean> {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}/auth/whoami`, {
        headers: {
          Authorization: `Bearer ${payload.access_token}`,
        },
      });
      if (response.ok) {
        return true;
      }
    } catch {
      // Retry; backend may still be finishing the login transaction.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  return false;
}

export async function resumePendingOAuthLogin(): Promise<LoginPayload | null> {
  const pending = loadPendingOAuth();
  if (!pending) return null;
  const maxAgeMs = 10 * 60 * 1000;
  if (Date.now() - pending.createdAt > maxAgeMs) {
    clearPendingOAuth();
    return null;
  }
  try {
    let payload: LoginPayload | null = null;
    if (pending.token && pending.user) {
      payload = {
        access_token: pending.token,
        token_type: "bearer",
        expires_in_seconds: DEFAULT_AUTH_LIFETIME_SECONDS,
        user: pending.user,
      };
    } else {
      payload = await pollOAuthResult(pending.provider, pending.pollKey);
      if (payload) {
        savePendingOAuth({
          ...pending,
          token: payload.access_token,
          user: payload.user,
        });
      }
    }
    if (!payload) return null;
    const localPayload = await acceptRemoteLogin(payload.access_token);
    const ready = await waitForAuthReady(localPayload);
    if (!ready) return null;
    clearPendingOAuth();
    return localPayload;
  } catch {
    return null;
  }
}

export async function loadPersistedAuthState(): Promise<PersistedAuthState | null> {
  try {
    const response = await fetch(`${API_BASE}/auth/state`);
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as PersistedAuthState | null;
  } catch {
    return null;
  }
}

/**
 * Sign in with an OAuth provider.
 *
 * Strategy (works in both pywebview desktop and regular browser):
 * 1. Get auth_url + poll_key from the backend.
 * 2. Open the auth URL: try pywebview bridge → try window.open popup → fall back to new tab.
 * 3. Start polling /oauth/{provider}/poll every second (always).
 * 4. Also listen for postMessage from a popup (browser mode fallback).
 * 5. Whichever completes first (poll result OR postMessage) resolves the promise.
 */
export function loginWithOAuth(provider: "google" | "microsoft"): Promise<LoginPayload | null> {
  const clientSlug = getClientSlug();

  return new Promise((resolve, reject) => {
    let settled = false;
    let pollInterval = 0;
    let popup: Window | null = null;

    function settle(payload: LoginPayload) {
      if (settled) return;
      settled = true;
      window.clearInterval(pollInterval);
      window.removeEventListener("focus", triggerPoll);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("message", onMessage);
      try { popup?.close(); } catch { /* ignore */ }
      resolve(payload);
    }

    function fail(err: Error) {
      if (settled) return;
      settled = true;
      window.clearInterval(pollInterval);
      window.removeEventListener("focus", triggerPoll);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("message", onMessage);
      try { popup?.close(); } catch { /* ignore */ }
      clearPendingOAuth();
      reject(err);
    }

    // postMessage listener — works when the auth page opens in a real popup
    function onMessage(event: MessageEvent) {
      if (!isTrustedPopupOrigin(event.origin)) return;
      const data = event.data as { type?: string; token?: string; user?: AuthUser; error?: string };
      if (data.type === "oauth_success" && data.token && data.user) {
        const payload: LoginPayload = {
          access_token: data.token,
          token_type: "bearer",
          expires_in_seconds: DEFAULT_AUTH_LIFETIME_SECONDS,
          user: data.user,
        };
        savePendingOAuth({
          provider,
          pollKey,
          createdAt: Date.now(),
          token: payload.access_token,
          user: payload.user,
        });
        void (async () => {
          const localPayload = await acceptRemoteLogin(payload.access_token);
          const ready = await waitForAuthReady(localPayload);
          if (ready) {
            settle(localPayload);
          } else {
            fail(new Error("Login timed out. Please try again."));
          }
        })();
      } else if (data.type === "oauth_error") {
        fail(new Error(data.error ?? "OAuth login failed."));
      }
    }
    window.addEventListener("message", onMessage);

    let pollKey = "";
    let pollInFlight = false;

    async function triggerPoll() {
      if (settled || !pollKey || pollInFlight) return;
      pollInFlight = true;
      try {
        const payload = await pollOAuthResult(provider, pollKey);
        if (payload) {
          savePendingOAuth({
            provider,
            pollKey,
            createdAt: Date.now(),
            token: payload.access_token,
            user: payload.user,
          });
          const localPayload = await acceptRemoteLogin(payload.access_token);
          const ready = await waitForAuthReady(localPayload);
          if (ready) {
            settle(localPayload);
          }
        }
      } catch {
        // Keep waiting; transient network/visibility issues are expected here.
      } finally {
        pollInFlight = false;
      }
    }

    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        void triggerPoll();
      }
    }

    void (async () => {
      let authUrl = "";

      try {
        const started = await getOAuthStart(provider, clientSlug);
        authUrl = started.auth_url;
        pollKey = started.poll_key;
        savePendingOAuth({ provider, pollKey, createdAt: Date.now() });
      } catch (e) {
        fail(e instanceof Error ? e : new Error("Failed to start OAuth flow."));
        return;
      }

      // ── Open the auth URL ─────────────────────────────────────────────────
      // Detect whether we are running inside pywebview (desktop .exe).
      // window.pywebview is injected by pywebview before the page loads.
      const pywebview = (window as unknown as { pywebview?: { api?: { open_browser?: (u: string) => void } } }).pywebview;
      const isDesktop = Boolean(pywebview);

      // Try 1: pywebview bridge — opens the URL in the OS default browser.
      if (isDesktop) {
        try {
          pywebview?.api?.open_browser?.(authUrl);
        } catch { /* bridge failed, but we are still in desktop mode — do NOT fall through to window.open */ }
        // NEVER call window.open() in pywebview. Even if open_browser failed,
        // window.open() would open a SECOND pywebview window that starts its own
        // OAuth flow with the same state token.  When that second window's callback
        // fires (with an error because the state was already consumed by the system
        // browser), it postMessages {type:"oauth_error"} back here, causing fail()
        // to run and clear the localStorage pending-key before polling can retrieve
        // the result — leaving the user permanently stuck on the login screen.
        //
        // In desktop mode, the background pending-OAuth recovery loop is the
        // source of truth for actually switching the app into the authenticated
        // state. Resolve this click-flow immediately so the UI does not remain
        // blocked on a long-lived promise while the external browser completes.
        resolve(null);
        return;
      }

      // Try 2: popup window — browser-only mode (not inside pywebview).
      if (!isDesktop && !settled) {
        popup = window.open(authUrl, `convey_oauth_${provider}`, "width=520,height=640,scrollbars=yes,resizable=yes");
        // If popup was blocked, open in a new tab as last resort.
        if (!popup) {
          window.open(authUrl, "_blank");
        }
      }

      // ── Poll every second (always runs regardless of how browser was opened) ──
      const TIMEOUT_MS = 5 * 60 * 1000;
      let elapsed = 0;
      window.addEventListener("focus", triggerPoll);
      document.addEventListener("visibilitychange", handleVisibilityChange);
      void triggerPoll();
      pollInterval = window.setInterval(async () => {
        if (settled) { window.clearInterval(pollInterval); return; }
        elapsed += 1000;
        if (elapsed >= TIMEOUT_MS) {
          fail(new Error("Login timed out. Please try again."));
          return;
        }
        await triggerPoll();
      }, 1000);
    })();
  });
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
// Settings (no auth required — local settings)
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
