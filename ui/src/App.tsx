import React, { useEffect, useState } from "react";
import { UploadScreen } from "./components/UploadScreen";
import { LoadingScreen } from "./components/LoadingScreen";
import { ReviewScreen } from "./components/ReviewScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { ProfileScreen } from "./components/ProfileScreen";
import { ClientPolicyScreen } from "./components/ClientPolicyScreen";
import { LoginScreen } from "./components/LoginScreen";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import {
  askRunQuestion,
  applyAnswerPatches,
  AnswerUpdatePayload,
  AutofillJobPayload,
  cancelAutofillJob,
  continueAutofillJob,
  getSettings,
  getAutofillJob,
  ReviewRunPayload,
  createRun,
  saveAnswers,
  saveSettings,
  startAutofillJob,
} from "./lib/api";

type ViewState = "upload" | "loading" | "main" | "settings" | "profile" | "policy";

type LocalSettingsForm = {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  aiProvider: "openai" | "anthropic";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
};

function firstRunKey(userId: string) {
  return `convey:onboarded:${userId}`;
}

function sessionDismissKey(userId: string) {
  return `convey:onboarding:dismissed:${userId}`;
}

function isSettingsConfigured(settings: LocalSettingsForm): boolean {
  const hasProviderKey =
    settings.aiProvider === "anthropic"
      ? Boolean(settings.anthropicApiKey?.trim())
      : Boolean(settings.openAiApiKey?.trim());
  return Boolean(settings.defaultModelName?.trim()) && hasProviderKey && Boolean(settings.triconveyPath?.trim());
}

function FirstRunSetupModal({
  open,
  settings,
  saving,
  onChange,
  onSave,
  onLater,
}: {
  open: boolean;
  settings: LocalSettingsForm;
  saving: boolean;
  onChange: (next: LocalSettingsForm) => void;
  onSave: () => void;
  onLater: () => void;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/40 px-4">
      <div className="w-full max-w-3xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl">
        <div className="border-b border-slate-100 px-6 py-5">
          <h2 className="text-xl font-bold text-slate-900">Finish your desktop setup</h2>
          <p className="mt-1 text-sm text-slate-500">
            Save your AI provider, API key, default model, and Convey path so uploads, chat, and autofill work smoothly on this machine.
          </p>
        </div>

        <div className="grid gap-6 px-6 py-6 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">Language</label>
            <select
              value={settings.language}
              onChange={(e) => onChange({ ...settings, language: e.target.value })}
              className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="English">English</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">AI Provider</label>
            <select
              value={settings.aiProvider}
              onChange={(e) =>
                onChange({
                  ...settings,
                  aiProvider: e.target.value as "openai" | "anthropic",
                  defaultModelName: e.target.value === "anthropic" ? "claude-sonnet-4-6" : "gpt-4.1-mini",
                })
              }
              className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>

          <div className="space-y-2 md:col-span-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              {settings.aiProvider === "anthropic" ? "Anthropic API key" : "OpenAI API key"}
            </label>
            <input
              type="password"
              value={settings.aiProvider === "anthropic" ? settings.anthropicApiKey : settings.openAiApiKey}
              onChange={(e) =>
                onChange({
                  ...settings,
                  ...(settings.aiProvider === "anthropic"
                    ? { anthropicApiKey: e.target.value }
                    : { openAiApiKey: e.target.value }),
                })
              }
              placeholder={settings.aiProvider === "anthropic" ? "sk-ant-..." : "sk-..."}
              className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3.5 text-sm text-slate-900 placeholder-slate-400 outline-none focus:border-primary focus:bg-white focus:ring-1 focus:ring-primary"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">Default model</label>
            <input
              value={settings.defaultModelName}
              onChange={(e) => onChange({ ...settings, defaultModelName: e.target.value })}
              placeholder={settings.aiProvider === "anthropic" ? "claude-sonnet-4-6" : "gpt-4.1-mini"}
              className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3.5 text-sm text-slate-900 placeholder-slate-400 outline-none focus:border-primary focus:bg-white focus:ring-1 focus:ring-primary"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">Convey executable path</label>
            <input
              value={settings.triconveyPath}
              onChange={(e) => onChange({ ...settings, triconveyPath: e.target.value })}
              placeholder="C:\\Program Files\\TriConvey\\TriConvey.exe"
              className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3.5 text-sm text-slate-900 placeholder-slate-400 outline-none focus:border-primary focus:bg-white focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-slate-100 px-6 py-4">
          <button
            onClick={onLater}
            className="text-sm font-semibold text-slate-500 transition-colors hover:text-slate-700"
          >
            Later
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            className="rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-primary/20 transition-all disabled:opacity-50 disabled:shadow-none"
          >
            {saving ? "Saving..." : "Save and continue"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root — wraps the whole app in AuthProvider so every descendant can useAuth()
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

// ---------------------------------------------------------------------------
// AppContent — rendered inside AuthProvider; handles auth gate + app routing
// ---------------------------------------------------------------------------

function AppContent() {
  // ── Auth ────────────────────────────────────────────────────────────────────
  const { user, isLoading, logout } = useAuth();

  // ── App state (hooks must always run — even before auth gate) ───────────────
  const [settings, setSettings] = useState({
    language: "English",
    openAiApiKey: "",
    anthropicApiKey: "",
    aiProvider: "openai" as "openai" | "anthropic",
    defaultModelName: "gpt-4.1-mini",
    triconveyPath: "",
    preferredAutofillFields: [] as string[],
  });
  const [view, setView] = useState<ViewState>("upload");
  const [run, setRun] = useState<ReviewRunPayload | null>(null);
  const [loadingMessage, setLoadingMessage] = useState(
    "Extracting Section 32 answers, review flags, and autofill actions...",
  );
  const [uploadError, setUploadError] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [autofilling, setAutofilling] = useState(false);
  const [autofillJob, setAutofillJob] = useState<AutofillJobPayload | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [showSetupModal, setShowSetupModal] = useState(false);
  const [setupSaving, setSetupSaving] = useState(false);

  // Fetch settings once the user is authenticated
  useEffect(() => {
    if (!user) {
      setSettingsLoaded(false);
      setShowSetupModal(false);
      return;
    }
    void (async () => {
      try {
        const nextSettings = await getSettings();
        setSettings(nextSettings);
        const seen = localStorage.getItem(firstRunKey(user.user_id)) === "true";
        const dismissed = sessionStorage.getItem(sessionDismissKey(user.user_id)) === "true";
        setShowSetupModal((!seen || !isSettingsConfigured(nextSettings)) && !dismissed);
      } catch {
        // Keep defaults if backend settings are not available yet.
        const seen = localStorage.getItem(firstRunKey(user.user_id)) === "true";
        const dismissed = sessionStorage.getItem(sessionDismissKey(user.user_id)) === "true";
        setShowSetupModal((!seen || !isSettingsConfigured(settings)) && !dismissed);
      } finally {
        setSettingsLoaded(true);
      }
    })();
  }, [user]);

  // Poll autofill job status
  useEffect(() => {
    if (!autofillJob || !["queued", "running", "cancelling", "awaiting_user"].includes(autofillJob.status)) {
      return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const nextJob = await getAutofillJob(autofillJob.job_id);
        setAutofillJob(nextJob);
        if (nextJob.status === "completed" && nextJob.result) {
          setRun(nextJob.result);
          setView("main");
          setAutofilling(false);
          setAutofillJob(null);
        } else if (nextJob.status === "cancelled") {
          if (nextJob.result) setRun(nextJob.result);
          setUploadError("Autofill was cancelled.");
          setView("main");
          setAutofilling(false);
          setAutofillJob(null);
        } else if (nextJob.status === "awaiting_user") {
          setLoadingMessage(
            nextJob.manual_action?.message ||
              "Please open Property Details in Convey, then press Continue.",
          );
        } else if (nextJob.status === "failed") {
          setUploadError(nextJob.error || "Autofill failed.");
          setView("main");
          setAutofilling(false);
          setAutofillJob(null);
        }
      } catch (error) {
        setUploadError(error instanceof Error ? error.message : "Could not refresh autofill status.");
        setView("main");
        setAutofilling(false);
      }
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [autofillJob]);

  // ── Auth gate — must come after all hooks ────────────────────────────────────
  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <svg
          className="animate-spin h-8 w-8 text-slate-400"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    );
  }

  if (!user) {
    return <LoginScreen />;
  }

  // ── Event handlers ───────────────────────────────────────────────────────────

  const handleUploadComplete = async (files: File[]) => {
    setUploadError("");
    setAutofillJob(null);
    setLoadingMessage("Extracting Section 32 answers, review flags, and autofill actions...");
    setView("loading");
    try {
      const nextRun = await createRun(files, {
        model: settings.defaultModelName,
        triconveyExe: settings.triconveyPath || null,
      });
      setRun(nextRun);
      setView("main");
    } catch (error) {
      const message =
        error instanceof TypeError && error.message === "Failed to fetch"
          ? "Could not reach the local backend. Restart the app/backend and try again."
          : error instanceof Error
            ? error.message
            : "Failed to analyze the uploaded PDFs.";
      setUploadError(message);
      setView("upload");
    }
  };

  const handleSaveReview = async (updates: Record<string, AnswerUpdatePayload>) => {
    if (!run || saving) return;
    setSaving(true);
    try {
      const nextRun = await saveAnswers(run.manifest.run_id, updates);
      setRun(nextRun);
    } finally {
      setSaving(false);
    }
  };

  const handleAutofill = async (updates: Record<string, AnswerUpdatePayload>) => {
    if (!run || autofilling) return;
    setAutofilling(true);
    try {
      const savedRun = Object.keys(updates).length
        ? await saveAnswers(run.manifest.run_id, updates)
        : run;
      setRun(savedRun);
      setLoadingMessage("Starting Convey autofill and waiting for execution feedback...");
      setView("loading");
      const job = await startAutofillJob(savedRun.manifest.run_id, {
        skipReviewGate: true,
        triconveyExe: settings.triconveyPath || undefined,
      });
      setAutofillJob(job);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Autofill failed.");
      setView("main");
      setAutofilling(false);
    }
  };

  const handleCancelAutofill = async () => {
    if (!autofillJob) return;
    try {
      const job = await cancelAutofillJob(autofillJob.job_id);
      setAutofillJob(job);
      setLoadingMessage("Cancelling Convey autofill...");
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not cancel autofill.");
      setView("main");
      setAutofilling(false);
    }
  };

  const handleContinueAutofill = async () => {
    if (!autofillJob) return;
    try {
      const job = await continueAutofillJob(autofillJob.job_id);
      setAutofillJob(job);
      setLoadingMessage("Resuming Convey autofill from Property Details...");
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not continue autofill.");
      setView("main");
      setAutofilling(false);
    }
  };

  const handleLogout = async () => {
    setRun(null);
    setUploadError("");
    setAutofillJob(null);
    setAutofilling(false);
    setView("upload");
    await logout();
  };

  const handleAskAssistant = async (
    question: string,
    history: Array<{ role: "user" | "assistant"; content: string }>,
    mode: "quick" | "standard" | "thorough",
    signal?: AbortSignal,
  ) => {
    if (!run) throw new Error("No review run is loaded yet.");
    return askRunQuestion(run.manifest.run_id, question, {
      history,
      mode,
      signal,
    });
  };

  const handleApplyPatch = async (questionId: string, newValue: string, _reason: string) => {
    if (!run) return;
    const nextRun = await applyAnswerPatches(run.manifest.run_id, [
      { question_id: questionId, new_value: newValue, reason: _reason },
    ]) as ReviewRunPayload;
    setRun(nextRun);
  };

  const handleSaveSettings = async (nextSettings: typeof settings) => {
    const saved = await saveSettings(nextSettings);
    setSettings(saved);
    if (user) {
      localStorage.setItem(firstRunKey(user.user_id), "true");
      sessionStorage.removeItem(sessionDismissKey(user.user_id));
    }
    setShowSetupModal(false);
    setView("main");
  };

  const handleSaveSetupModal = async () => {
    if (!user || setupSaving) return;
    setSetupSaving(true);
    try {
      const saved = await saveSettings(settings);
      setSettings(saved);
      localStorage.setItem(firstRunKey(user.user_id), "true");
      sessionStorage.removeItem(sessionDismissKey(user.user_id));
      setShowSetupModal(false);
    } finally {
      setSetupSaving(false);
    }
  };

  const handleLaterSetup = () => {
    if (user) {
      sessionStorage.setItem(sessionDismissKey(user.user_id), "true");
    }
    setShowSetupModal(false);
  };

  // ── View routing ─────────────────────────────────────────────────────────────

  switch (view) {
    case "upload":
      return (
        <>
          <UploadScreen
            onUploadComplete={handleUploadComplete}
            errorMessage={uploadError}
          />
          <FirstRunSetupModal
            open={settingsLoaded && showSetupModal}
            settings={settings}
            saving={setupSaving}
            onChange={setSettings}
            onSave={handleSaveSetupModal}
            onLater={handleLaterSetup}
          />
        </>
      );
    case "loading":
      return (
        <LoadingScreen
          message={loadingMessage}
          cancellable={Boolean(autofillJob && ["queued", "running", "cancelling", "awaiting_user"].includes(autofillJob.status))}
          onCancel={handleCancelAutofill}
          cancelLabel={autofillJob?.status === "cancelling" ? "Cancelling..." : "Cancel Autofill"}
          actionLabel={autofillJob?.status === "awaiting_user" ? (autofillJob.manual_action?.cta || "Continue") : undefined}
          onAction={autofillJob?.status === "awaiting_user" ? handleContinueAutofill : undefined}
        />
      );
    case "main":
      if (!run) {
        return (
          <UploadScreen
            onUploadComplete={handleUploadComplete}
            errorMessage="No review run is loaded yet."
          />
        );
      }
      return (
        <>
          <ReviewScreen
            run={run}
            onBack={() => setView("upload")}
            onProfile={() => setView("profile")}
            onSettings={() => setView("settings")}
          onPolicy={() => setView("policy")}
            onLogout={handleLogout}
            onSaveReview={handleSaveReview}
            onAutofill={handleAutofill}
            onAskAssistant={handleAskAssistant}
            onApplyPatch={handleApplyPatch}
            isSaving={saving}
            isAutofilling={autofilling}
            errorMessage={uploadError}
            onDismissError={() => setUploadError("")}
          />
          <FirstRunSetupModal
            open={settingsLoaded && showSetupModal}
            settings={settings}
            saving={setupSaving}
            onChange={setSettings}
            onSave={handleSaveSetupModal}
            onLater={handleLaterSetup}
          />
        </>
      );
    case "settings":
      return (
        <SettingsScreen
          onBack={() => setView("main")}
          settings={settings}
          onSaveSettings={handleSaveSettings}
        />
      );
    case "profile":
      return <ProfileScreen onBack={() => setView("main")} />;
    case "policy":
      return (
        <ClientPolicyScreen
          onBack={() => setView("main")}
          settings={settings}
          onSaveSettings={handleSaveSettings}
        />
      );
    default:
      return (
        <UploadScreen
          onUploadComplete={handleUploadComplete}
          errorMessage={uploadError}
        />
      );
  }
}
