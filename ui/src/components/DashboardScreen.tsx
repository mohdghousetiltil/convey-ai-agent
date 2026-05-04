import React from "react";
import { motion } from "motion/react";
import { ArrowRight, Bot, CheckCircle2, Clock, Download, FolderOpen, KeyRound, Plus, Sparkles, Timer, Wand2, X, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CloudSyncStatusPayload, UpdateStatusPayload } from "../lib/api";
import { Chatbot } from "./Chatbot";
import { Header } from "./Header";

export type RecentRun = {
  run_id: string;
  client_name: string;
  volume_folio: string;
  property_address: string;
  created_at?: string;
  status?: string;
  time_taken_seconds?: number | null;
};

interface DashboardScreenProps {
  userName?: string | null;
  userInitials?: string;
  recentRuns: RecentRun[];
  isLoadingRuns?: boolean;
  onLoadAllRuns?: () => Promise<void>;
  isLoadingAllRuns?: boolean;
  updateStatus?: UpdateStatusPayload | null;
  cloudSyncStatus?: CloudSyncStatusPayload | null;
  onProcessNew: () => void;
  onViewRun: (runId: string) => void;
  onProfile?: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onAbout?: () => void;
  onLogout?: () => void;
  onAskAssistant: Parameters<typeof Chatbot>[0]["onAsk"];
  onApplyPatch?: Parameters<typeof Chatbot>[0]["onApplyPatch"];
  activeRunId?: string;
  onInstallUpdate?: () => void;
  isInstallingUpdate?: boolean;
  showQuickSetup?: boolean;
  quickSetupSettings?: {
    language: string;
    openAiApiKey: string;
    anthropicApiKey: string;
    googleApiKey: string;
    aiProvider: "openai" | "anthropic" | "google" | "hybrid";
    defaultModelName: string;
    triconveyPath: string;
    preferredAutofillFields: string[];
    updateRepository: string;
    includePrereleaseUpdates: boolean;
    autoCheckForUpdates: boolean;
    cloudSyncEnabled: boolean;
  };
  onSaveQuickSetup?: (settings: DashboardScreenProps["quickSetupSettings"]) => Promise<void> | void;
  onDismissQuickSetup?: () => void;
  onBrowseTriconveyPath?: () => void;
  onResolveTriconveyReference?: (payloadText: string) => Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }>;
}

function formatTimeTaken(seconds?: number | null): string {
  if (seconds == null || seconds <= 0) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

function formatDate(iso?: string) {
  if (!iso) return "-";
  try {
    const date = new Date(iso);
    return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
  } catch {
    return iso;
  }
}

function getStatusBadge(status?: string) {
  switch ((status || "").toLowerCase()) {
    case "complete":
      return {
        label: "Ready",
        className: "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400",
        icon: CheckCircle2,
      };
    case "pending":
    case "running":
      return {
        label: "Processing",
        className: "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400",
        icon: Clock,
      };
    case "failed":
      return {
        label: "Failed",
        className: "border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400",
        icon: Clock,
      };
    default:
      return {
        label: status || "Ready",
        className: "border-border bg-muted text-muted-foreground",
        icon: Clock,
      };
  }
}

export function DashboardScreen({
  userName,
  userInitials,
  recentRuns,
  isLoadingRuns,
  onLoadAllRuns,
  isLoadingAllRuns,
  updateStatus,
  cloudSyncStatus,
  onProcessNew,
  onViewRun,
  onProfile,
  onSettings,
  onPolicy,
  onAbout,
  onLogout,
  onAskAssistant,
  onApplyPatch,
  activeRunId,
  onInstallUpdate,
  isInstallingUpdate,
  showQuickSetup = false,
  quickSetupSettings,
  onSaveQuickSetup,
  onDismissQuickSetup,
  onBrowseTriconveyPath,
  onResolveTriconveyReference,
}: DashboardScreenProps) {
  const [isChatOpen, setIsChatOpen] = React.useState(false);
  const [showAllRuns, setShowAllRuns] = React.useState(false);
  const INITIAL_RUN_LIMIT = 10;
  
  const [greeting, setGreeting] = React.useState("");
  const name = userName?.trim() ? userName.trim() : "there";

  React.useEffect(() => {
    const updateGreeting = () => {
      const hour = new Date().getHours();
      let timeGreeting = "";
      
      if (hour >= 5 && hour < 12) timeGreeting = "Good Morning";
      else if (hour >= 12 && hour < 17) timeGreeting = "Good Afternoon";
      else if (hour >= 17 && hour < 22) timeGreeting = "Good Evening";
      else timeGreeting = "It's late night";

      const variations = [
        `${timeGreeting}, ${name}`,
        `${timeGreeting}, ${name}`, // Weighted
        `${name} returns!`,
        `How's your day, ${name}?`,
        `Nice to see you, ${name}`,
        `Back in action, ${name}!`,
        `Ready for more, ${name}?`
      ];

      const selected = variations[Math.floor(Math.random() * variations.length)];
      setGreeting(selected);
    };

    updateGreeting();
    const timer = setInterval(updateGreeting, 30 * 60 * 1000); // 30 mins
    return () => clearInterval(timer);
  }, [name]);

  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "r") {
        event.preventDefault();
        window.location.reload();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const [quickSetupForm, setQuickSetupForm] = React.useState(() => quickSetupSettings);
  const [quickSetupSaving, setQuickSetupSaving] = React.useState(false);

  React.useEffect(() => {
    setQuickSetupForm(quickSetupSettings);
  }, [quickSetupSettings]);

  const cloudLabel =
    cloudSyncStatus?.enabled === false
      ? "Cloud sync disabled"
      : cloudSyncStatus?.connected
        ? "Cloud sync healthy"
        : cloudSyncStatus?.configured === false
          ? "Cloud sync not configured"
          : cloudSyncStatus?.worker_running === false
            ? "Cloud sync paused"
            : cloudSyncStatus?.connected === false
          ? "Cloud sync offline"
            : "Systems operational";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background font-sans text-foreground">
      <Header
        userInitials={userInitials}
        onProfile={onProfile}
        onSettings={onSettings}
        onPolicy={onPolicy}
        onLogout={onLogout}
        showChatToggle
        isChatOpen={isChatOpen}
        onChatToggle={() => setIsChatOpen((value) => !value)}
      />

      <div className="flex flex-1 overflow-hidden">
        <main className="custom-scrollbar flex-1 overflow-y-auto p-8">
          <div className="mx-auto max-w-7xl space-y-10">
            <div className="flex items-center gap-3">
              <motion.div
                initial={{ opacity: 0, x: -18 }}
                animate={{ opacity: 1, x: 0 }}
                className="inline-flex items-center gap-2 rounded-full border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-emerald-700 dark:text-emerald-400"
              >
                <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                {cloudLabel}
              </motion.div>

              {updateStatus?.update_available && updateStatus?.latest_version ? (
                <motion.div
                  initial={{ opacity: 0, x: -18 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.08 }}
                  className="inline-flex items-center gap-1 rounded-full border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 pr-1 text-[10px] font-bold uppercase tracking-wider text-blue-700 dark:text-blue-400"
                >
                  <button
                    onClick={onAbout}
                    className="flex items-center gap-2 px-3 py-1 transition-colors hover:text-blue-900"
                    title={
                      updateStatus.published_at
                        ? `Update ${updateStatus.latest_version} published ${formatDate(updateStatus.published_at)}`
                        : `Update ${updateStatus.latest_version} available`
                    }
                  >
                    <Sparkles className="h-3 w-3" />
                    Update {updateStatus.latest_version}
                    {updateStatus.published_at ? (
                      <span className="hidden font-medium normal-case tracking-normal md:inline">
                        · {formatDate(updateStatus.published_at)}
                      </span>
                    ) : null}
                  </button>
                  <a
                    href={updateStatus.release_url || "https://github.com/mohdghousetiltil/convey-ai-agent/releases/"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 rounded-full border border-blue-200 dark:border-blue-700 bg-card px-2.5 py-1 text-blue-700 dark:text-blue-400 transition-colors hover:bg-accent"
                    title="Open release page"
                  >
                    <Download className="h-2.5 w-2.5" />
                    Download
                  </a>
                  {onInstallUpdate ? (
                    <button
                      onClick={onInstallUpdate}
                      disabled={isInstallingUpdate}
                      className="inline-flex items-center gap-1 rounded-full border border-blue-300 dark:border-blue-700 bg-card px-2.5 py-1 text-blue-700 dark:text-blue-300 transition-colors hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-60"
                    >
                      <Download className="h-2.5 w-2.5" />
                      {isInstallingUpdate ? "Installing..." : "Install"}
                    </button>
                  ) : null}
                </motion.div>
              ) : null}
            </div>

            <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">
              <div>
                <h2 className="text-4xl font-serif italic text-foreground">{greeting}</h2>
                <p className="mt-1 text-lg text-muted-foreground">Review recent matters or start a new Section 32 analysis.</p>
              </div>
              <Button
                onClick={onProcessNew}
                className="group h-14 rounded-2xl bg-primary px-8 text-lg font-bold text-white shadow-xl shadow-primary/20 transition-all active:scale-95 md:hover:scale-105"
              >
                <Plus className="mr-3 h-6 w-6 transition-transform group-hover:rotate-90" />
                New Section 32
              </Button>
            </div>

            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-muted-foreground">
                  <Zap className="h-4 w-4 text-amber-500" />
                  Recent activity
                </h3>
                {recentRuns.length > 0 ? (
                  <button
                    onClick={onProcessNew}
                    className="flex items-center gap-1 text-xs font-bold text-primary hover:underline"
                  >
                    {/* New matter <ArrowRight className="h-3 w-3" /> */}
                  </button>
                ) : null}
              </div>

              <div className="overflow-hidden rounded-3xl border border-border bg-card shadow-sm">
                {isLoadingRuns ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-border bg-muted/50">
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Client & address</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Vol/Folio</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Created</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Time taken</th>
                          <th className="px-8 py-5 text-right text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {Array.from({ length: 3 }).map((_, i) => (
                          <tr key={i} className="animate-pulse">
                            <td className="px-8 py-6">
                              <div className="h-4 w-40 rounded bg-muted mb-1.5" />
                              <div className="h-3 w-56 rounded bg-muted" />
                            </td>
                            <td className="px-8 py-6"><div className="h-7 w-24 rounded-lg bg-muted" /></td>
                            <td className="px-8 py-6"><div className="h-4 w-20 rounded bg-muted" /></td>
                            <td className="px-8 py-6"><div className="h-4 w-14 rounded bg-muted" /></td>
                            <td className="px-8 py-6 text-right"><div className="ml-auto h-7 w-20 rounded-full bg-muted" /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : recentRuns.length === 0 ? (
                  <div className="p-10 text-center">
                    <p className="text-base font-bold text-foreground">No recent matters yet.</p>
                    <p className="mt-1 text-sm text-muted-foreground">Start your first Section 32 upload to build activity history.</p>
                    <Button onClick={onProcessNew} className="mt-6 h-11 rounded-xl px-5 font-bold bg-primary text-white">
                      <Plus className="mr-2 h-4 w-4" />
                      New Matter
                    </Button>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-border bg-muted/50">
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Client & address</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Vol/Folio</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Created</th>
                          <th className="px-8 py-5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Time taken</th>
                          <th className="px-8 py-5 text-right text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {(showAllRuns ? recentRuns : recentRuns.slice(0, INITIAL_RUN_LIMIT)).map((run) => {
                          const badge = getStatusBadge(run.status);
                          const StatusIcon = badge.icon;
                          return (
                          <tr
                            key={run.run_id}
                            onClick={() => onViewRun(run.run_id)}
                            className="group cursor-pointer transition-colors hover:bg-muted/50"
                          >
                            <td className="px-8 py-6">
                              <div className="flex flex-col">
                                <span className="text-base font-bold text-foreground transition-colors group-hover:text-primary">
                                  {run.client_name || "Matter Client"}
                                </span>
                                <span className="mt-0.5 text-sm text-muted-foreground">{run.property_address || "-"}</span>
                              </div>
                            </td>
                            <td className="px-8 py-6">
                              <span className="rounded-lg border border-border bg-muted px-3 py-1.5 font-mono text-xs text-muted-foreground">
                                {run.volume_folio || "-"}
                              </span>
                            </td>
                            <td className="px-8 py-6 text-sm text-muted-foreground">
                              <span className="inline-flex items-center gap-2">
                                <Clock className="h-3.5 w-3.5" />
                                {formatDate(run.created_at)}
                              </span>
                            </td>
                            <td className="px-8 py-6 text-sm text-muted-foreground">
                              <span className="inline-flex items-center gap-2">
                                <Timer className="h-3.5 w-3.5" />
                                {formatTimeTaken(run.time_taken_seconds)}
                              </span>
                            </td>
                            <td className="px-8 py-6 text-right">
                              <div className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider ${badge.className}`}>
                                <StatusIcon className="h-3.5 w-3.5" />
                                {badge.label}
                              </div>
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {!showAllRuns && recentRuns.length > INITIAL_RUN_LIMIT ? (
                      <div className="border-t border-border px-8 py-4 text-center">
                        <button
                          onClick={async () => {
                            if (onLoadAllRuns) await onLoadAllRuns();
                            setShowAllRuns(true);
                          }}
                          disabled={isLoadingAllRuns}
                          className="text-sm font-semibold text-primary hover:underline disabled:opacity-50"
                        >
                          {isLoadingAllRuns ? "Loading..." : `See more (${recentRuns.length - INITIAL_RUN_LIMIT} more)`}
                        </button>
                      </div>
                    ) : showAllRuns && recentRuns.length > INITIAL_RUN_LIMIT ? (
                      <div className="border-t border-border px-8 py-4 text-center">
                        <button
                          onClick={() => setShowAllRuns(false)}
                          className="text-sm font-semibold text-muted-foreground hover:underline"
                        >
                          Show less
                        </button>
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          </div>
        </main>

        {showQuickSetup && quickSetupForm ? (
          <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-6 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, y: 14, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              className="w-full max-w-2xl overflow-hidden rounded-[2rem] border border-border bg-card shadow-2xl"
            >
              <div className="flex items-start justify-between gap-4 border-b border-border bg-muted/40 px-6 py-5">
                <div className="flex items-start gap-4">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-white shadow-lg shadow-primary/20">
                    <Wand2 className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="text-xl font-bold text-foreground">Quick Setup</h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Let&apos;s get this workspace ready with the main settings you&apos;ll use every day.
                    </p>
                  </div>
                </div>
                <button
                  onClick={onDismissQuickSetup}
                  className="rounded-xl p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  aria-label="Close quick setup"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-6 px-6 py-6 md:grid-cols-2">
                <div className="space-y-4">
                  <div className="rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <Bot className="h-4 w-4 text-blue-500" />
                      AI provider
                    </div>
                    <div className="mt-3 grid gap-2">
                      {(["openai", "anthropic", "hybrid"] as const).map((provider) => (
                        <button
                          key={provider}
                          onClick={() => setQuickSetupForm((current) => current ? ({
                            ...current,
                            aiProvider: provider,
                            defaultModelName:
                              provider === "anthropic" || provider === "hybrid"
                                ? "claude-sonnet-4-6"
                                : "gpt-4.1-mini",
                          }) : current)}
                          className={[
                            "rounded-xl border px-3 py-2 text-left text-sm transition-colors",
                            quickSetupForm.aiProvider === provider
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border bg-card text-foreground hover:border-primary/40",
                          ].join(" ")}
                        >
                          {provider === "openai" ? "OpenAI" : provider === "anthropic" ? "Anthropic" : "OpenAI + Claude"}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <KeyRound className="h-4 w-4 text-emerald-500" />
                      API key
                    </div>
                    <Input
                      type="password"
                      value={
                        quickSetupForm.aiProvider === "anthropic" || quickSetupForm.aiProvider === "hybrid"
                          ? quickSetupForm.anthropicApiKey
                          : quickSetupForm.openAiApiKey
                      }
                      onChange={(event) =>
                        setQuickSetupForm((current) =>
                          current
                            ? quickSetupForm.aiProvider === "anthropic" || quickSetupForm.aiProvider === "hybrid"
                              ? { ...current, anthropicApiKey: event.target.value }
                              : { ...current, openAiApiKey: event.target.value }
                            : current,
                        )
                      }
                      placeholder={
                        quickSetupForm.aiProvider === "anthropic" || quickSetupForm.aiProvider === "hybrid"
                          ? "sk-ant-..."
                          : "sk-..."
                      }
                      className="mt-3 h-11 border-border bg-card"
                    />
                    <p className="mt-2 text-xs text-muted-foreground">Stored locally on this desktop app.</p>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <FolderOpen className="h-4 w-4 text-amber-500" />
                      Convey executable
                    </div>
                    <div className="mt-3 flex gap-2">
                      <Input
                        value={quickSetupForm.triconveyPath}
                        onChange={(event) =>
                          setQuickSetupForm((current) => current ? { ...current, triconveyPath: event.target.value } : current)
                        }
                        placeholder="C:\\Program Files\\TriConvey\\TriConvey.exe"
                        className="h-11 border-border bg-card"
                      />
                      <Button type="button" variant="outline" className="h-11 rounded-xl" onClick={onBrowseTriconveyPath}>
                        Browse
                      </Button>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">Used for desktop autofill into Convey.</p>
                  </div>

                  <div className="rounded-2xl border border-border bg-muted/50 p-4">
                    <p className="text-sm font-semibold text-foreground">What this sets up</p>
                    <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        AI review and document Q&A
                      </div>
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        Convey autofill path
                      </div>
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        Your default workspace behavior
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-end gap-3 border-t border-border px-6 py-5">
                <Button type="button" variant="outline" className="rounded-xl" onClick={onDismissQuickSetup}>
                  Remind me later
                </Button>
                <Button
                  type="button"
                  className="rounded-xl bg-primary px-5 text-white transition-all hover:bg-primary/90"
                  disabled={quickSetupSaving}
                  onClick={async () => {
                    if (!quickSetupForm || !onSaveQuickSetup) return;
                    setQuickSetupSaving(true);
                    try {
                      await onSaveQuickSetup(quickSetupForm);
                    } finally {
                      setQuickSetupSaving(false);
                    }
                  }}
                >
                  {quickSetupSaving ? "Saving..." : "Save and continue"}
                </Button>
              </div>
            </motion.div>
          </div>
        ) : null}

        <Chatbot
          isOpen={isChatOpen}
          onClose={() => setIsChatOpen(false)}
          onAsk={onAskAssistant}
          onApplyPatch={onApplyPatch}
          runId={activeRunId}
          userName={userName}
          chatContext="dashboard"
          onResolveTriconveyReference={onResolveTriconveyReference}
        />
      </div>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
      `}</style>
    </div>
  );
}
