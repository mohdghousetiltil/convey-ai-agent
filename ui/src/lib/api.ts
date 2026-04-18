/**
 * API client.
 *
 * Every request automatically attaches the stored Bearer token.
 * On 401, the token is cleared and an `auth:logout` custom event is
 * dispatched so the AuthContext can redirect to login.
 */

import { clearAuth, getClientSlug, getToken, saveAuth } from "./auth";
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
  client_name: string;
  matter: {
    client_name: string;
    volume_folio: string;
    property_address: string;
  };
  run_dir: string;
  corpus_path?: string;
  summary_text: string;
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

export interface ChatCitation {
  file?: string | null;
  page?: number | null;
  quote?: string | null;
}

export interface ChatAnswerPayload {
  answer: string;
  citations: ChatCitation[];
}

export interface AutofillJobPayload {
  job_id: string;
  run_id: string;
  status: "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
  dry_run: boolean;
  skip_review_gate: boolean;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  result?: ReviewRunPayload | null;
}

export interface AnswerUpdatePayload {
  value: ReviewFieldValue;
  needs_review?: boolean;
}

export interface LocalSettingsPayload {
  language: string;
  openAiApiKey: string;
  defaultModelName: string;
  triconveyPath: string;
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
// Core fetch wrapper — injects Bearer token, handles 401 globally
// ---------------------------------------------------------------------------

/** Fired globally when the server returns 401. AuthContext listens for this. */
export const AUTH_LOGOUT_EVENT = "auth:logout";

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 401) {
    clearAuth();
    window.dispatchEvent(new CustomEvent(AUTH_LOGOUT_EVENT));
    throw new Error("Session expired. Please log in again.");
  }

  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = (payload as Record<string, string>).detail ?? (payload as Record<string, string>).message ?? message;
    } catch {
      message = await response.text();
    }
    throw new Error(message || "Request failed.");
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Auth endpoints
// ---------------------------------------------------------------------------

export async function login(
  clientSlug: string,
  email: string,
  password: string,
): Promise<LoginPayload> {
  const payload = await apiRequest<LoginPayload>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_slug: clientSlug, email, password }),
  });
  saveAuth(payload.access_token, payload.user);
  return payload;
}

export async function logout(): Promise<void> {
  try {
    await apiRequest("/auth/logout", { method: "POST" });
  } finally {
    clearAuth();
  }
}

export async function whoami(): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/whoami");
}

export async function getOAuthProviders(): Promise<OAuthProviders> {
  return apiRequest<OAuthProviders>("/auth/oauth/providers");
}

/**
 * Opens the OAuth provider login in a popup window.
 * Returns a promise that resolves when the popup posts back a token,
 * or rejects on error / timeout.
 */
export function loginWithOAuth(provider: "google" | "microsoft"): Promise<LoginPayload> {
  const clientSlug = getClientSlug();
  const startUrl = `${API_BASE}/auth/oauth/${provider}/start?client_slug=${encodeURIComponent(clientSlug)}`;

  return new Promise((resolve, reject) => {
    // 1. Open popup
    const popup = window.open(
      startUrl,
      `convey_oauth_${provider}`,
      "width=520,height=640,scrollbars=yes,resizable=yes",
    );

    if (!popup) {
      reject(new Error("Popup was blocked. Allow popups for this page and try again."));
      return;
    }

    // 2. Backend will do the OAuth dance and redirect back to /api/auth/oauth/{provider}/callback
    //    which returns an HTML page that calls window.opener.postMessage(...)

    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("Login timed out. Please try again."));
    }, 5 * 60 * 1000); // 5 minutes

    function onMessage(event: MessageEvent) {
      // Only accept messages from our own backend origin (localhost).
      const expectedOrigin = new URL(API_BASE).origin;
      if (event.origin !== expectedOrigin) return;

      const data = event.data as { type?: string; token?: string; user?: AuthUser; error?: string };
      if (data.type === "oauth_success" && data.token && data.user) {
        cleanup();
        saveAuth(data.token, data.user);
        resolve({
          access_token: data.token,
          token_type: "bearer",
          expires_in_seconds: 3600,
          user: data.user,
        });
      } else if (data.type === "oauth_error") {
        cleanup();
        reject(new Error(data.error ?? "OAuth login failed."));
      }
    }

    function cleanup() {
      window.clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
      try {
        popup?.close();
      } catch {
        /* ignore */
      }
    }

    window.addEventListener("message", onMessage);
  });
}

// ---------------------------------------------------------------------------
// Run endpoints
// ---------------------------------------------------------------------------

export async function createRun(
  files: File[],
  options?: { useAiReview?: boolean; model?: string; launchConvey?: boolean; triconveyExe?: string | null },
): Promise<ReviewRunPayload> {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  body.append("use_ai_review", String(options?.useAiReview ?? false));
  body.append("model", options?.model ?? "gpt-4.1-mini");
  body.append("launch_convey", String(options?.launchConvey ?? true));
  if (options?.triconveyExe) body.append("triconvey_exe", options.triconveyExe);
  return apiRequest<ReviewRunPayload>("/runs", { method: "POST", body });
}

export async function getRun(runId: string): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}`);
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

export async function askRunQuestion(
  runId: string,
  question: string,
  options?: { model?: string },
): Promise<ChatAnswerPayload> {
  return apiRequest<ChatAnswerPayload>(`/runs/${runId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, model: options?.model ?? "gpt-4.1-mini" }),
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
