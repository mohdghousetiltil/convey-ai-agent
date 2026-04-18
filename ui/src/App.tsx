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
  AnswerUpdatePayload,
  AutofillJobPayload,
  cancelAutofillJob,
  getSettings,
  getAutofillJob,
  ReviewRunPayload,
  createRun,
  saveAnswers,
  saveSettings,
  startAutofillJob,
} from "./lib/api";

type ViewState = "upload" | "loading" | "main" | "settings" | "profile" | "policy";

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
    defaultModelName: "gpt-4.1-mini",
    triconveyPath: "",
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

  // Fetch settings once the user is authenticated
  useEffect(() => {
    if (!user) return;
    void (async () => {
      try {
        const nextSettings = await getSettings();
        setSettings(nextSettings);
      } catch {
        // Keep defaults if backend settings are not available yet.
      }
    })();
  }, [user]);

  // Poll autofill job status
  useEffect(() => {
    if (!autofillJob || !["queued", "running", "cancelling"].includes(autofillJob.status)) {
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
      setUploadError(error instanceof Error ? error.message : "Failed to analyze the uploaded PDFs.");
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

  const handleLogout = async () => {
    setRun(null);
    setUploadError("");
    setAutofillJob(null);
    setAutofilling(false);
    setView("upload");
    await logout();
  };

  const handleAskAssistant = async (question: string) => {
    if (!run) throw new Error("No review run is loaded yet.");
    return askRunQuestion(run.manifest.run_id, question, { model: settings.defaultModelName });
  };

  const handleSaveSettings = (nextSettings: typeof settings) => {
    void (async () => {
      const saved = await saveSettings(nextSettings);
      setSettings(saved);
    })();
    setView("main");
  };

  // ── View routing ─────────────────────────────────────────────────────────────

  switch (view) {
    case "upload":
      return (
        <UploadScreen
          onUploadComplete={handleUploadComplete}
          errorMessage={uploadError}
        />
      );
    case "loading":
      return (
        <LoadingScreen
          message={loadingMessage}
          cancellable={Boolean(autofillJob && ["queued", "running", "cancelling"].includes(autofillJob.status))}
          onCancel={handleCancelAutofill}
          cancelLabel={autofillJob?.status === "cancelling" ? "Cancelling..." : "Cancel Autofill"}
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
          isSaving={saving}
          isAutofilling={autofilling}
          errorMessage={uploadError}
          onDismissError={() => setUploadError("")}
        />
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
      return <ClientPolicyScreen onBack={() => setView("main")} />;
    default:
      return (
        <UploadScreen
          onUploadComplete={handleUploadComplete}
          errorMessage={uploadError}
        />
      );
  }
}
