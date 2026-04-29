import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleStop,
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  Paperclip,
  Pencil,
  Send,
  ShieldCheck,
  Sparkles,
  Upload,
  User,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import {
  ChatAnswerPayload,
  CorpusPendingEntry,
  ProcessDocumentResult,
  ProposedDocumentChange,
  ProposedPatch,
  ReasoningStep,
  applyDocumentChanges,
  confirmCorpusEntry,
  getRunCorpus,
  processNewChatDocument,
  uploadChatFiles,
} from "../lib/api";

type Mode = "quick" | "standard" | "thorough";

/** Whether this chatbot is scoped to a specific matter or the dashboard. */
export type ChatbotContext = "matter" | "dashboard";

interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

interface ConflictPreview {
  path: string;
  reason?: string | null;
  candidates: Array<{
    value: string | boolean | number | null;
    extractor: string;
    file?: string | null;
    page?: number | null;
    confidence: number;
  }>;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  attachments?: string[];
  citations?: ChatAnswerPayload["citations"];
  proposed_patches?: ProposedPatch[];
  reasoning_steps?: ReasoningStep[];
  tool_calls_made?: number;
  confidence_note?: string | null;
  critic_applied?: boolean;
  patchStates?: Record<string, "pending" | "applied" | "dismissed">;
  agent_runs?: ChatAnswerPayload["agent_runs"];
  summary_model?: string | null;
  summary_provider?: string | null;
  /** If true this message is a re-review confirmation prompt */
  isReviewConfirm?: boolean;
}

const REVIEW_AGAIN_PATTERNS = [
  /\breview\s+(the\s+)?matter\s+again\b/i,
  /\bre[-\s]?analys[ei]s?\b/i,
  /\bre[-\s]?extract\b/i,
  /\bstart\s+over\b/i,
  /\bredo\s+(the\s+)?(analysis|extraction|review)\b/i,
  /\brun\s+(the\s+)?(analysis|extraction|review)\s+again\b/i,
  /\brefresh\s+(the\s+)?matter\b/i,
];

function isReviewAgainRequest(text: string): boolean {
  return REVIEW_AGAIN_PATTERNS.some((pattern) => pattern.test(text));
}

interface ChatbotProps {
  isOpen: boolean;
  onClose: () => void;
  onAsk: (
    question: string,
    history: HistoryTurn[],
    mode: Mode,
    signal?: AbortSignal,
    sessionId?: string,
  ) => Promise<ChatAnswerPayload>;
  onApplyPatch?: (questionId: string, newValue: string, reason: string) => Promise<void>;
  /** Called when the user confirms they want to re-analyse the matter. */
  onReviewAgain?: () => Promise<void>;
  /** Called after a successful reprocess so the parent can refresh the run payload. */
  onRunReprocessed?: () => void;
  initialConflicts?: ConflictPreview[];
  initialTurns?: HistoryTurn[];
  runId?: string;
  userName?: string | null;
  suppressMotion?: boolean;
  chatContext?: ChatbotContext;
  onResolveTriconveyReference?: (payloadText: string) => Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }>;
}

function stableDropStamp(content: string): number {
  let hash = 0;
  for (let i = 0; i < content.length; i += 1) {
    hash = ((hash << 5) - hash + content.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

function makeReferenceFile(content: string): File {
  const stamp = stableDropStamp(content);
  return new File([content], `triconvey-drop-${stamp}.json`, {
    type: "application/json",
    lastModified: stamp,
  });
}

function extractLocalPaths(...rawValues: string[]): string[] {
  const paths = new Set<string>();
  const pushMatches = (text: string) => {
    for (const match of text.matchAll(/file:\/\/\/[^\s"'<>]+/gi)) {
      paths.add(match[0]);
    }
    for (const match of text.matchAll(/@?[A-Za-z]:\\[^\r\n"<>|?*]+/g)) {
      paths.add(match[0].replace(/^@+/, ""));
    }
  };
  for (const raw of rawValues) {
    const text = raw.trim();
    if (!text) continue;
    pushMatches(text);
    for (const token of text.split(/\s+/)) {
      const cleaned = token.trim().replace(/^@+/, "");
      if (cleaned.startsWith("file:///") || /^[A-Za-z]:\\/.test(cleaned)) {
        paths.add(cleaned);
      }
    }
  }
  return Array.from(paths);
}

function isTriconveyReferenceName(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    lower.endsWith(".smokeball.tmp") ||
    lower.endsWith(".smokeball.json") ||
    /^triconvey-drop-\d+\.json$/.test(lower) ||
    lower.endsWith("triconvey-drop.json")
  );
}

function extractDroppedFilePaths(files: File[]): string[] {
  const paths = new Set<string>();
  for (const file of files) {
    const candidate = (file as File & { path?: string }).path;
    if (typeof candidate === "string" && candidate.trim()) {
      paths.add(candidate.trim());
    }
  }
  return Array.from(paths);
}

function parseDroppedReferenceText(
  rawPlain: string,
  rawUriList = "",
  rawHtml = "",
): { localPaths?: string[]; matterPayload?: string } | null {
  const matterPayload = rawPlain.trim().includes('"MatterId"') ? rawPlain.trim() : undefined;
  const localPaths = extractLocalPaths(rawPlain, rawUriList, rawHtml);
  if (!matterPayload && localPaths.length === 0) return null;
  return { matterPayload, localPaths: localPaths.length ? localPaths : undefined };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** A pending file attachment in the chatbot — may be a real PDF or a TriConvey reference. */
interface ChatUploadItem {
  file: File;
  /** Human-readable label (resolved PDF names replace the raw reference filename). */
  displayName?: string;
  /** Secondary line shown under the chip. */
  subtitle?: string;
  /** True while we are fetching the TriConvey reference resolution. */
  resolving?: boolean;
}

function isSupportedChatUpload(file: File): boolean {
  const name = file.name.toLowerCase();
  return file.type === "application/pdf" || name.endsWith(".pdf") || isTriconveyReferenceName(name);
}

// ---------------------------------------------------------------------------
// Agent pipeline stages shown while waiting for a response
// ---------------------------------------------------------------------------

type AgentStage = { label: string; icon: React.ElementType; detail: string };

const MATTER_STAGES: AgentStage[] = [
  { label: "Reading documents",   icon: FileText,    detail: "Scanning uploaded matter files" },
  { label: "Extracting facts",    icon: Wrench,      detail: "Pulling relevant facts and figures" },
  { label: "Cross-checking",      icon: ShieldCheck, detail: "Verifying against known sources" },
  { label: "Composing answer",    icon: BrainCircuit,detail: "Synthesising findings" },
];

const DASHBOARD_STAGES: AgentStage[] = [
  { label: "Thinking",  icon: BrainCircuit, detail: "Processing your question" },
  { label: "Searching", icon: Wrench,       detail: "Looking up relevant context" },
  { label: "Answering", icon: Sparkles,     detail: "Composing response" },
];

const UPLOAD_STAGES: AgentStage[] = [
  { label: "Uploading files",    icon: Paperclip,   detail: "Sending documents to server" },
  { label: "Processing PDFs",    icon: FileText,    detail: "Parsing and indexing content" },
  { label: "Extracting fields",  icon: Wrench,      detail: "Running extraction pipeline" },
  { label: "Updating matter",    icon: CheckCircle2,detail: "Merging new facts into the matter" },
];

// ---------------------------------------------------------------------------

function buildWelcomeMessage(userName?: string | null, chatContext?: ChatbotContext): Message {
  const name = userName?.trim() ? userName.trim().split(" ")[0] : null;
  const greeting = name ? `Hi ${name}!` : "Hi there!";
  if (chatContext === "dashboard") {
    return {
      id: "welcome",
      role: "assistant",
      text: `${greeting} I'm your workspace assistant. Ask me about your recent matters, how to use this app, or anything else. How can I help?`,
    };
  }
  return {
    id: "welcome",
    role: "assistant",
    text: `${greeting} I'm your AI agent for this matter. I've processed your files and I'm ready to cross-check facts, compare sources, or suggest corrections.\n\nYou can also attach new documents using the 📎 button — I'll extract the relevant information and update the matter fields automatically.\n\nHow can I help today?`,
  };
}

const MODES: { id: Mode; label: string }[] = [
  { id: "quick", label: "Basic" },
  { id: "standard", label: "Normal" },
  { id: "thorough", label: "Deep" },
];

function buildConflictIntro(conflicts: ConflictPreview[]): string | null {
  if (!conflicts.length) return null;
  const lines = conflicts.slice(0, 5).map((conflict) => {
    const field = humanizeFieldId(conflict.path);
    const files = Array.from(new Set(conflict.candidates.map(c => c.file).filter(Boolean)));
    if (files.length > 1) {
      return `- ${field}: Disagreement between "${files[0]}" and "${files[1]}"`;
    }
    return `- ${field}: Multiple values suggested by automated extractors`;
  });
  const count = conflicts.length;
  return `I've identified ${count} unresolved conflict${count !== 1 ? "s" : ""} across the matter documents. For instance:\n\n${lines.join("\n")}\n\nWhich of these would you like to resolve first?`;
}

function loadFromSession(runId: string): Message[] | null {
  try {
    const raw = sessionStorage.getItem(`chatbot_${runId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Message[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function saveToSession(runId: string, messages: Message[]) {
  try {
    sessionStorage.setItem(`chatbot_${runId}`, JSON.stringify(messages));
  } catch {
    // Ignore session storage failures.
  }
}

function humanizeFieldId(qid: string): string {
  return qid
    .replace(/^sec32_/, "")
    .replace(/^policy_\d+_/, "")
    .replace(/_/g, " ")
    .replace(/\b(\d+)\b/g, "#$1")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function shortReason(reason: string): string {
  const firstSentence = (reason || "").split(/\.\s/)[0].trim();
  return firstSentence.length > 100 ? `${firstSentence.slice(0, 97)}...` : firstSentence;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CitationCard({ citation }: { citation: NonNullable<ChatAnswerPayload["citations"]>[number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card text-[0.73rem] shadow-sm">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 px-2.5 py-2 text-left transition-colors hover:bg-accent"
      >
        <FileText className="h-3 w-3 shrink-0 text-primary/60" />
        <span className="flex-1 truncate font-semibold text-foreground">
          {citation.file || "Unknown file"}
          {citation.page ? ` - p.${citation.page}` : ""}
        </span>
        {open ? <ChevronUp className="h-3 w-3 text-muted-foreground" /> : <ChevronDown className="h-3 w-3 text-muted-foreground" />}
      </button>
      {open && citation.quote ? (
        <p className="border-t border-border px-2.5 pb-2 pt-1.5 italic leading-relaxed text-muted-foreground">
          "{citation.quote}"
        </p>
      ) : null}
    </div>
  );
}

function CorpusConfirmCard({
  entry,
  confirming,
  onConfirm,
  onDismiss,
}: {
  entry: CorpusPendingEntry;
  confirming: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  const pageNote =
    entry.pages_processed < entry.page_count
      ? ` (first ${entry.pages_processed} of ${entry.page_count} pages)`
      : "";
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 space-y-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-blue-800">
          <FileText className="h-4 w-4 shrink-0" />
          <span>New document extracted: {entry.filename}{pageNote}</span>
        </div>
        <button onClick={onDismiss} className="text-blue-400 hover:text-blue-600 shrink-0">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-blue-600">Extracted information</p>
        <ul className="space-y-0.5 text-blue-900">
          {entry.highlights.map((h, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <span className="mt-0.5 text-blue-400">•</span>
              <span>{h}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-blue-600">
        Confirm to add this information to the matter corpus so it can be used in answers.
      </p>
      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          disabled={confirming}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {confirming ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
          {confirming ? "Adding…" : "Confirm & add to corpus"}
        </button>
        <button
          onClick={onDismiss}
          className="rounded-lg border border-blue-300 px-4 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function DocumentWarningCard({
  filename,
  reason,
  warningMessage,
  onProceed,
  onIgnore,
  proceeding,
}: {
  filename: string;
  reason: string;
  warningMessage: string | null | undefined;
  onProceed: () => void;
  onIgnore: () => void;
  proceeding: boolean;
}) {
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 space-y-3 text-sm">
      <div className="flex items-start gap-2 font-semibold text-amber-800">
        <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
        <span>This might be the wrong document: <span className="font-normal italic">{filename}</span></span>
      </div>
      <p className="text-amber-900 text-xs">
        {warningMessage || reason || "This document does not appear to be a conveyancing document for this matter."}
      </p>
      <p className="text-xs text-amber-700">Would you like me to proceed with this document anyway?</p>
      <div className="flex gap-2">
        <button
          onClick={onProceed}
          disabled={proceeding}
          className="flex items-center gap-1.5 rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {proceeding ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
          {proceeding ? "Processing…" : "Yes, proceed"}
        </button>
        <button
          onClick={onIgnore}
          className="rounded-lg border border-amber-300 px-4 py-1.5 text-xs font-semibold text-amber-700 hover:bg-amber-100"
        >
          Ignore document
        </button>
      </div>
    </div>
  );
}

function ProposedChangesCard({
  filename,
  changes,
  onApprove,
  onDismiss,
  applying,
}: {
  filename: string;
  changes: ProposedDocumentChange[];
  onApprove: (approvedIds: string[]) => void;
  onDismiss: () => void;
  applying: boolean;
}) {
  const [selected, setSelected] = React.useState<Set<string>>(
    () => new Set(changes.map((c) => c.question_id))
  );

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const formatVal = (v: unknown) => {
    if (v === null || v === undefined) return <span className="italic text-gray-400">empty</span>;
    if (typeof v === "boolean") return v ? "Yes" : "No";
    return String(v);
  };

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 space-y-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-emerald-800">
          <FileText className="h-4 w-4 shrink-0" />
          <span>New information from: {filename}</span>
        </div>
        <button onClick={onDismiss} className="text-emerald-400 hover:text-emerald-600">
          <X className="h-4 w-4" />
        </button>
      </div>
      {changes.length === 0 ? (
        <p className="text-xs text-emerald-700">No new or changed information found in this document.</p>
      ) : (
        <>
          <p className="text-xs text-emerald-700">
            Select the information to add to this matter. Deselect anything that looks incorrect.
          </p>
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {changes.map((change) => (
              <label
                key={change.question_id}
                className="flex items-start gap-2.5 cursor-pointer group"
              >
                <input
                  type="checkbox"
                  checked={selected.has(change.question_id)}
                  onChange={() => toggle(change.question_id)}
                  className="mt-0.5 h-3.5 w-3.5 accent-emerald-600"
                />
                <div className="flex-1 min-w-0">
                  <span className="font-medium text-emerald-900">{change.question_label || change.question_id}</span>
                  {change.current_value !== null && change.current_value !== undefined ? (
                    <div className="text-xs text-gray-500 truncate">
                      Was: {formatVal(change.current_value)} → Now: <span className="text-emerald-700 font-medium">{formatVal(change.proposed_value)}</span>
                    </div>
                  ) : (
                    <div className="text-xs text-emerald-700 truncate">
                      New: <span className="font-medium">{formatVal(change.proposed_value)}</span>
                    </div>
                  )}
                  {change.source && (
                    <div className="text-[10px] text-gray-400 truncate">{change.source}</div>
                  )}
                </div>
              </label>
            ))}
          </div>
          <div className="flex gap-2 pt-1">
            <button
              onClick={() => onApprove([...selected])}
              disabled={applying || selected.size === 0}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {applying ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
              {applying ? "Applying…" : `Apply ${selected.size} change${selected.size !== 1 ? "s" : ""}`}
            </button>
            <button
              onClick={onDismiss}
              className="rounded-lg border border-emerald-300 px-4 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100"
            >
              Dismiss
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function PatchCard({
  patch,
  state,
  onApply,
  onDismiss,
}: {
  patch: ProposedPatch;
  state: "pending" | "applied" | "dismissed";
  onApply: () => void;
  onDismiss: () => void;
}) {
  const label = humanizeFieldId(patch.question_id);
  const brief = shortReason(patch.reason);
  const isApplied = state === "applied";
  const isDismissed = state === "dismissed";

  return (
    <div
      className={[
        "overflow-hidden rounded-xl border transition-all",
        isApplied
          ? "border-emerald-200 bg-emerald-50/60"
          : isDismissed
            ? "border-border bg-muted opacity-50"
            : "border-amber-200 bg-amber-50/70",
      ].join(" ")}
    >
      <div className="flex items-center gap-3 px-3 py-2.5">
        <AlertTriangle className={["h-3.5 w-3.5 shrink-0", isApplied ? "text-emerald-500" : "text-amber-500"].join(" ")} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.78rem] font-semibold text-foreground">{label}</p>
          <p className="text-[0.76rem] font-bold text-foreground">{patch.new_value}</p>
          {brief ? <p className="mt-0.5 truncate text-[0.7rem] text-muted-foreground">{brief}</p> : null}
        </div>
        {state === "pending" ? (
          <div className="flex shrink-0 gap-1">
            <button
              onClick={onApply}
              className="flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-[0.7rem] font-semibold text-white transition-colors hover:bg-emerald-700"
            >
              <CheckCircle2 className="h-3 w-3" />
              Apply
            </button>
            <button
              onClick={onDismiss}
              className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[0.7rem] font-semibold text-muted-foreground transition-colors hover:bg-accent"
            >
              <XCircle className="h-3 w-3" />
            </button>
          </div>
        ) : (
          <span className={["text-[0.68rem] font-bold uppercase tracking-wider", isApplied ? "text-emerald-600" : "text-muted-foreground"].join(" ")}>
            {isApplied ? "Applied" : "Dismissed"}
          </span>
        )}
      </div>
    </div>
  );
}

/** Multi-layer agent pipeline shown while waiting for a response. */
function AgentPipeline({
  stages,
  activeIndex,
  suppressMotion,
}: {
  stages: AgentStage[];
  activeIndex: number;
  suppressMotion: boolean;
}) {
  const stage = stages[activeIndex] || stages[0];

  return (
    <div className="flex items-center gap-3 py-1">
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10">
        {suppressMotion ? (
          <Loader2 className="h-3 w-3 text-primary" />
        ) : (
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
          >
            <Loader2 className="h-3 w-3 text-primary" />
          </motion.div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <AnimatePresence mode="wait">
          <motion.div
            key={stage.label}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.2 }}
            className="flex items-center gap-2"
          >
            <span className="text-[0.8rem] font-semibold text-foreground truncate">
              {stage.label}
            </span>
            <span className="text-[0.72rem] text-muted-foreground hidden sm:inline whitespace-nowrap">
              — {stage.detail}
            </span>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

type ThinkingPhase = "uploading" | "reprocessing" | "thinking";

function ThinkingIndicator({
  mode,
  phase,
  chatContext,
  suppressMotion = false,
}: {
  mode: Mode;
  phase: ThinkingPhase;
  chatContext: ChatbotContext;
  suppressMotion?: boolean;
}) {
  const [stageIndex, setStageIndex] = useState(0);
  const [elapsedSecs, setElapsedSecs] = useState(0);

  const stages: AgentStage[] = useMemo(() => {
    if (phase === "uploading") return UPLOAD_STAGES;
    return chatContext === "dashboard" ? DASHBOARD_STAGES : MATTER_STAGES;
  }, [phase, chatContext]);

  useEffect(() => {
    setStageIndex(0);
    setElapsedSecs(0);
  }, [stages]);

  // Advance stage every 3.5 s
  useEffect(() => {
    const timer = window.setInterval(() => {
      setStageIndex((i) => Math.min(i + 1, stages.length - 1));
    }, 3500);
    return () => window.clearInterval(timer);
  }, [stages]);

  // Elapsed seconds counter
  useEffect(() => {
    const timer = window.setInterval(() => setElapsedSecs((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const wrapper = (
    <div className="flex gap-2.5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl bg-foreground shadow-sm mt-0.5">
        <Bot className="h-3.5 w-3.5 text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <AgentPipeline stages={stages} activeIndex={stageIndex} suppressMotion={suppressMotion} />
        <p className="px-0.5 text-[0.68rem] font-semibold text-muted-foreground/60">{elapsedSecs}s elapsed</p>
      </div>
    </div>
  );

  if (suppressMotion) return wrapper;
  return <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>{wrapper}</motion.div>;
}

/** A small chip showing a pending attachment. */
function AttachmentChip({ name, onRemove }: { name: string; onRemove: () => void }) {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/8 px-2.5 py-1 text-[0.72rem] font-semibold text-primary">
      <FileText className="h-3 w-3 shrink-0" />
      <span className="max-w-[120px] truncate">{name}</span>
      <button onClick={onRemove} className="ml-0.5 rounded text-primary/60 hover:text-primary">
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

function MessageBubble({
  msg,
  onApplyPatch,
  onDismissPatch,
  onEdit,
  suppressMotion,
}: {
  msg: Message;
  onApplyPatch: (msgId: string, questionId: string) => void;
  onDismissPatch: (msgId: string, questionId: string) => void;
  onEdit?: (msgId: string, text: string) => void;
  onConfirmReview?: () => void;
  onDismissReview?: (msgId: string) => void;
  suppressMotion?: boolean;
}) {
  const isUser = msg.role === "user";
  const [citationsOpen, setCitationsOpen] = useState(false);

  const content = (
    <>
      <div className={["mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl shadow-sm", isUser ? "bg-muted" : "bg-primary/10"].join(" ")}>
        {isUser ? <User className="h-3.5 w-3.5 text-muted-foreground" /> : <Bot className="h-3.5 w-3.5 text-primary" />}
      </div>

      <div className={`flex max-w-[88%] flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
        {/* Attachment chips on user messages */}
        {isUser && msg.attachments && msg.attachments.length > 0 ? (
          <div className="flex flex-wrap gap-1 justify-end">
            {msg.attachments.map((name) => (
              <div key={name} className="inline-flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/8 px-2 py-0.5 text-[0.7rem] font-semibold text-primary">
                <FileText className="h-2.5 w-2.5" />
                <span className="max-w-[100px] truncate">{name}</span>
              </div>
            ))}
          </div>
        ) : null}

        <div className="flex items-start gap-1.5">
          {isUser && onEdit ? (
            <button
              onClick={() => onEdit(msg.id, msg.text)}
              className="mt-2 shrink-0 rounded-lg p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover/bubble:opacity-100"
              title="Edit and resend"
            >
              <Pencil className="h-3 w-3" />
            </button>
          ) : null}
          <div
            className={[
              "rounded-2xl px-4 py-3 text-[0.84rem] leading-relaxed shadow-sm transition-all",
              isUser
                ? "rounded-tr-sm bg-linear-to-br from-primary to-indigo-600 text-white shadow-lg shadow-primary/10"
                : "rounded-tl-sm border border-border/50 bg-card/90 backdrop-blur-sm text-foreground",
            ].join(" ")}
          >
            <p className="whitespace-pre-wrap">{msg.text}</p>
          </div>
        </div>

        {/* Re-review confirmation buttons */}
        {!isUser && msg.isReviewConfirm ? (
          <div className="flex gap-2 px-1">
            <button
              onClick={onConfirmReview}
              className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-[0.75rem] font-semibold text-white transition-colors hover:bg-primary/90"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              Yes, re-analyse
            </button>
            <button
              onClick={() => onDismissReview?.(msg.id)}
              className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-1.5 text-[0.75rem] font-semibold text-muted-foreground transition-colors hover:bg-accent"
            >
              <XCircle className="h-3.5 w-3.5" />
              No, cancel
            </button>
          </div>
        ) : null}

        {!isUser && (msg.tool_calls_made || msg.critic_applied || msg.summary_model) ? (
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            {msg.tool_calls_made ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[0.67rem] font-medium text-muted-foreground">
                <Wrench className="h-2.5 w-2.5" />
                {msg.tool_calls_made} tool call{msg.tool_calls_made !== 1 ? "s" : ""}
              </span>
            ) : null}
            {msg.critic_applied ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-[0.67rem] font-medium text-violet-600">
                <ShieldCheck className="h-2.5 w-2.5" />
                AI reviewed
              </span>
            ) : null}
            {msg.summary_model ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-[0.67rem] font-medium text-blue-700">
                <Sparkles className="h-2.5 w-2.5" />
                Finalised by {msg.summary_provider || "ai"} / {msg.summary_model}
              </span>
            ) : null}
          </div>
        ) : null}

        {!isUser && msg.citations && msg.citations.length > 0 ? (
          <div className="w-full space-y-1">
            <button
              onClick={() => setCitationsOpen((value) => !value)}
              className="flex items-center gap-1.5 px-1 text-[0.7rem] font-semibold text-muted-foreground transition-colors hover:text-foreground"
            >
              <FileText className="h-3 w-3" />
              {msg.citations.length} source{msg.citations.length !== 1 ? "s" : ""}
              {citationsOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>
            {citationsOpen ? (
              <div className="space-y-1">
                {msg.citations.map((citation, index) => (
                  <CitationCard key={`${msg.id}-citation-${index}`} citation={citation} />
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {!isUser && msg.proposed_patches && msg.proposed_patches.length > 0 ? (
          <div className="w-full space-y-2">
            <p className="px-1 text-[0.7rem] font-bold uppercase tracking-wider text-amber-600">Proposed corrections</p>
            <div className="grid gap-2">
              {msg.proposed_patches.map((patch) => (
                <PatchCard
                  key={`${msg.id}-${patch.question_id}`}
                  patch={patch}
                  state={msg.patchStates?.[patch.question_id] ?? "pending"}
                  onApply={() => onApplyPatch(msg.id, patch.question_id)}
                  onDismiss={() => onDismissPatch(msg.id, patch.question_id)}
                />
              ))}
            </div>
          </div>
        ) : null}

        {!isUser && msg.reasoning_steps && msg.reasoning_steps.length > 0 ? (
          <details className="w-full rounded-xl border border-border bg-card px-3 py-2 text-[0.73rem] shadow-sm">
            <summary className="cursor-pointer font-semibold text-muted-foreground">
              Reasoning ({msg.reasoning_steps.length} step{msg.reasoning_steps.length !== 1 ? "s" : ""})
            </summary>
            <div className="mt-2 space-y-2 text-muted-foreground">
              {msg.reasoning_steps.map((step, index) => (
                <div key={`${msg.id}-step-${index}`} className="rounded-lg bg-muted px-3 py-2">
                  <div className="font-semibold text-foreground">
                    {index + 1}. {step.tool}
                  </div>
                  <div className="mt-1 text-muted-foreground">{step.summary}</div>
                </div>
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </>
  );

  if (suppressMotion) {
    return <div className={`flex gap-2.5 group/bubble ${isUser ? "flex-row-reverse" : ""}`}>{content}</div>;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex gap-2.5 group/bubble ${isUser ? "flex-row-reverse" : ""}`}
    >
      {content}
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Main Chatbot component
// ---------------------------------------------------------------------------

export function Chatbot({
  isOpen,
  onClose,
  onAsk,
  onApplyPatch,
  onReviewAgain,
  onRunReprocessed,
  initialConflicts = [],
  initialTurns = [],
  runId,
  userName,
  suppressMotion = false,
  chatContext = "matter",
  onResolveTriconveyReference,
}: ChatbotProps) {
  const [messages, setMessages] = useState<Message[]>(() => {
    if (runId) {
      const stored = loadFromSession(runId);
      if (stored) return stored;
    }
    return [buildWelcomeMessage(userName, chatContext)];
  });
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<Mode>("standard");
  const [expanded, setExpanded] = useState(false);
  const [sessionId] = useState<string>(() => crypto.randomUUID());
  const [conflictInjected, setConflictInjected] = useState(() => {
    if (runId) {
      const stored = loadFromSession(runId);
      return stored !== null && stored.length > 1;
    }
    return false;
  });

  // ── File attachment state ─────────────────────────────────────────────────
  const [pendingFiles, setPendingFiles] = useState<ChatUploadItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  // ── Corpus confirmation state ─────────────────────────────────────────────
  const [corpusPending, setCorpusPending] = useState<CorpusPendingEntry[]>([]);
  const [confirmingCorpus, setConfirmingCorpus] = useState<Set<string>>(new Set());
  // ── Incremental document processing state ─────────────────────────────────
  type DocProcState =
    | { phase: "warning"; result: ProcessDocumentResult }
    | { phase: "changes"; result: ProcessDocumentResult; applying: boolean }
    | { phase: "processing"; filename: string };
  const [docProcQueue, setDocProcQueue] = useState<DocProcState[]>([]);
  const [thinkingPhase, setThinkingPhase] = useState<ThinkingPhase>("thinking");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const prevRunIdRef = useRef(runId);

  useEffect(() => {
    if (runId && messages.length > 0) {
      saveToSession(runId, messages);
    }
  }, [messages, runId]);

  useEffect(() => {
    if (prevRunIdRef.current === runId) return;
    prevRunIdRef.current = runId;

    requestRef.current?.abort();
    requestRef.current = null;
    setSending(false);
    setPendingFiles([]);
    setInput("");

    if (runId) {
      const stored = loadFromSession(runId);
      if (stored) {
        setMessages(stored);
        setConflictInjected(true);
        return;
      }
    }
    setMessages([buildWelcomeMessage(userName, chatContext)]);
    setConflictInjected(false);
  }, [runId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: suppressMotion ? "auto" : "smooth" });
  }, [messages, sending, suppressMotion]);

  useEffect(() => {
    if (isOpen && !suppressMotion) {
      window.setTimeout(() => textareaRef.current?.focus(), 300);
    } else {
      setInput("");
    }
  }, [isOpen, suppressMotion]);

  useEffect(() => {
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!isOpen || conflictInjected) return;
    const introText = buildConflictIntro(initialConflicts);
    if (introText) {
      setMessages((prev) => {
        if (prev.some((message) => message.id === "conflicts-intro")) return prev;
        return [...prev, { id: "conflicts-intro", role: "assistant", text: introText }];
      });
    }
    setConflictInjected(true);
  }, [isOpen, conflictInjected, initialConflicts]);

  useEffect(() => {
    if (!initialTurns.length) return;
    setMessages((prev) => {
      if (prev.length > 1) return prev;
      const restored: Message[] = initialTurns.map((turn, index) => ({
        id: `history-${index}`,
        role: turn.role,
        text: turn.content,
      }));
      return [prev[0], ...restored];
    });
  }, [initialTurns]);

  const buildHistory = (): HistoryTurn[] =>
    messages
      .filter((message) => message.id !== "welcome" && message.id !== "conflicts-intro")
      .map((message) => ({ role: message.role, content: message.text }));

  const appendFiles = async (nextFiles: File[]) => {
    // Build initial items — TriConvey references start in "resolving" state
    const nextItems: ChatUploadItem[] = nextFiles.map((file) => {
      if (isTriconveyReferenceName(file.name)) {
        return { file, displayName: "TriConvey document", subtitle: "Loading PDFs…", resolving: true };
      }
      return { file };
    });

    setPendingFiles((prev) => {
      const existing = new Set(prev.map((item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
      return [...prev, ...nextItems.filter((item) => !existing.has(`${item.file.name}:${item.file.size}:${item.file.lastModified}`))];
    });

    if (!onResolveTriconveyReference) return;

    // Resolve each TriConvey reference and update the chip once resolved
    await Promise.all(
      nextFiles.map(async (file) => {
        if (!isTriconveyReferenceName(file.name)) return;
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        const payloadText = await file.text();

        let resolved: { displayName: string; subtitle: string } = {
          displayName: "TriConvey document",
          subtitle: "Could not load PDFs — open them in TriConvey first",
        };

        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            const result = await onResolveTriconveyReference(payloadText);
            const names = result.resolved.map((r) => r.name).filter(Boolean);
            if (names.length > 0) {
              resolved = {
                displayName: names.join(", "),
                subtitle: `${names.length} PDF${names.length === 1 ? "" : "s"} from TriConvey`,
              };
              break;
            }
          } catch {
            // retry
          }
          if (attempt < 2) await delay(attempt === 0 ? 1000 : 1200);
        }

        // Update the chip with resolved names
        setPendingFiles((prev) =>
          prev.map((item) =>
            `${item.file.name}:${item.file.size}:${item.file.lastModified}` === key
              ? { ...item, ...resolved, resolving: false }
              : item
          )
        );
      })
    );
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []).filter(isSupportedChatUpload);
    void appendFiles(selected);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const plainText = e.dataTransfer.getData("text/plain");
    const uriList = e.dataTransfer.getData("text/uri-list");
    const htmlText = e.dataTransfer.getData("text/html");
    const parsed = parseDroppedReferenceText(plainText, uriList, htmlText);
    const droppedFiles = Array.from(e.dataTransfer.files ?? []).filter(isSupportedChatUpload);
    const droppedFilePaths = extractDroppedFilePaths(droppedFiles);
    const droppedPdfFiles = droppedFiles.filter((f) => !isTriconveyReferenceName(f.name));
    const hasOnlyTriconveyRefs =
      droppedFiles.length > 0 && droppedFiles.every((f) => isTriconveyReferenceName(f.name));

    // 1. Desktop file paths (e.g. from Windows Explorer or TriConvey)
    if (droppedFilePaths.length) {
      void appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: droppedFilePaths }, null, 2))]);
      return;
    }
    // 2. Local paths in drag payload text (TriConvey drag from document list)
    if (parsed?.localPaths?.length) {
      void appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: parsed.localPaths }, null, 2))]);
      return;
    }
    // 3. Plain PDFs dropped directly
    if (droppedPdfFiles.length > 0) {
      void appendFiles(droppedPdfFiles);
      return;
    }
    // 4. Matter payload from TriConvey
    if (parsed?.matterPayload) {
      void appendFiles([makeReferenceFile(parsed.matterPayload)]);
      return;
    }
    // 5. TriConvey reference files (.smokeball.tmp / .json)
    if (hasOnlyTriconveyRefs) {
      void appendFiles(droppedFiles);
      return;
    }
    if (droppedFiles.length > 0) {
      void appendFiles(droppedFiles);
    }
  };

  const removeFile = (name: string) => {
    setPendingFiles((prev) => prev.filter((item) => item.file.name !== name));
  };

  const handleConfirmReviewAgain = async () => {
    if (!onReviewAgain) return;
    // Replace confirm message with acknowledgement
    setMessages((prev) =>
      prev.map((m) =>
        m.isReviewConfirm
          ? { ...m, isReviewConfirm: false, text: "Re-analysing the matter — I'll update all fields once done. This may take a minute." }
          : m,
      ),
    );
    try {
      await onReviewAgain();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: `${Date.now()}-err`, role: "assistant", text: `Re-analysis failed: ${err instanceof Error ? err.message : "unknown error"}` },
      ]);
    }
  };

  const handleSend = async (text?: string) => {
    const question = (text ?? input).trim();
    const hasFiles = pendingFiles.length > 0;
    if ((!question && !hasFiles) || sending) return;

    // ── Intercept "review again" intent ─────────────────────────────────────
    if (chatContext === "matter" && onReviewAgain && isReviewAgainRequest(question)) {
      setMessages((prev) => [
        ...prev,
        { id: `${Date.now()}-user`, role: "user", text: question },
        {
          id: `${Date.now()}-confirm`,
          role: "assistant",
          text: "Do you want me to re-analyse this matter with all current documents? I'll re-extract every field and update the review screen.",
          isReviewConfirm: true,
        },
      ]);
      setInput("");
      return;
    }

    const history = buildHistory();
    const attachmentNames = pendingFiles.map((item) => item.displayName || item.file.name);
    const filesToUpload = pendingFiles.map((item) => item.file);
    const questionText = question || `Please extract and update all relevant matter fields from the attached document${filesToUpload.length > 1 ? "s" : ""}.`;

    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-user`, role: "user", text: questionText, attachments: attachmentNames.length ? attachmentNames : undefined },
    ]);
    setInput("");
    setPendingFiles([]);
    setSending(true);
    const controller = new AbortController();
    requestRef.current = controller;

    try {
      // ── Step 1: Process each file individually (validate → extract → propose) ──
      if (hasFiles && runId) {
        setThinkingPhase("uploading");
        for (const file of filesToUpload) {
          // Show a "processing" placeholder immediately
          setDocProcQueue((prev) => [...prev, { phase: "processing", filename: file.name }]);
          try {
            const result = await processNewChatDocument(runId, file);
            // Remove the placeholder
            setDocProcQueue((prev) => prev.filter(
              (s) => !(s.phase === "processing" && s.filename === file.name)
            ));
            if (result.already_exists) {
              setMessages((prev) => [
                ...prev,
                { id: `${Date.now()}-dup`, role: "assistant", text: `ℹ️ **${result.filename ?? file.name}** is already part of this matter — it was included in the original documents. If you have an updated version, rename the file and drop it again.` },
              ]);
              continue;
            }
            if (result.error) {
              setMessages((prev) => [
                ...prev,
                { id: `${Date.now()}-doc-err`, role: "assistant", text: `Could not process ${file.name}: ${result.error}` },
              ]);
              continue;
            }
            // If suspicious → show warning card first
            if (result.validation?.is_suspicious) {
              setDocProcQueue((prev) => [...prev, { phase: "warning", result }]);
            } else {
              // Go straight to proposed changes
              setDocProcQueue((prev) => [...prev, { phase: "changes", result, applying: false }]);
            }
          } catch (uploadErr) {
            setDocProcQueue((prev) => prev.filter(
              (s) => !(s.phase === "processing" && s.filename === file.name)
            ));
            const msg = uploadErr instanceof Error ? uploadErr.message : "Upload failed";
            setMessages((prev) => [
              ...prev,
              { id: `${Date.now()}-upload-err`, role: "assistant", text: `Failed to process ${file.name}: ${msg}` },
            ]);
          }
        }
      }

      // ── Step 2: Ask the AI ───────────────────────────────────────────────
      setThinkingPhase("thinking");
      const response = await onAsk(questionText, history, mode, controller.signal, sessionId);
      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-assistant`,
          role: "assistant",
          text: response.answer || "I could not find a precise answer in the uploaded documents.",
          citations: response.citations,
          proposed_patches: response.proposed_patches,
          reasoning_steps: response.reasoning_steps,
          tool_calls_made: response.tool_calls_made,
          confidence_note: response.confidence_note,
          critic_applied: response.critic_applied,
          patchStates: Object.fromEntries(
            (response.proposed_patches ?? []).map((patch) => [patch.question_id, "pending" as const]),
          ),
          agent_runs: response.agent_runs,
          summary_model: response.summary_model,
          summary_provider: response.summary_provider,
        },
      ]);
    } catch (error) {
      const aborted =
        (error instanceof DOMException && error.name === "AbortError") ||
        (error instanceof Error && error.name === "AbortError");
      if (aborted) return;
      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-error`,
          role: "assistant",
          text: error instanceof Error ? `Error: ${error.message}` : "Something went wrong. Please try again.",
        },
      ]);
    } finally {
      requestRef.current = null;
      setSending(false);
      setThinkingPhase("thinking");
    }
  };

  const handleCancel = () => {
    requestRef.current?.abort();
    requestRef.current = null;
    setSending(false);
    setThinkingPhase("thinking");
  };

  const handleEditMessage = (msgId: string, text: string) => {
    if (sending) {
      requestRef.current?.abort();
      requestRef.current = null;
      setSending(false);
    }
    const index = messages.findIndex((message) => message.id === msgId);
    if (index === -1) return;
    setMessages((prev) => prev.slice(0, index));
    setInput(text);
    if (!suppressMotion) {
      window.setTimeout(() => textareaRef.current?.focus(), 50);
    }
  };

  const handleApplyPatch = async (msgId: string, questionId: string) => {
    const message = messages.find((entry) => entry.id === msgId);
    const patch = message?.proposed_patches?.find((entry) => entry.question_id === questionId);
    if (!patch || !onApplyPatch) return;

    try {
      await onApplyPatch(questionId, patch.new_value, patch.reason);
      setMessages((prev) =>
        prev.map((entry) =>
          entry.id === msgId ? { ...entry, patchStates: { ...entry.patchStates, [questionId]: "applied" } } : entry,
        ),
      );
    } catch {
      // Leave pending if patch apply fails.
    }
  };

  const handleDismissPatch = (msgId: string, questionId: string) => {
    setMessages((prev) =>
      prev.map((entry) =>
        entry.id === msgId ? { ...entry, patchStates: { ...entry.patchStates, [questionId]: "dismissed" } } : entry,
      ),
    );
  };

  const assistantPaused = suppressMotion;
  const isBusy = assistantPaused || sending;
  const isDashboard = chatContext === "dashboard";
  const agentLabel = isDashboard ? "Assistant" : "Matter Agent";
  const statusText = isBusy ? (isDashboard ? "thinking" : "multi-agent review") : "ready";
  const panelWidth = expanded ? 660 : 500;
  const lastUserIndex = messages.reduce((last, message, index) => (message.role === "user" ? index : last), -1);
  const canSend = (input.trim().length > 0 || pendingFiles.length > 0) && !assistantPaused;

  const handleDismissReviewConfirm = (msgId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === msgId ? { ...m, isReviewConfirm: false, text: "Okay, no problem! Let me know if you need anything else." } : m,
      ),
    );
  };

  const messageList = suppressMotion ? (
    messages.map((message, index) => (
      <MessageBubble
        key={message.id}
        msg={message}
        onApplyPatch={handleApplyPatch}
        onDismissPatch={handleDismissPatch}
        onEdit={index === lastUserIndex ? handleEditMessage : undefined}
        onConfirmReview={handleConfirmReviewAgain}
        onDismissReview={handleDismissReviewConfirm}
        suppressMotion
      />
    ))
  ) : (
    <AnimatePresence initial={false}>
      {messages.map((message, index) => (
        <MessageBubble
          key={message.id}
          msg={message}
          onApplyPatch={handleApplyPatch}
          onDismissPatch={handleDismissPatch}
          onEdit={index === lastUserIndex ? handleEditMessage : undefined}
          onConfirmReview={handleConfirmReviewAgain}
          onDismissReview={handleDismissReviewConfirm}
        />
      ))}
    </AnimatePresence>
  );

  const shell = (
    <>
      {/* Header */}
      <div className="flex shrink-0 flex-col gap-3 border-b border-border bg-card px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="group/icon relative flex h-11 w-11 items-center justify-center rounded-[1.1rem] bg-foreground text-background shadow-lg shadow-black/20">
              {isDashboard ? <Sparkles className="h-4 w-4 relative z-10" /> : <Bot className="h-4 w-4 relative z-10" />}
              {!isDashboard ? (
                <Sparkles className="absolute -right-0.5 -top-0.5 h-3.5 w-3.5 text-sky-200 transition-transform duration-500 group-hover/icon:-translate-y-1 group-hover/icon:translate-x-1 group-hover/icon:rotate-12" />
              ) : null}
              <div className="absolute inset-0 rounded-[1.1rem] bg-white/10 opacity-0 transition-opacity group-hover/icon:opacity-100" />
              <span className="absolute inset-0 rounded-[1.1rem] border border-white/20" />
            </div>
            <div>
              <h3 className="text-sm font-bold leading-tight text-foreground">{agentLabel}</h3>
              <div className="flex items-center gap-1.5">
                <span className={["h-1.5 w-1.5 rounded-full", isBusy ? (suppressMotion ? "bg-amber-400" : "animate-pulse bg-amber-400") : "bg-emerald-500"].join(" ")} />
                <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{statusText}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as Mode)}
              className="h-9 rounded-xl border border-border bg-card px-3 text-[0.74rem] font-semibold text-foreground outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary"
            >
              {MODES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <button
              onClick={() => setExpanded((value) => !value)}
              className="rounded-xl p-1.5 text-muted-foreground transition-colors hover:bg-accent"
              title={expanded ? "Collapse" : "Expand"}
            >
              {expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
            <button onClick={onClose} className="rounded-xl p-1.5 text-muted-foreground transition-colors hover:bg-accent">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="custom-scrollbar relative flex-1 overflow-y-auto bg-muted/20">
        <div
          className="absolute inset-0 pointer-events-none opacity-[0.03]"
          style={{ backgroundImage: `radial-gradient(circle at 1px 1px, #000 1px, transparent 0)`, backgroundSize: "24px 24px" }}
        />
        <div className="relative space-y-5 px-4 py-6">
          {messageList}
          {sending ? (
            <ThinkingIndicator
              mode={mode}
              phase={thinkingPhase}
              chatContext={chatContext}
              suppressMotion={suppressMotion}
            />
          ) : null}

          {/* Corpus pending confirmation cards (legacy path) */}
          {corpusPending.length > 0 ? (
            <div className="mt-3 space-y-3 px-1">
              {corpusPending.map((entry) => (
                <CorpusConfirmCard
                  key={entry.document_id}
                  entry={entry}
                  confirming={confirmingCorpus.has(entry.document_id)}
                  onConfirm={async () => {
                    if (!runId) return;
                    setConfirmingCorpus((prev) => new Set([...prev, entry.document_id]));
                    try {
                      const result = await confirmCorpusEntry(runId, entry.document_id);
                      setCorpusPending((prev) => prev.filter((e) => e.document_id !== entry.document_id));
                      setMessages((prev) => [
                        ...prev,
                        {
                          id: `${Date.now()}-corpus-confirmed`,
                          role: "assistant",
                          text: result.message || `Document corpus updated with ${entry.filename}.`,
                        },
                      ]);
                    } catch {
                      // keep card visible on error
                    } finally {
                      setConfirmingCorpus((prev) => {
                        const next = new Set(prev);
                        next.delete(entry.document_id);
                        return next;
                      });
                    }
                  }}
                  onDismiss={() =>
                    setCorpusPending((prev) => prev.filter((e) => e.document_id !== entry.document_id))
                  }
                />
              ))}
            </div>
          ) : null}

          {/* Incremental document processing cards */}
          {docProcQueue.length > 0 ? (
            <div className="mt-3 space-y-3 px-1">
              {docProcQueue.map((state, idx) => {
                const key = idx;
                const dismiss = () =>
                  setDocProcQueue((prev) => prev.filter((_, i) => i !== idx));

                if (state.phase === "processing") {
                  return (
                    <div key={key} className="flex items-center gap-2 rounded-xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>Analysing <span className="font-medium">{state.filename}</span>…</span>
                    </div>
                  );
                }

                if (state.phase === "warning") {
                  return (
                    <DocumentWarningCard
                      key={key}
                      filename={state.result.filename}
                      reason={state.result.validation?.reason ?? ""}
                      warningMessage={state.result.validation?.warning_message}
                      proceeding={false}
                      onIgnore={dismiss}
                      onProceed={() => {
                        // User approved — move straight to changes card
                        setDocProcQueue((prev) =>
                          prev.map((s, i) =>
                            i === idx ? { phase: "changes" as const, result: (s as { phase: "warning"; result: ProcessDocumentResult }).result, applying: false } : s
                          )
                        );
                      }}
                    />
                  );
                }

                if (state.phase === "changes") {
                  return (
                    <ProposedChangesCard
                      key={key}
                      filename={state.result.filename}
                      changes={state.result.proposed_changes ?? []}
                      applying={state.applying}
                      onDismiss={dismiss}
                      onApprove={async (approvedIds) => {
                        if (!runId) return;
                        setDocProcQueue((prev) =>
                          prev.map((s, i) => i === idx ? { ...s, applying: true } : s)
                        );
                        try {
                          const applied = await applyDocumentChanges(runId, {
                            document_id: state.result.document_id,
                            doc_path: state.result.doc_path,
                            corpus_entry: state.result.corpus_entry,
                            approved_change_ids: approvedIds,
                            all_changes: state.result.proposed_changes ?? [],
                          });
                          dismiss();
                          onRunReprocessed?.();
                          setMessages((prev) => [
                            ...prev,
                            {
                              id: `${Date.now()}-doc-applied`,
                              role: "assistant",
                              text: applied.applied_changes.length > 0
                                ? `Applied ${applied.applied_changes.length} change(s) from **${state.result.filename}** to this matter.`
                                : `${state.result.filename} added to the matter. No answer changes were needed.`,
                            },
                          ]);
                        } catch {
                          setDocProcQueue((prev) =>
                            prev.map((s, i) => i === idx ? { ...s, applying: false } : s)
                          );
                        }
                      }}
                    />
                  );
                }

                return null;
              })}
            </div>
          ) : null}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input area */}
      <div className="border-t border-border bg-card px-4 pb-4 pt-3 shrink-0">
        {/* Pending file chips */}
        {pendingFiles.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {pendingFiles.map((item) => (
              <div key={`${item.file.name}:${item.file.lastModified}`} className="relative">
                <AttachmentChip
                  name={item.displayName || item.file.name}
                  onRemove={() => removeFile(item.file.name)}
                />
                {item.resolving && (
                  <span className="absolute -right-1 -top-1 flex h-3 w-3 items-center justify-center rounded-full bg-primary">
                    <Loader2 className="h-2 w-2 animate-spin text-primary-foreground" />
                  </span>
                )}
                {item.subtitle && !item.resolving && (
                  <span className="sr-only">{item.subtitle}</span>
                )}
              </div>
            ))}
          </div>
        ) : null}

        <div className="relative flex items-end gap-2 rounded-[1.4rem] border border-border bg-muted/50 px-3.5 py-2.5 shadow-xs transition-all duration-300 focus-within:border-primary/40 focus-within:bg-card focus-within:shadow-lg focus-within:shadow-primary/5">
          {/* File attach — only available for matter chatbot with a runId */}
          {chatContext === "matter" && runId ? (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf,.json,.smokeball.tmp"
                multiple
                className="sr-only"
                onChange={handleFileChange}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || assistantPaused}
                className="shrink-0 rounded-xl p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-primary disabled:opacity-40"
                title="Attach PDFs or drag from TriConvey"
              >
                <Paperclip className="h-4 w-4" />
              </button>
            </>
          ) : null}

          <Textarea
            ref={textareaRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder={
              assistantPaused
                ? "Assistant pauses while autofill is running"
                : sending
                ? "AI is reviewing… press stop to cancel"
                : pendingFiles.length > 0
                ? "Add a question or send to extract all fields…"
                : chatContext === "matter"
                ? "Ask anything about the documents…"
                : "Ask anything about your workspace…"
            }
            rows={1}
            disabled={sending || assistantPaused}
            className="min-h-[1.5rem] max-h-32 flex-1 resize-none border-0 bg-transparent p-0 text-[0.85rem] leading-relaxed outline-none focus-visible:ring-0 disabled:opacity-60"
          />

          {sending ? (
            <button
              onClick={handleCancel}
              className="shrink-0 rounded-xl border border-rose-200 bg-rose-50 p-1.5 text-rose-600 transition-all hover:scale-105 hover:bg-rose-100 active:scale-95"
              title="Cancel"
            >
              <CircleStop className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={() => void handleSend()}
              disabled={!canSend}
              className="shrink-0 rounded-2xl bg-primary p-1.5 text-white transition-all hover:scale-105 active:scale-95 disabled:opacity-40"
              title="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[10px] text-muted-foreground">
          {chatContext === "matter"
            ? "Every answer is AI-reviewed against the uploaded matter files. Verify important values before autofill."
            : "AI assistant for workspace navigation and general queries."}
        </p>
      </div>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--muted-foreground); }
      `}</style>
    </>
  );

  if (suppressMotion) {
    return (
      <div
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }}
        onDragLeave={(e) => { e.stopPropagation(); if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false); }}
        onDrop={(e) => { e.stopPropagation(); void handleDrop(e); }}
        className="relative z-10 flex h-full shrink-0 flex-col overflow-hidden border-l border-border bg-card shadow-2xl"
        style={{ width: isOpen ? panelWidth : 0, opacity: isOpen ? 1 : 0 }}
      >
        {isDragging && (
          <div className="absolute inset-0 z-[110] flex flex-col items-center justify-center bg-background/60 backdrop-blur-sm">
            <div className="flex h-20 w-20 items-center justify-center rounded-full bg-primary text-white shadow-xl">
              <Upload className="h-10 w-10 animate-bounce" />
            </div>
            <p className="mt-4 text-lg font-black text-foreground">Drop PDFs or TriConvey documents</p>
            <p className="text-sm font-bold text-muted-foreground">PDFs and TriConvey reference files accepted</p>
          </div>
        )}
        {shell}
      </div>
    );
  }

  return (
    <motion.div
      initial={false}
      animate={{ width: isOpen ? panelWidth : 0, opacity: isOpen ? 1 : 0 }}
      transition={{ type: "spring", stiffness: 320, damping: 32 }}
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }}
      onDragLeave={(e) => { e.stopPropagation(); if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false); }}
      onDrop={(e) => { e.stopPropagation(); void handleDrop(e); }}
      className="relative z-10 flex h-full shrink-0 flex-col overflow-hidden border-l border-border bg-card shadow-2xl"
    >
      {isDragging && (
        <div className="absolute inset-0 z-[110] flex flex-col items-center justify-center bg-background/60 backdrop-blur-sm">
          <div className="flex h-20 w-20 items-center justify-center rounded-full bg-primary text-white shadow-xl">
            <Upload className="h-10 w-10 animate-bounce" />
          </div>
          <p className="mt-4 text-lg font-black text-foreground">Drop PDFs or TriConvey documents</p>
          <p className="text-sm font-bold text-muted-foreground">PDFs and TriConvey reference files accepted</p>
        </div>
      )}
      {shell}
    </motion.div>
  );
}
