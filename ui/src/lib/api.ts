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

export interface AnswerUpdatePayload {
  value: ReviewFieldValue;
  needs_review?: boolean;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8765/api";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail ?? payload.message ?? message;
    } catch {
      message = await response.text();
    }
    throw new Error(message || "Request failed.");
  }
  return response.json() as Promise<T>;
}

export async function createRun(files: File[], options?: { useAiReview?: boolean; model?: string }): Promise<ReviewRunPayload> {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  body.append("use_ai_review", String(options?.useAiReview ?? false));
  body.append("model", options?.model ?? "gpt-4.1-mini");
  return apiRequest<ReviewRunPayload>("/runs", {
    method: "POST",
    body,
  });
}

export async function getRun(runId: string): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}`);
}

export async function saveAnswers(runId: string, updates: Record<string, AnswerUpdatePayload>): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}/answers`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ updates }),
  });
}

export async function autofillTriConvey(
  runId: string,
  options?: { dryRun?: boolean; triconveyExe?: string; skipReviewGate?: boolean },
): Promise<ReviewRunPayload> {
  return apiRequest<ReviewRunPayload>(`/runs/${runId}/autofill`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      dry_run: options?.dryRun ?? false,
      triconvey_exe: options?.triconveyExe ?? null,
      skip_review_gate: options?.skipReviewGate ?? false,
    }),
  });
}
