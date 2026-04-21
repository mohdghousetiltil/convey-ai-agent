import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleStop,
  FileText,
  Maximize2,
  Minimize2,
  Pencil,
  Send,
  ShieldCheck,
  Sparkles,
  User,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import {
  ChatAnswerPayload,
  ProposedPatch,
  ReasoningStep,
} from "../lib/api";

type Mode = "quick" | "standard" | "thorough";

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
}

interface ChatbotProps {
  isOpen: boolean;
  onClose: () => void;
  onAsk: (
    question: string,
    history: HistoryTurn[],
    mode: Mode,
    signal?: AbortSignal,
  ) => Promise<ChatAnswerPayload>;
  onApplyPatch?: (questionId: string, newValue: string, reason: string) => Promise<void>;
  initialConflicts?: ConflictPreview[];
  initialTurns?: HistoryTurn[];
  runId?: string;
}

const WELCOME_MESSAGE: Message = {
  id: "welcome",
  role: "assistant",
  text:
    "AI review is ready. I can cross-check extracted facts, review uploaded PDFs again, compare sources, and suggest safe corrections before autofill.",
};

const SUGGESTED: string[] = [
  "What is the annual water charge?",
  "What are the annual council rates?",
  "Is the property in a bushfire prone area?",
  "What planning overlays affect the property?",
  "Are there any unresolved conflicts in the extracted data?",
  "What owners corporation fees apply?",
  "Run full review checklist for this matter.",
];

const MODES: { id: Mode; label: string }[] = [
  { id: "quick", label: "Basic" },
  { id: "standard", label: "Normal" },
  { id: "thorough", label: "Deep" },
];

const THINKING_LABELS: Record<Mode, string[]> = {
  quick: [
    "thinking",
    "reviewing the files",
    "finding the clue",
    "checking the corpus",
  ],
  standard: [
    "thinking hard",
    "reviewing the files",
    "cross-checking sources",
    "building the branch",
    "tightening the answer",
  ],
  thorough: [
    "thinking hard",
    "reviewing the files",
    "cross-checking every source",
    "finding the clue",
    "building the branch",
    "final AI review in progress",
  ],
};

function buildConflictIntro(conflicts: ConflictPreview[]): string {
  if (!conflicts.length) {
    return "No unresolved fact conflicts were found. I am ready to help with document questions, source checks, and safe corrections.";
  }
  const lines = conflicts.slice(0, 4).map((conflict) => {
    const summary = conflict.candidates
      .slice(0, 2)
      .map((candidate) => {
        const source = candidate.file ? ` from ${candidate.file}` : "";
        return `${candidate.extractor} says "${candidate.value}"${source}`;
      })
      .join(" vs ");
    return `- ${conflict.path}: ${summary}`;
  });
  return `${conflicts.length} unresolved conflict${conflicts.length !== 1 ? "s" : ""} found:\n${lines.join("\n")}\n\nAsk me about any of these and I will help resolve them.`;
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

function CitationCard({ citation }: { citation: NonNullable<ChatAnswerPayload["citations"]>[number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white text-[0.73rem] shadow-sm">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 px-2.5 py-2 text-left transition-colors hover:bg-slate-50"
      >
        <FileText className="h-3 w-3 shrink-0 text-primary/60" />
        <span className="flex-1 truncate font-semibold text-slate-700">
          {citation.file || "Unknown file"}
          {citation.page ? ` - p.${citation.page}` : ""}
        </span>
        {open ? <ChevronUp className="h-3 w-3 text-slate-400" /> : <ChevronDown className="h-3 w-3 text-slate-400" />}
      </button>
      {open && citation.quote ? (
        <p className="border-t border-slate-100 px-2.5 pb-2 pt-1.5 italic leading-relaxed text-slate-500">
          "{citation.quote}"
        </p>
      ) : null}
    </div>
  );
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
            ? "border-slate-200 bg-slate-50 opacity-50"
            : "border-amber-200 bg-amber-50/70",
      ].join(" ")}
    >
      <div className="flex items-center gap-3 px-3 py-2.5">
        <AlertTriangle className={["h-3.5 w-3.5 shrink-0", isApplied ? "text-emerald-500" : "text-amber-500"].join(" ")} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.78rem] font-semibold text-slate-800">{label}</p>
          <p className="text-[0.76rem] font-bold text-slate-700">{patch.new_value}</p>
          {brief ? <p className="mt-0.5 truncate text-[0.7rem] text-slate-400">{brief}</p> : null}
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
              className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[0.7rem] font-semibold text-slate-500 transition-colors hover:bg-slate-50"
            >
              <XCircle className="h-3 w-3" />
            </button>
          </div>
        ) : (
          <span className={["text-[0.68rem] font-bold uppercase tracking-wider", isApplied ? "text-emerald-600" : "text-slate-400"].join(" ")}>
            {isApplied ? "Applied" : "Dismissed"}
          </span>
        )}
      </div>
    </div>
  );
}

function MessageBubble({
  msg,
  onApplyPatch,
  onDismissPatch,
  onEdit,
}: {
  msg: Message;
  onApplyPatch: (msgId: string, questionId: string) => void;
  onDismissPatch: (msgId: string, questionId: string) => void;
  onEdit?: (msgId: string, text: string) => void;
}) {
  const isUser = msg.role === "user";
  const [citationsOpen, setCitationsOpen] = useState(false);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex gap-2.5 group/bubble ${isUser ? "flex-row-reverse" : ""}`}
    >
      <div className={["mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl shadow-sm", isUser ? "bg-slate-200" : "bg-primary/10"].join(" ")}>
        {isUser ? <User className="h-3.5 w-3.5 text-slate-600" /> : <Bot className="h-3.5 w-3.5 text-primary" />}
      </div>

      <div className={`flex max-w-[88%] flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
        <div className="flex items-start gap-1.5">
          {isUser && onEdit ? (
            <button
              onClick={() => onEdit(msg.id, msg.text)}
              className="mt-2 shrink-0 rounded-lg p-1 text-slate-400 opacity-0 transition-opacity hover:bg-slate-100 hover:text-slate-600 group-hover/bubble:opacity-100"
              title="Edit and resend"
            >
              <Pencil className="h-3 w-3" />
            </button>
          ) : null}
          <div
            className={[
              "rounded-3xl px-4 py-3 text-[0.84rem] leading-relaxed shadow-sm",
              isUser
                ? "rounded-tr-md bg-primary text-white shadow-primary/20"
                : "rounded-tl-md border border-slate-200/80 bg-white text-slate-800",
            ].join(" ")}
          >
            <p className="whitespace-pre-wrap">{msg.text}</p>
          </div>
        </div>

        {!isUser && (msg.tool_calls_made || msg.critic_applied || msg.summary_model) ? (
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            {msg.tool_calls_made ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[0.67rem] font-medium text-slate-500">
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
              className="flex items-center gap-1.5 px-1 text-[0.7rem] font-semibold text-slate-400 transition-colors hover:text-slate-600"
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
          <details className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[0.73rem] shadow-sm">
            <summary className="cursor-pointer font-semibold text-slate-600">
              Reasoning ({msg.reasoning_steps.length} step{msg.reasoning_steps.length !== 1 ? "s" : ""})
            </summary>
            <div className="mt-2 space-y-2 text-slate-500">
              {msg.reasoning_steps.map((step, index) => (
                <div key={`${msg.id}-step-${index}`} className="rounded-lg bg-slate-50 px-3 py-2">
                  <div className="font-semibold text-slate-700">
                    {index + 1}. {step.tool}
                  </div>
                  <div className="mt-1 text-slate-500">{step.summary}</div>
                </div>
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </motion.div>
  );
}

function ThinkingIndicator({ mode, uploadState }: { mode: Mode; uploadState: string | null }) {
  const [labelIndex, setLabelIndex] = useState(0);
  const labels = useMemo(() => {
    if (uploadState) {
      return [uploadState];
    }
    return THINKING_LABELS[mode];
  }, [mode, uploadState]);

  useEffect(() => {
    setLabelIndex(0);
  }, [labels]);

  useEffect(() => {
    if (labels.length <= 1) return;
    const timer = window.setInterval(() => {
      setLabelIndex((current) => (current + 1) % labels.length);
    }, 1700);
    return () => window.clearInterval(timer);
  }, [labels]);

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-2.5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl bg-primary/10 shadow-sm">
        <Bot className="h-3.5 w-3.5 text-primary" />
      </div>
      <div className="flex items-center gap-3 rounded-3xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" />
        </div>
        <span className="text-[0.78rem] font-medium capitalize text-slate-500">{labels[labelIndex]}</span>
      </div>
    </motion.div>
  );
}

export function Chatbot({
  isOpen,
  onClose,
  onAsk,
  onApplyPatch,
  initialConflicts = [],
  initialTurns = [],
  runId,
}: ChatbotProps) {
  const [messages, setMessages] = useState<Message[]>(() => {
    if (runId) {
      const stored = loadFromSession(runId);
      if (stored) return stored;
    }
    return [WELCOME_MESSAGE];
  });
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<Mode>("standard");
  const [expanded, setExpanded] = useState(false);
  const [conflictInjected, setConflictInjected] = useState(() => {
    if (runId) {
      const stored = loadFromSession(runId);
      return stored !== null && stored.length > 1;
    }
    return false;
  });
  const [uploadState, setUploadState] = useState<string | null>(null);

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
    setUploadState(null);
    setInput("");

    if (runId) {
      const stored = loadFromSession(runId);
      if (stored) {
        setMessages(stored);
        setConflictInjected(true);
        return;
      }
    }
    setMessages([WELCOME_MESSAGE]);
    setConflictInjected(false);
  }, [runId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    if (isOpen) {
      window.setTimeout(() => textareaRef.current?.focus(), 300);
    } else {
      setInput("");
    }
  }, [isOpen]);

  useEffect(() => {
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!isOpen || conflictInjected) return;
    setMessages((prev) => {
      if (prev.some((message) => message.id === "conflicts-intro")) return prev;
      return [
        ...prev,
        {
          id: "conflicts-intro",
          role: "assistant",
          text: buildConflictIntro(initialConflicts),
        },
      ];
    });
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

  const handleSend = async (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || sending) return;

    const history = buildHistory();
    setMessages((prev) => [...prev, { id: `${Date.now()}-user`, role: "user", text: question }]);
    setInput("");
    setSending(true);
    const controller = new AbortController();
    requestRef.current = controller;

    try {
      const response = await onAsk(question, history, mode, controller.signal);
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
          patchStates: Object.fromEntries((response.proposed_patches ?? []).map((patch) => [patch.question_id, "pending" as const])),
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
    }
  };

  const handleCancel = () => {
    requestRef.current?.abort();
    requestRef.current = null;
    setSending(false);
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
    window.setTimeout(() => textareaRef.current?.focus(), 50);
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

  const isBusy = sending || Boolean(uploadState);
  const statusText = isBusy ? "multi-agent review" : "ready";
  const panelWidth = expanded ? 660 : 500;
  const showSuggestions = messages.length <= 2 && !sending;

  return (
    <motion.div
      initial={false}
      animate={{ width: isOpen ? panelWidth : 0, opacity: isOpen ? 1 : 0 }}
      transition={{ type: "spring", stiffness: 320, damping: 32 }}
      className="relative z-10 flex h-full shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.08),_transparent_38%),linear-gradient(180deg,_#ffffff_0%,_#f8fafc_100%)] shadow-2xl"
    >
      <div className="flex h-16 items-center justify-between border-b border-slate-200/80 bg-white/80 px-4 backdrop-blur shrink-0">
        <div className="flex items-center gap-3">
          <div className="group/icon relative flex h-10 w-10 items-center justify-center rounded-[1.1rem] bg-[linear-gradient(135deg,#0f172a_0%,#1e3a8a_55%,#0ea5e9_100%)] text-white shadow-lg shadow-sky-900/20 transition-transform duration-200 hover:scale-105">
            <Bot className="h-4 w-4" />
            <Sparkles className="absolute -right-0.5 -top-0.5 h-3 w-3 text-sky-200 transition-transform duration-300 group-hover/icon:-translate-y-0.5 group-hover/icon:translate-x-0.5 group-hover/icon:rotate-12" />
            <span className="absolute inset-0 rounded-[1.1rem] border border-white/10 transition-opacity duration-200 group-hover/icon:opacity-0" />
          </div>
          <div>
            <h3 className="text-sm font-bold leading-tight text-slate-900">AI Agent</h3>
            <div className="flex items-center gap-1.5">
              <span className={["h-1.5 w-1.5 rounded-full", isBusy ? "animate-pulse bg-amber-400" : "bg-emerald-500"].join(" ")} />
              <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">{statusText}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as Mode)}
            className="h-9 rounded-xl border border-slate-200 bg-white px-3 text-[0.74rem] font-semibold text-slate-600 outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary"
          >
            {MODES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          <button
            onClick={() => setExpanded((value) => !value)}
            className="rounded-xl p-1.5 text-slate-400 transition-colors hover:bg-slate-100"
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button onClick={onClose} className="rounded-xl p-1.5 text-slate-400 transition-colors hover:bg-slate-100">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="custom-scrollbar flex-1 space-y-5 overflow-y-auto px-4 py-4">
        <AnimatePresence initial={false}>
          {(() => {
            const lastUserIndex = messages.reduce((last, message, index) => (message.role === "user" ? index : last), -1);
            return messages.map((message, index) => (
              <MessageBubble
                key={message.id}
                msg={message}
                onApplyPatch={handleApplyPatch}
                onDismissPatch={handleDismissPatch}
                onEdit={index === lastUserIndex ? handleEditMessage : undefined}
              />
            ));
          })()}
        </AnimatePresence>
        {sending ? <ThinkingIndicator mode={mode} uploadState={uploadState} /> : null}
        <div ref={bottomRef} />
      </div>

      {showSuggestions ? (
        <div className="px-4 pb-2 shrink-0">
          <p className="mb-2 px-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">Suggested</p>
          <div className="flex flex-wrap gap-2">
            {SUGGESTED.map((question) => (
              <button
                key={question}
                onClick={() => void handleSend(question)}
                disabled={sending}
                className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-left text-[0.76rem] text-slate-600 shadow-sm transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="border-t border-slate-200 bg-white/85 px-3 pb-3 pt-2 backdrop-blur shrink-0">
        <div className="relative flex items-end gap-2 rounded-3xl border border-slate-200 bg-slate-50/90 px-3 py-2 transition-colors focus-within:border-primary focus-within:bg-white">
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
              sending
                ? "AI is reviewing... press stop to cancel"
                : "Ask anything about the documents... (Shift+Enter for new line)"
            }
            rows={1}
            disabled={sending}
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
              disabled={!input.trim()}
              className="shrink-0 rounded-2xl bg-primary p-1.5 text-white transition-all hover:scale-105 active:scale-95 disabled:opacity-40"
              title="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[10px] text-slate-400">
          Every answer is AI-reviewed against the uploaded matter files. Verify important values before autofill.
        </p>
      </div>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 10px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }
      `}</style>
    </motion.div>
  );
}
