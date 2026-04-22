import React, { useEffect, useState } from "react";
import { UploadScreen } from "./components/UploadScreen";
import { LoadingScreen } from "./components/LoadingScreen";
import { ReviewScreen } from "./components/ReviewScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { ProfileScreen } from "./components/ProfileScreen";
import { AboutScreen } from "./components/AboutScreen";
import { ClientPolicyScreen } from "./components/ClientPolicyScreen";
import { LoginScreen } from "./components/LoginScreen";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import {
  askRunQuestion,
  AppInfoPayload,
  applyAnswerPatches,
  AnswerUpdatePayload,
  AutofillJobPayload,
  cancelAutofillJob,
  checkForUpdates,
  CloudSyncStatusPayload,
  continueAutofillJob,
  downloadUpdateInstaller,
  getAppInfo,
  getSettings,
  getAutofillJob,
  getRun,
  ReviewRunPayload,
  createRun,
  saveAnswers,
  saveSettings,
  startAutofillJob,
  UpdateStatusPayload,
} from "./lib/api";

type ViewState = "upload" | "loading" | "main" | "settings" | "profile" | "policy" | "about";
type LoadingKind = "analysis" | "autofill" | null;

type LocalSettingsForm = {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  aiProvider: "openai" | "anthropic" | "hybrid";
  aiMode: "cost_efficient" | "all_time_best" | "turbo";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
  updateRepository: string;
  includePrereleaseUpdates: boolean;
  autoCheckForUpdates: boolean;
  cloudSyncEnabled: boolean;
};

type DesktopBridge = {
  pywebview?: {
    api?: {
      launch_installer?: (installerPath: string) => Promise<boolean> | boolean;
      close_app?: () => void;
      pick_triconvey_executable?: () => Promise<string | null> | string | null;
      open_local_data_dir?: () => Promise<boolean> | boolean;
    };
  };
};

function firstRunKey(userId: string) {
  return `convey:onboarded:${userId}`;
}

function sessionDismissKey(userId: string) {
  return `convey:onboarding:dismissed:${userId}`;
}

function activeRunKey(userId: string) {
  return `convey:active-run:${userId}`;
}

function activeAutofillJobKey(userId: string) {
  return `convey:active-autofill-job:${userId}`;
}

function clearPersistedWorkflowState(userId: string) {
  localStorage.removeItem(activeRunKey(userId));
  localStorage.removeItem(activeAutofillJobKey(userId));
}


function UpdateBanner({
  update,
  busy,
  error,
  onDismiss,
  onInstall,
}: {
  update: UpdateStatusPayload;
  busy: boolean;
  error: string;
  onDismiss: () => void;
  onInstall: () => void;
}) {
  return (
    <div className="fixed bottom-5 right-5 z-[120] w-full max-w-md rounded-3xl border border-amber-200 bg-white/95 p-5 shadow-2xl backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Update Available</p>
          <h3 className="mt-1 text-lg font-bold text-slate-900">Convey Agent {update.latest_version ?? "latest"}</h3>
          <p className="mt-1 text-sm text-slate-600">
            You are on {update.current_version}. A newer installer is ready to download and replace this app.
          </p>
        </div>
        <button onClick={onDismiss} className="text-xs font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-600">
          Later
        </button>
      </div>
      {error ? <p className="mt-3 text-sm font-medium text-rose-600">{error}</p> : null}
      <div className="mt-4 flex items-center gap-3">
        {update.release_url ? (
          <a
            href={update.release_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-semibold text-slate-500 hover:text-slate-700"
          >
            Release notes
          </a>
        ) : null}
        <button
          onClick={onInstall}
          disabled={busy}
          className="ml-auto rounded-xl bg-primary px-4 py-2 text-sm font-bold text-white disabled:opacity-60"
        >
          {busy ? "Preparing installer..." : "Download and install"}
        </button>
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
    aiProvider: "openai" as "openai" | "anthropic" | "hybrid",
    aiMode: "cost_efficient" as "cost_efficient" | "all_time_best" | "turbo",
    defaultModelName: "gpt-4.1-mini",
    triconveyPath: "",
    preferredAutofillFields: [] as string[],
    updateRepository: "",
    includePrereleaseUpdates: false,
    autoCheckForUpdates: true,
    cloudSyncEnabled: true,
  });
  const [view, setView] = useState<ViewState>("upload");
  const [run, setRun] = useState<ReviewRunPayload | null>(null);
  const [loadingMessage, setLoadingMessage] = useState(
    "Extracting Section 32 answers, review flags, and autofill actions...",
  );
  const [loadingKind, setLoadingKind] = useState<LoadingKind>(null);
  const [uploadError, setUploadError] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [autofilling, setAutofilling] = useState(false);
  const [autofillJob, setAutofillJob] = useState<AutofillJobPayload | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [appInfo, setAppInfo] = useState<AppInfoPayload | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusPayload | null>(null);
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const [installingUpdate, setInstallingUpdate] = useState(false);
  const [updateError, setUpdateError] = useState("");
  const [dismissedUpdateVersion, setDismissedUpdateVersion] = useState<string | null>(null);
  const [cloudSyncStatus, setCloudSyncStatus] = useState<CloudSyncStatusPayload | null>(null);

  // Fetch settings once the user is authenticated
  useEffect(() => {
    if (!user) {
      setSettingsLoaded(false);
      setAppInfo(null);
      setCloudSyncStatus(null);
      return;
    }
    void (async () => {
      try {
        const nextSettings = await getSettings();
        setSettings(nextSettings);
        try {
          setAppInfo(await getAppInfo());
        } catch {
          setAppInfo({ name: "Convey Agent", publisher: "Convey Agent", version: "0.0.1" });
        }
      } catch {
        // Keep defaults if backend settings are not available yet.
      } finally {
        setSettingsLoaded(true);
      }
    })();
  }, [user]);


  useEffect(() => {
    if (!updateStatus?.latest_version) {
      return;
    }
    if (dismissedUpdateVersion && dismissedUpdateVersion !== updateStatus.latest_version) {
      setDismissedUpdateVersion(null);
    }
  }, [updateStatus?.latest_version, dismissedUpdateVersion]);

  useEffect(() => {
    if (!user) {
      return;
    }

    let cancelled = false;
    const savedRunId = localStorage.getItem(activeRunKey(user.user_id));
    const savedJobId = localStorage.getItem(activeAutofillJobKey(user.user_id));

    void (async () => {
      if (savedRunId) {
        try {
          const savedRun = await getRun(savedRunId);
          if (!cancelled) {
            setRun(savedRun);
            if (!savedJobId) {
              setView("main");
            }
          }
        } catch {
          localStorage.removeItem(activeRunKey(user.user_id));
        }
      }

      if (savedJobId) {
        try {
          const savedJob = await getAutofillJob(savedJobId);
          if (cancelled) return;
          setAutofillJob(savedJob);
          if (["queued", "running", "cancelling", "awaiting_user"].includes(savedJob.status)) {
            setAutofilling(true);
            setLoadingMessage("Starting Convey autofill and waiting for execution feedback...");
            setLoadingKind("autofill");
            setView("loading");
          } else if (savedJob.status === "completed") {
            if (savedJob.result) {
              setRun(savedJob.result);
            }
            setAutofilling(false);
            setAutofillJob(null);
            setLoadingKind(null);
            setView("main");
          } else if (savedJob.status === "cancelled" || savedJob.status === "failed") {
            setAutofilling(false);
            setAutofillJob(null);
            setLoadingKind(null);
            setView("main");
          }
        } catch {
          localStorage.removeItem(activeAutofillJobKey(user.user_id));
          if (!cancelled) {
            setLoadingKind(null);
            setView(savedRunId ? "main" : "upload");
          }
        }
      } else if (!cancelled) {
        setLoadingKind(null);
        setView(savedRunId ? "main" : "upload");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => {
    if (!user) {
      return;
    }
    if (run?.manifest.run_id) {
      localStorage.setItem(activeRunKey(user.user_id), run.manifest.run_id);
    } else {
      localStorage.removeItem(activeRunKey(user.user_id));
    }
  }, [run, user]);

  useEffect(() => {
    if (!user) {
      return;
    }
    if (autofillJob?.job_id) {
      localStorage.setItem(activeAutofillJobKey(user.user_id), autofillJob.job_id);
    } else {
      localStorage.removeItem(activeAutofillJobKey(user.user_id));
    }
  }, [autofillJob, user]);

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
          setLoadingKind("autofill");
          setLoadingMessage("Autofill is being aborted, cancelling...");
          setView("loading");
          window.setTimeout(() => {
            setUploadError("Autofill was cancelled.");
            setView("main");
            setAutofilling(false);
            setAutofillJob(null);
            setLoadingKind(null);
          }, 900);
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
    setLoadingKind("analysis");
    setView("loading");
    try {
      const nextRun = await createRun(files, {
        useAiReview: true,
        model: settings.defaultModelName,
        triconveyExe: settings.triconveyPath || null,
      });
      setRun(nextRun);
      setLoadingKind(null);
      setView("main");
    } catch (error) {
      const message =
        error instanceof TypeError && error.message === "Failed to fetch"
          ? "Could not reach the local backend. Restart the app/backend and try again."
          : error instanceof Error
            ? error.message
            : "Failed to analyze the uploaded PDFs.";
      setUploadError(message);
      setLoadingKind(null);
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
      setLoadingKind("autofill");
      setView("loading");
      const job = await startAutofillJob(savedRun.manifest.run_id, {
        skipReviewGate: true,
        triconveyExe: settings.triconveyPath || undefined,
      });
      setAutofillJob(job);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Autofill failed.");
      setLoadingKind(null);
      setView("main");
      setAutofilling(false);
    }
  };

  const handleCancelAutofill = async () => {
    if (!autofillJob) return;
    try {
      setLoadingKind("autofill");
      setLoadingMessage("Autofill is being aborted, cancelling...");
      setView("loading");
      const job = await cancelAutofillJob(autofillJob.job_id);
      setAutofillJob(job);
      window.setTimeout(() => {
        setUploadError("Autofill was cancelled.");
        setView("main");
        setAutofilling(false);
        setAutofillJob(null);
        setLoadingKind(null);
      }, 900);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not cancel autofill.");
      setLoadingKind(null);
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
      setLoadingKind(null);
      setView("main");
      setAutofilling(false);
    }
  };

  const handleLogout = async () => {
    if (user) {
      clearPersistedWorkflowState(user.user_id);
    }
    setRun(null);
    setUploadError("");
    setAutofillJob(null);
    setAutofilling(false);
    setLoadingKind(null);
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
      aiMode: settings.aiMode,
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

  const handleBrowseTriconveyPath = async () => {
    const bridge = window as unknown as DesktopBridge;
    const pickExecutable = bridge.pywebview?.api?.pick_triconvey_executable;
    if (!pickExecutable) {
      setUpdateError("Desktop path browsing is only available in the installed Windows app.");
      return;
    }
    try {
      const selected = await Promise.resolve(pickExecutable());
      if (selected) {
        setSettings((current) => ({ ...current, triconveyPath: selected }));
      }
    } catch (error) {
      setUpdateError(error instanceof Error ? error.message : "Could not browse for the Convey executable.");
    }
  };

  const handleOpenLocalDataDir = async () => {
    const bridge = window as unknown as DesktopBridge;
    const openLocalDataDir = bridge.pywebview?.api?.open_local_data_dir;
    if (!openLocalDataDir) {
      setUpdateError("Open local data folder is only available in the installed Windows app.");
      return;
    }
    try {
      await Promise.resolve(openLocalDataDir());
    } catch (error) {
      setUpdateError(error instanceof Error ? error.message : "Could not open the local app-data folder.");
    }
  };

  const handleCheckForUpdates = async (manual = true) => {
    if (checkingUpdates) return;
    setCheckingUpdates(true);
    if (manual) {
      setUpdateError("");
    }
    try {
      const next = await checkForUpdates({
        includePrerelease: settings.includePrereleaseUpdates,
        updateRepository: settings.updateRepository || undefined,
      });
      setUpdateStatus(next);
      if (manual) {
        setUpdateError("");
      }
    } catch (error) {
      if (manual) {
        setUpdateError("");
      }
    } finally {
      setCheckingUpdates(false);
    }
  };

  const handleDownloadAndInstallUpdate = async () => {
    if (installingUpdate) return;
    setInstallingUpdate(true);
    setUpdateError("");
    try {
      const payload = await downloadUpdateInstaller({
        includePrerelease: settings.includePrereleaseUpdates,
        updateRepository: settings.updateRepository || undefined,
      });
      setUpdateStatus(payload.release);
      const bridge = window as unknown as DesktopBridge;
      const launchInstaller = bridge.pywebview?.api?.launch_installer;
      if (launchInstaller) {
        await Promise.resolve(launchInstaller(payload.download.installer_path));
        bridge.pywebview?.api?.close_app?.();
        return;
      }
      if (payload.release.release_url) {
        window.open(payload.release.release_url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      setUpdateError(error instanceof Error ? error.message : "Could not download the update installer.");
    } finally {
      setInstallingUpdate(false);
    }
  };

  const showUpdateBanner = false;

  // ── View routing ─────────────────────────────────────────────────────────────

  switch (view) {
    case "upload":
      return (
        <>
          <UploadScreen
            onUploadComplete={handleUploadComplete}
            errorMessage={uploadError}
          />
        </>
      );
    case "loading":
      if (loadingKind === null) {
        if (run) {
          return (
            <>
              <ReviewScreen
                run={run}
                onBack={() => setView("upload")}
                onProfile={() => setView("profile")}
                onSettings={() => setView("settings")}
                onAbout={() => setView("about")}
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
                updateStatus={updateStatus}
              />
            </>
          );
        }
        return (
          <>
            <UploadScreen
              onUploadComplete={handleUploadComplete}
              errorMessage={uploadError}
            />
            {showUpdateBanner && updateStatus ? (
              <UpdateBanner
                update={updateStatus}
                busy={installingUpdate}
                error={updateError}
                onDismiss={() => setDismissedUpdateVersion(updateStatus.latest_version ?? null)}
                onInstall={() => void handleDownloadAndInstallUpdate()}
              />
            ) : null}
          </>
        );
      }
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
          <>
            <UploadScreen
              onUploadComplete={handleUploadComplete}
              errorMessage="No review run is loaded yet."
            />
          </>
        );
      }
      return (
        <>
          <ReviewScreen
            run={run}
            onBack={() => setView("upload")}
            onProfile={() => setView("profile")}
            onSettings={() => setView("settings")}
            onAbout={() => setView("about")}
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
            updateStatus={updateStatus}
          />
        </>
      );
    case "settings":
      return (
        <SettingsScreen
          onBack={() => setView("main")}
          settings={settings}
          onSaveSettings={handleSaveSettings}
          appInfo={appInfo}
          updateStatus={updateStatus}
          isCheckingUpdates={checkingUpdates}
          isInstallingUpdate={installingUpdate}
          onCheckForUpdates={() => void handleCheckForUpdates(true)}
          onInstallUpdate={() => void handleDownloadAndInstallUpdate()}
          onBrowseTriconveyPath={() => void handleBrowseTriconveyPath()}
          onOpenLocalDataDir={() => void handleOpenLocalDataDir()}
        />
      );
    case "profile":
      return <ProfileScreen onBack={() => setView("main")} />;
    case "about":
      return (
        <AboutScreen
          onBack={() => setView("main")}
          appInfo={appInfo}
          updateStatus={updateStatus}
          updateMessage={updateError}
          isCheckingUpdates={checkingUpdates}
          isInstallingUpdate={installingUpdate}
          onCheckForUpdates={() => void handleCheckForUpdates(true)}
          onInstallUpdate={() => void handleDownloadAndInstallUpdate()}
        />
      );
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
