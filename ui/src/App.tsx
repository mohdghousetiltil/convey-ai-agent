import React, { useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { DashboardScreen } from "./components/DashboardScreen";
import type { RecentRun } from "./components/DashboardScreen";
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
  type AppInfoPayload,
  applyAnswerPatches,
  type AnswerUpdatePayload,
  type AutofillJobPayload,
  cancelAutofillJob,
  checkForUpdates,
  type CloudSyncStatusPayload,
  continueAutofillJob,
  createRun,
  deleteRun,
  downloadUpdateInstaller,
  reinstallCurrentVersion,
  getAppInfo,
  getAutofillJob,
  activateAiAnswers,
  getAllRuns,
  getRecentRuns,
  getRun,
  getSettings,
  type ReviewRunPayload,
  resolveTriconveyReference,
  saveAnswers,
  saveSettings,
  startAutofillJob,
  type UpdateStatusPayload,
} from "./lib/api";

type LoadingKind = "analysis" | "autofill" | null;

type LocalSettingsForm = {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  googleApiKey: string;
  openRouterApiKey: string;
  aiProvider: "openai" | "anthropic" | "google" | "hybrid" | "openrouter";
  aiMode: "cost_efficient" | "all_time_best" | "turbo";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
  updateRepository: string;
  includePrereleaseUpdates: boolean;
  autoCheckForUpdates: boolean;
  cloudSyncEnabled: boolean;
  theme: "light" | "dark" | "auto";
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

function recentRunsKey(userId: string) {
  return `convey:recent-runs:${userId}`;
}

function loadRecentRuns(userId: string): RecentRun[] {
  try {
    const raw = localStorage.getItem(recentRunsKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as RecentRun[]).filter((item) => typeof item?.run_id === "string") : [];
  } catch {
    return [];
  }
}

function saveRecentRuns(userId: string, runs: RecentRun[]) {
  try {
    localStorage.setItem(recentRunsKey(userId), JSON.stringify(runs.slice(0, 20)));
  } catch {
    // Ignore localStorage failures.
  }
}

function toRecentRun(run: ReviewRunPayload): RecentRun {
  return {
    run_id: run.manifest.run_id,
    client_name: run.matter.client_name || run.client_name || run.manifest.client_name || "",
    volume_folio: run.matter.volume_folio || "",
    property_address: run.matter.property_address || "",
    created_at: run.manifest.created_at,
    status: "complete",
  };
}

function upsertRecentRun(userId: string, run: ReviewRunPayload): RecentRun[] {
  const next = toRecentRun(run);
  const merged = [next, ...loadRecentRuns(userId).filter((item) => item.run_id !== next.run_id)];
  saveRecentRuns(userId, merged);
  return merged;
}

function clearPersistedWorkflowState(userId: string) {
  localStorage.removeItem(activeRunKey(userId));
  localStorage.removeItem(activeAutofillJobKey(userId));
}

function applyResolvedTheme(dark: boolean) {
  const html = document.documentElement;
  html.classList.add("theme-transitioning");
  if (dark) html.classList.add("dark");
  else html.classList.remove("dark");
  window.setTimeout(() => html.classList.remove("theme-transitioning"), 220);
}

function applyThemePreference(theme: "light" | "dark" | "auto") {
  if (theme === "dark") {
    applyResolvedTheme(true);
    return;
  }
  if (theme === "light") {
    applyResolvedTheme(false);
    return;
  }
  applyResolvedTheme(window.matchMedia("(prefers-color-scheme: dark)").matches);
}

// Apply theme immediately on first load to prevent flash of wrong theme
(function applyInitialTheme() {
  try {
    const saved = localStorage.getItem("convey:theme") as "light" | "dark" | "auto" | null;
    const isDark = saved === "dark" || (saved === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (isDark) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  } catch {
    // ignore
  }
})();

// HashRouter hard fallback for desktop/webview launches that open "/" with no hash.
(function ensureHashRoute() {
  try {
    if (typeof window === "undefined") return;
    if (!window.location.hash || window.location.hash === "#") {
      window.location.hash = "/dashboard";
    }
  } catch {
    // Ignore location write failures.
  }
})();

export default function App() {
  return (
    <AuthProvider>
      <HashRouter>
        <AppContent />
      </HashRouter>
    </AuthProvider>
  );
}

function AppContent() {
  const { user, isLoading, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [settings, setSettings] = useState<LocalSettingsForm>({
    language: "English",
    openAiApiKey: "",
    anthropicApiKey: "",
    googleApiKey: "",
    openRouterApiKey: "",
    aiProvider: "openrouter",
    aiMode: "cost_efficient",
    defaultModelName: "nvidia/nemotron-3-super-120b-a12b:free",
    triconveyPath: "",
    preferredAutofillFields: [],
    updateRepository: "",
    includePrereleaseUpdates: false,
    autoCheckForUpdates: true,
    cloudSyncEnabled: true,
    theme: "light",
  });
  // Apply theme class to <html> whenever settings.theme changes
  useEffect(() => {
    if (settings.theme === "dark") {
      applyResolvedTheme(true);
    } else if (settings.theme === "light") {
      applyResolvedTheme(false);
    } else {
      // auto: follow system
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      applyResolvedTheme(mq.matches);
      const handler = (e: MediaQueryListEvent) => applyResolvedTheme(e.matches);
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [settings.theme]);

  const [run, setRun] = useState<ReviewRunPayload | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [loadingMessage, setLoadingMessage] = useState("Extracting Section 32 answers, review flags, and autofill actions...");
  const [loadingKind, setLoadingKind] = useState<LoadingKind>(null);
  const [uploadError, setUploadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [autofilling, setAutofilling] = useState(false);
  const [autofillJob, setAutofillJob] = useState<AutofillJobPayload | null>(null);
  const [appInfo, setAppInfo] = useState<AppInfoPayload | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusPayload | null>(null);
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const [installingUpdate, setInstallingUpdate] = useState(false);
  const [updateError, setUpdateError] = useState("");
  const [downloadedInstallerPath, setDownloadedInstallerPath] = useState<string | null>(null);
  const [downloadedInstallerVersion, setDownloadedInstallerVersion] = useState<string | null>(null);
  const [isBackgroundDownloadingUpdate, setIsBackgroundDownloadingUpdate] = useState(false);
  const [cloudSyncStatus, setCloudSyncStatus] = useState<CloudSyncStatusPayload | null>(null);
  const [isLoadingRuns, setIsLoadingRuns] = useState(false);
  const [isLoadingAllRuns, setIsLoadingAllRuns] = useState(false);
  const [isReanalysing, setIsReanalysing] = useState(false);
  const [showQuickSetup, setShowQuickSetup] = useState(false);
  const [pendingDropFiles, setPendingDropFiles] = useState<File[]>([]);
  const [isDashboardDragOver, setIsDashboardDragOver] = useState(false);
  
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);
  const [loadingProgress, setLoadingProgress] = useState<number>(0);
  const [loadingStatusHeading, setLoadingStatusHeading] = useState<string>("");
  const [aiEnhancedReady, setAiEnhancedReady] = useState(false);
  const [activatingAi, setActivatingAi] = useState(false);
  const [backgroundRunWatchId, setBackgroundRunWatchId] = useState<string | null>(null);

  const maybeStartBackgroundUpdateDownload = async (status: UpdateStatusPayload | null) => {
    if (!status?.update_available || !status.latest_version) return;
    if (isBackgroundDownloadingUpdate) return;
    if (downloadedInstallerVersion === status.latest_version && downloadedInstallerPath) return;
    setIsBackgroundDownloadingUpdate(true);
    try {
      const payload = await downloadUpdateInstaller({
        includePrerelease: settings.includePrereleaseUpdates,
        updateRepository: settings.updateRepository || undefined,
      });
      setUpdateStatus(payload.release);
      setDownloadedInstallerPath(payload.download.installer_path);
      setDownloadedInstallerVersion(payload.download.latest_version);
    } catch {
      // Keep background behavior silent to avoid noisy startup errors.
    } finally {
      setIsBackgroundDownloadingUpdate(false);
    }
  };

  useEffect(() => {
    if (!user) {
      setAppInfo(null);
      setCloudSyncStatus(null);
      setShowQuickSetup(false);
      return;
    }

    void (async () => {
      let hasOnboarded = localStorage.getItem(firstRunKey(user.user_id)) === "true";
      const dismissedThisSession = sessionStorage.getItem(sessionDismissKey(user.user_id)) === "true";

      let loadedSettings = settings;
      try {
        const nextSettings = await getSettings();
        // Merge localStorage theme as fallback if backend doesn't have it yet
        const lsTheme = localStorage.getItem("convey:theme") as "light" | "dark" | "auto" | null;
        const initialTheme = lsTheme ?? nextSettings.theme ?? "light";
        const merged: LocalSettingsForm = { googleApiKey: "", openRouterApiKey: "", aiMode: "cost_efficient", ...nextSettings, theme: initialTheme };
        setSettings(merged);
        loadedSettings = merged;
        if (merged.theme) localStorage.setItem("convey:theme", merged.theme);
      } catch {
        // Keep defaults if backend settings are not available yet.
      }

      // Auto-mark existing users as onboarded if they already have settings configured.
      if (!hasOnboarded && (loadedSettings?.anthropicApiKey || loadedSettings?.openAiApiKey || loadedSettings?.triconveyPath)) {
        localStorage.setItem(firstRunKey(user.user_id), "true");
        hasOnboarded = true;
      }

      setShowQuickSetup(!hasOnboarded && !dismissedThisSession);

      try {
        setAppInfo(await getAppInfo());
      } catch {
        setAppInfo({ name: "Convey Agent", publisher: "Convey Agent", version: "0.0.127" });
      }

      setIsLoadingRuns(true);
      try {
        const nextRecentRuns = await getRecentRuns(10);
        const normalized = nextRecentRuns.map((item) => ({
          run_id: item.run_id,
          client_name: item.client_name || "",
          volume_folio: item.volume_folio || "",
          property_address: item.property_address || "",
          created_at: item.created_at ?? undefined,
          status: item.status,
          time_taken_seconds: item.time_taken_seconds ?? null,
        }));
        setRecentRuns(normalized);
        saveRecentRuns(user.user_id, normalized);
      } catch {
        setRecentRuns(loadRecentRuns(user.user_id));
      } finally {
        setIsLoadingRuns(false);
      }

      // Auto-check for updates on startup if enabled.
      if (loadedSettings.autoCheckForUpdates) {
        try {
          const next = await checkForUpdates({
            includePrerelease: loadedSettings.includePrereleaseUpdates,
            updateRepository: loadedSettings.updateRepository || undefined,
          });
          setUpdateStatus(next);
          await maybeStartBackgroundUpdateDownload(next);
        } catch {
          // Silently ignore startup update check failures.
        }
      }
    })();
  }, [user]);

  // Run Status Polling (for background analysis)
  useEffect(() => {
    if (!loadingRunId || loadingKind !== "analysis") return;

    let timer: any;
    let isActive = true;

    const poll = async () => {
      try {
        const current = await getRun(loadingRunId);
        if (!isActive) return;

        if (current.status === "completed" || (current.status as string) === "complete") {
          setRun(current);
          setActiveRunId(loadingRunId);
          if (user) upsertRecentRun(user.user_id, current);
          setLoadingKind(null);
          setLoadingRunId(null);
          navigate(`/analysis/${loadingRunId}`, { replace: true });
        } else if (current.status === "failed") {
          setUploadError(current.error_message || "Analysis failed.");
          setLoadingKind(null);
          setLoadingRunId(null);
          navigate("/analysis/upload", { replace: true });
        } else {
          // current.status === "running" or "pending"
          setLoadingProgress(current.progress_pct ?? 0);
          setLoadingStatusHeading(current.progress_status ?? "");
          timer = setTimeout(poll, 1500);
        }
      } catch (err) {
        if (!isActive) return;
        console.error("Polling failed:", err);
        timer = setTimeout(poll, 3000);
      }
    };

    poll();

    return () => {
      isActive = false;
      if (timer) clearTimeout(timer);
    };
  }, [loadingRunId, loadingKind, user, navigate]);

  // Poll for background AI enhancement completion on the active run
  useEffect(() => {
    if (!activeRunId || !run || run.ai_enhanced || aiEnhancedReady) return;
    let isActive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const current = await getRun(activeRunId);
        if (!isActive) return;
        if (current.ai_enhanced) {
          setAiEnhancedReady(true);
          return;
        }
      } catch { /* ignore transient errors */ }
      if (isActive) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    return () => {
      isActive = false;
      if (timer) clearTimeout(timer);
    };
  }, [activeRunId, run, aiEnhancedReady]);

  // Reset AI enhancement state when run changes
  useEffect(() => {
    setAiEnhancedReady(run?.ai_enhanced ?? false);
    setActivatingAi(false);
  }, [run?.manifest?.run_id]);

  const handleActivateAi = async () => {
    if (!activeRunId || activatingAi) return;
    setActivatingAi(true);
    try {
      const updated = await activateAiAnswers(activeRunId);
      setRun(updated);
      setAiEnhancedReady(false);
    } catch (e) {
      console.error("Failed to activate AI answers:", e);
    } finally {
      setActivatingAi(false);
    }
  };

  useEffect(() => {
    if (!user) {
      setRun(null);
      setActiveRunId(null);
      setRecentRuns([]);
      setAutofillJob(null);
      setAutofilling(false);
      setLoadingKind(null);
      setUploadError("");
      return;
    }

    const savedRunId = localStorage.getItem(activeRunKey(user.user_id));
    const savedJobId = localStorage.getItem(activeAutofillJobKey(user.user_id));
    setActiveRunId(savedRunId);
    setRecentRuns(loadRecentRuns(user.user_id));

    if (!savedJobId) {
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const savedJob = await getAutofillJob(savedJobId);
        if (cancelled) return;
        setAutofillJob(savedJob);

        if (["queued", "running", "cancelling", "awaiting_user"].includes(savedJob.status)) {
          setAutofilling(true);
          setLoadingKind(null);
          setLoadingMessage(savedJob.manual_action?.message || "Autofill in progress...");
          if (savedRunId) {
            navigate(`/analysis/${savedRunId}`, { replace: true });
          }
          return;
        }

        if (savedJob.status === "completed" && savedJob.result) {
          setRun(savedJob.result);
          setActiveRunId(savedJob.result.manifest.run_id);
          setRecentRuns(upsertRecentRun(user.user_id, savedJob.result));
        }

        setAutofilling(false);
        setAutofillJob(null);
        setLoadingKind(null);
      } catch {
        localStorage.removeItem(activeAutofillJobKey(user.user_id));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [navigate, user]);

  useEffect(() => {
    if (!user) return;

    if (run?.manifest.run_id) {
      localStorage.setItem(activeRunKey(user.user_id), run.manifest.run_id);
      setActiveRunId(run.manifest.run_id);
      setRecentRuns(upsertRecentRun(user.user_id, run));
      return;
    }

    localStorage.removeItem(activeRunKey(user.user_id));
    setActiveRunId(null);
  }, [run, user]);

  useEffect(() => {
    if (!user) return;

    if (autofillJob?.job_id) {
      localStorage.setItem(activeAutofillJobKey(user.user_id), autofillJob.job_id);
      return;
    }

    localStorage.removeItem(activeAutofillJobKey(user.user_id));
  }, [autofillJob, user]);

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
          setAutofilling(false);
          setAutofillJob(null);
          navigate(`/analysis/${nextJob.result.manifest.run_id}`, { replace: true });
          return;
        }

        if (nextJob.status === "cancelled") {
          if (nextJob.result) {
            setRun(nextJob.result);
          }
          window.setTimeout(() => {
            setUploadError("Autofill was cancelled.");
            setAutofilling(false);
            setAutofillJob(null);
            setLoadingKind(null);
            if (nextJob.result?.manifest.run_id) {
              navigate(`/analysis/${nextJob.result.manifest.run_id}`, { replace: true });
            } else {
              navigate("/dashboard", { replace: true });
            }
          }, 900);
          return;
        }

        if (nextJob.status === "awaiting_user") {
          setLoadingMessage(nextJob.manual_action?.message || "Please open Property Details in Convey, then press Continue.");
          return;
        }

        if (nextJob.status === "failed") {
          setUploadError(nextJob.error || "Autofill failed.");
          setAutofilling(false);
          setAutofillJob(null);
          if (nextJob.result?.manifest.run_id) {
            navigate(`/analysis/${nextJob.result.manifest.run_id}`, { replace: true });
          } else {
            navigate("/dashboard", { replace: true });
          }
        }
      } catch (error) {
        setUploadError(error instanceof Error ? error.message : "Could not refresh autofill status.");
        setAutofilling(false);
        navigate("/dashboard", { replace: true });
      }
    }, 1200);

    return () => window.clearTimeout(timer);
  }, [autofillJob, navigate]);

  useEffect(() => {
    if (isLoading || user) return;

    if (location.pathname && location.pathname !== "/login") {
      try {
        sessionStorage.setItem("convey:returnTo", `${location.pathname}${location.search ?? ""}`);
      } catch {
        // Ignore session storage failures.
      }
    }
  }, [isLoading, location.pathname, location.search, user]);

  useEffect(() => {
    if (!user) return;

    const target = (() => {
      try {
        return sessionStorage.getItem("convey:returnTo");
      } catch {
        return null;
      }
    })();

    if (target) {
      try {
        sessionStorage.removeItem("convey:returnTo");
      } catch {
        // Ignore session storage failures.
      }
    }

    if (location.pathname === "/login" || location.pathname === "/") {
      navigate(target && target !== "/login" ? target : "/dashboard", { replace: true });
    }
  }, [location.pathname, navigate, user]);

  useEffect(() => {
    if (!backgroundRunWatchId || !user) return;
    let cancelled = false;
    let timer: number | undefined;

    const maybeNotify = (title: string, body: string) => {
      if (typeof window === "undefined" || !("Notification" in window)) return;
      if (Notification.permission === "granted") {
        new Notification(title, { body });
        return;
      }
      if (Notification.permission !== "denied") {
        void Notification.requestPermission().then((permission) => {
          if (permission === "granted") {
            new Notification(title, { body });
          }
        });
      }
    };

    const pollBackground = async () => {
      try {
        const current = await getRun(backgroundRunWatchId);
        if (cancelled) return;
        const status = String(current.status || "").toLowerCase();
        if (status === "completed" || status === "complete") {
          maybeNotify("Analysis complete", "Your matter processing has finished.");
          setBackgroundRunWatchId(null);
          const nextRecent = await getRecentRuns(10);
          if (!cancelled) {
            setRecentRuns(nextRecent);
            saveRecentRuns(user.user_id, nextRecent);
          }
          return;
        }
        if (status === "failed") {
          maybeNotify("Analysis failed", "The background analysis run failed.");
          setBackgroundRunWatchId(null);
          return;
        }
      } catch {
        // Keep polling; transient errors are expected occasionally.
      }
      if (!cancelled) {
        timer = window.setTimeout(pollBackground, 4000);
      }
    };

    pollBackground();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [backgroundRunWatchId, user]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <svg
          className="h-8 w-8 animate-spin text-slate-400"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginScreen />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  const handleUploadComplete = async (files: File[]) => {
    setPendingDropFiles([]);
    setUploadError("");
    setAutofillJob(null);
    setLoadingMessage("Extracting Section 32 answers, review flags, and autofill actions...");
    setLoadingKind("analysis");
    navigate("/analysis/loading", { replace: true });

    try {
      const result = await createRun(files, {
        useAiReview: true,
        model: settings.defaultModelName,
        triconveyExe: settings.triconveyPath || null,
      });
      
      // The backend returns a Run-like object with progress info
      setLoadingRunId(result.manifest?.run_id || (result as any).run_id);
      setLoadingProgress(0);
      setLoadingStatusHeading("Initializing...");
      
    } catch (error) {
      const message =
        error instanceof TypeError && error.message === "Failed to fetch"
          ? "Could not reach the local backend. Restart the app/backend and try again."
          : error instanceof Error
            ? error.message
            : "Failed to analyze the uploaded PDFs.";
      setUploadError(message);
      setLoadingKind(null);
      setLoadingRunId(null);
      navigate("/analysis/upload", { replace: true });
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
    setLoadingKind(null);
    setLoadingMessage("Autofill in progress...");
    try {
      const savedRun = Object.keys(updates).length ? await saveAnswers(run.manifest.run_id, updates) : run;
      setRun(savedRun);
      const job = await startAutofillJob(savedRun.manifest.run_id, {
        skipReviewGate: true,
        triconveyExe: settings.triconveyPath || undefined,
      });
      setAutofillJob(job);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Autofill failed.");
      setLoadingKind(null);
      setAutofilling(false);
      if (run?.manifest.run_id) {
        navigate(`/analysis/${run.manifest.run_id}`, { replace: true });
      } else {
        navigate("/dashboard", { replace: true });
      }
    }
  };

  const handleCancelAutofill = async () => {
    if (!autofillJob) return;
    try {
      const job = await cancelAutofillJob(autofillJob.job_id);
      setAutofillJob(job);
      window.setTimeout(() => {
        setUploadError("Autofill was cancelled.");
        setAutofilling(false);
        setAutofillJob(null);
        setLoadingKind(null);
        if (run?.manifest.run_id) {
          navigate(`/analysis/${run.manifest.run_id}`, { replace: true });
        } else {
          navigate("/dashboard", { replace: true });
        }
      }, 900);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not cancel autofill.");
      setLoadingKind(null);
      setAutofilling(false);
      navigate("/dashboard", { replace: true });
    }
  };

  const handleCancelAnalysis = () => {
    if (loadingRunId) {
      setBackgroundRunWatchId(loadingRunId);
    }
    setLoadingKind(null);
    setLoadingRunId(null);
    setLoadingProgress(0);
    setLoadingStatusHeading("");
    navigate("/dashboard", { replace: true });
  };

  const handleContinueAutofill = async () => {
    if (!autofillJob) return;
    try {
      const job = await continueAutofillJob(autofillJob.job_id);
      setAutofillJob(job);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not continue autofill.");
      setLoadingKind(null);
      setAutofilling(false);
      navigate("/dashboard", { replace: true });
    }
  };

  const handleLogout = async () => {
    clearPersistedWorkflowState(user.user_id);
    setRun(null);
    setUploadError("");
    setAutofillJob(null);
    setAutofilling(false);
    setLoadingKind(null);
    await logout();
    navigate("/login", { replace: true });
  };

  const handleAskAssistant = async (
    question: string,
    history: Array<{ role: "user" | "assistant"; content: string }>,
    mode: "quick" | "standard" | "thorough",
    signal?: AbortSignal,
    sessionId?: string,   // Feature 4: vector memory session
  ) => {
    if (!run) {
      throw new Error("No review run is loaded yet.");
    }

    return askRunQuestion(run.manifest.run_id, question, {
      history,
      mode,
      signal,
      sessionId,           // Feature 4
    });
  };

  const handleApplyPatch = async (questionId: string, newValue: string, reason: string) => {
    if (!run) return;
    const nextRun = await applyAnswerPatches(run.manifest.run_id, [
      { question_id: questionId, new_value: newValue, reason },
    ]) as ReviewRunPayload;
    setRun(nextRun);
  };

  const handleSaveSettings = async (nextSettings: LocalSettingsForm) => {
    const saved = await saveSettings(nextSettings);
    const merged: LocalSettingsForm = { googleApiKey: "", openRouterApiKey: "", aiMode: "cost_efficient", ...saved, theme: nextSettings.theme ?? saved.theme ?? "light" };
    setSettings(merged);
    localStorage.setItem(firstRunKey(user.user_id), "true");
    localStorage.setItem("convey:theme", merged.theme);
    sessionStorage.removeItem(sessionDismissKey(user.user_id));
    setShowQuickSetup(false);
  };

  const handleDismissQuickSetup = () => {
    if (!user) return;
    sessionStorage.setItem(sessionDismissKey(user.user_id), "true");
    setShowQuickSetup(false);
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
      if (next.update_available) {
        void maybeStartBackgroundUpdateDownload(next);
      } else {
        setDownloadedInstallerPath(null);
        setDownloadedInstallerVersion(null);
      }
      if (manual) {
        setUpdateError("");
      }
    } catch {
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
      let installerPath = downloadedInstallerPath;
      if (!installerPath || downloadedInstallerVersion !== updateStatus?.latest_version) {
        const payload = await downloadUpdateInstaller({
          includePrerelease: settings.includePrereleaseUpdates,
          updateRepository: settings.updateRepository || undefined,
        });
        setUpdateStatus(payload.release);
        setDownloadedInstallerPath(payload.download.installer_path);
        setDownloadedInstallerVersion(payload.download.latest_version);
        installerPath = payload.download.installer_path;
      }
      const bridge = window as unknown as DesktopBridge;
      const launchInstaller = bridge.pywebview?.api?.launch_installer;
      if (launchInstaller && installerPath) {
        await Promise.resolve(launchInstaller(installerPath));
        bridge.pywebview?.api?.close_app?.();
        return;
      }
      if (updateStatus?.release_url) {
        window.open(updateStatus.release_url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      setUpdateError(error instanceof Error ? error.message : "Could not download the update installer.");
    } finally {
      setInstallingUpdate(false);
    }
  };

  const handleReinstallCurrentVersion = async () => {
    if (installingUpdate) return;
    setInstallingUpdate(true);
    setUpdateError("");
    try {
      const payload = await reinstallCurrentVersion({
        updateRepository: settings.updateRepository || undefined,
      });
      const installerPath = payload.download.installer_path;
      const bridge = window as unknown as DesktopBridge;
      const launchInstaller = bridge.pywebview?.api?.launch_installer;
      if (launchInstaller && installerPath) {
        await Promise.resolve(launchInstaller(installerPath));
        bridge.pywebview?.api?.close_app?.();
        return;
      }
      if (updateStatus?.release_url) {
        window.open(updateStatus.release_url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      setUpdateError(error instanceof Error ? error.message : "Could not download the reinstall package.");
    } finally {
      setInstallingUpdate(false);
    }
  };

  const userInitials = user.name
    ? user.name.split(" ").map((name) => name[0]).join("").slice(0, 2).toUpperCase()
    : (user.email?.[0] ?? "?").toUpperCase();

  const goDashboard = () => navigate("/dashboard");
  const goProfile = () => navigate("/profile");
  const goSettings = () => navigate("/settings");
  const goPolicy = () => navigate("/policy");
  const goAbout = () => navigate("/about");
  const goUpload = () => navigate("/analysis/upload");

  const handleProcessNew = () => {
    setRun(null);
    setUploadError("");
    setPendingDropFiles([]);
    goUpload();
  };

  const handleDashboardDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDashboardDragOver(false);

    const dropped = Array.from(e.dataTransfer.files);
    const plainText = e.dataTransfer.getData("text/plain");
    const uriList = e.dataTransfer.getData("text/uri-list");
    const htmlText = e.dataTransfer.getData("text/html");

    // Collect local paths from .path property on File objects (PyWebView/desktop drops)
    const filePaths: string[] = [];
    for (const f of dropped) {
      const p = (f as File & { path?: string }).path;
      if (p && p.trim()) filePaths.push(p.trim());
    }

    // Extract paths from text data (TriConvey reference drops)
    const textPaths: string[] = [];
    for (const raw of [plainText, uriList, htmlText]) {
      if (!raw) continue;
      for (const match of raw.matchAll(/file:\/\/\/[^\s"'<>]+/gi)) textPaths.push(match[0]);
      for (const match of raw.matchAll(/@?[A-Za-z]:\\[^\r\n"<>|?*]+/g)) textPaths.push(match[0].replace(/^@+/, ""));
    }

    setRun(null);
    setUploadError("");

    // If we have desktop file paths, create a reference file that UploadScreen resolves
    if (filePaths.length > 0) {
      const refContent = JSON.stringify({ LocalPaths: filePaths }, null, 2);
      const stamp = Math.abs(filePaths.join("").split("").reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0));
      const refFile = new File([refContent], `triconvey-drop-${stamp}.json`, { type: "application/json", lastModified: stamp });
      setPendingDropFiles([refFile]);
      navigate("/analysis/upload", { replace: false });
      return;
    }

    if (textPaths.length > 0) {
      const refContent = JSON.stringify({ LocalPaths: textPaths }, null, 2);
      const stamp = Math.abs(textPaths.join("").split("").reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0));
      const refFile = new File([refContent], `triconvey-drop-${stamp}.json`, { type: "application/json", lastModified: stamp });
      setPendingDropFiles([refFile]);
      navigate("/analysis/upload", { replace: false });
      return;
    }

    // Check for TriConvey matter payload in text/plain
    if (plainText?.trim().includes('"MatterId"')) {
      const stamp = Date.now();
      const refFile = new File([plainText.trim()], `triconvey-drop-${stamp}.json`, { type: "application/json", lastModified: stamp });
      setPendingDropFiles([refFile]);
      navigate("/analysis/upload", { replace: false });
      return;
    }

    if (!dropped.length) return;
    setPendingDropFiles(dropped);
    navigate("/analysis/upload", { replace: false });
  };

  const handleViewRun = (runId: string) => {
    setUploadError("");
    navigate(`/analysis/${runId}`);
  };

  const handleDashboardAskAssistant = async (
    question: string,
    history: Array<{ role: "user" | "assistant"; content: string }>,
    mode: "quick" | "standard" | "thorough",
    signal?: AbortSignal,
    sessionId?: string,   // Feature 4
  ) => {
    if (!run) {
      return {
        answer: "Open a matter from the dashboard or start a new upload and I can answer against its documents.",
        citations: [],
      };
    }

    return handleAskAssistant(question, history, mode, signal, sessionId);
  };

  function AnalysisRunRoute() {
    const { runId } = useParams<{ runId: string }>();
    const [loadingRun, setLoadingRun] = useState(false);

    useEffect(() => {
      if (!runId) return;
      if (run?.manifest.run_id === runId) return;

      let cancelled = false;
      setLoadingRun(true);
      void (async () => {
        try {
          const nextRun = await getRun(runId);
          if (cancelled) return;
          setRun(nextRun);
          setUploadError("");
          setActiveRunId(nextRun.manifest.run_id);
          setRecentRuns(upsertRecentRun(user.user_id, nextRun));
        } catch (error) {
          if (cancelled) return;
          setRun(null);
          setUploadError(error instanceof Error ? error.message : "Could not load the selected matter.");
          navigate("/dashboard", { replace: true });
        } finally {
          if (!cancelled) {
            setLoadingRun(false);
          }
        }
      })();

      return () => {
        cancelled = true;
      };
    }, [runId, user.user_id]);

    if (!runId) {
      return <Navigate to="/analysis/upload" replace />;
    }

    if (loadingRun || !run || run.manifest.run_id !== runId) {
      return <LoadingScreen message="Loading matter workspace..." cancellable={false} />;
    }

    return (
      <ReviewScreen
        run={run}
        onBack={goDashboard}
        onProfile={goProfile}
        onSettings={goSettings}
        onAbout={goAbout}
        onPolicy={goPolicy}
        onLogout={handleLogout}
        onSaveReview={handleSaveReview}
        onAutofill={handleAutofill}
        onAskAssistant={handleAskAssistant}
        onApplyPatch={handleApplyPatch}
        onResolveTriconveyReference={resolveTriconveyReference}
        onRunReprocessed={async () => {
          if (!run) return;
          try {
            const refreshed = await getRun(run.manifest.run_id);
            setRun(refreshed);
          } catch {
            // Non-fatal refresh failure
          }
        }}
        onReviewAgain={async () => {
          if (!run) return;
          setLoadingMessage("Re-analysing matter documents...");
          setLoadingKind("analysis");
          navigate("/analysis/loading", { replace: true });
          
          try {
            const result = await createRun([], {
              useAiReview: true,
              model: settings.defaultModelName,
              triconveyExe: settings.triconveyPath || null,
              reanalyseRunId: run.manifest.run_id,
            });
            setLoadingRunId(result.manifest?.run_id || (result as any).run_id);
            setLoadingProgress(0);
            setLoadingStatusHeading("Restarting analysis...");
          } catch (err) {
            setUploadError(err instanceof Error ? err.message : "Re-analysis failed.");
            setLoadingKind(null);
            navigate(`/analysis/${run.manifest.run_id}`, { replace: true });
          }
        }}
        isSaving={saving}
        isAutofilling={autofilling}
        isReanalysing={isReanalysing}
        onCancelAutofill={handleCancelAutofill}
        errorMessage={uploadError}
        onDismissError={() => setUploadError("")}
        updateStatus={updateStatus}
      />
    );
  }

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <div
            className="relative h-full min-h-screen"
            onDragOver={(e) => { e.preventDefault(); setIsDashboardDragOver(true); }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDashboardDragOver(false); }}
            onDragEnd={() => setIsDashboardDragOver(false)}
            onDrop={handleDashboardDrop}
          >
            {isDashboardDragOver && (
              <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-blue-500/10 backdrop-blur-[1px]">
                <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-blue-400 bg-white/90 px-12 py-10 shadow-2xl dark:bg-slate-900/90">
                  <svg className="h-12 w-12 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                  </svg>
                  <p className="text-lg font-semibold text-blue-700 dark:text-blue-300">Drop files to upload</p>
                  <p className="text-sm text-slate-500 dark:text-slate-400">PDF and Word documents supported</p>
                </div>
              </div>
            )}
          <DashboardScreen
            userName={user.name}
            userInitials={userInitials}
            recentRuns={recentRuns}
            isLoadingRuns={isLoadingRuns}
            onLoadAllRuns={async () => {
              setIsLoadingAllRuns(true);
              try {
                const all = await getAllRuns();
                const normalized = all.map((item) => ({
                  run_id: item.run_id,
                  client_name: item.client_name || "",
                  volume_folio: item.volume_folio || "",
                  property_address: item.property_address || "",
                  created_at: item.created_at ?? undefined,
                  status: item.status,
                  time_taken_seconds: item.time_taken_seconds ?? null,
                }));
                setRecentRuns(normalized);
              } finally {
                setIsLoadingAllRuns(false);
              }
            }}
            isLoadingAllRuns={isLoadingAllRuns}
            updateStatus={updateStatus}
            cloudSyncStatus={cloudSyncStatus}
            onProcessNew={handleProcessNew}
            onViewRun={handleViewRun}
            onDeleteRun={async (runId) => {
              await deleteRun(runId);
              setRecentRuns((prev) => {
                const next = prev.filter((r) => r.run_id !== runId);
                if (user) saveRecentRuns(user.user_id, next);
                return next;
              });
              if (activeRunId === runId) {
                setActiveRunId(null);
                setRun(null);
              }
            }}
            onProfile={goProfile}
            onSettings={goSettings}
            onPolicy={goPolicy}
            onAbout={goAbout}
            onLogout={() => void handleLogout()}
            onAskAssistant={handleDashboardAskAssistant}
            onApplyPatch={handleApplyPatch}
            activeRunId={activeRunId ?? undefined}
            onResolveTriconveyReference={resolveTriconveyReference}
            onInstallUpdate={handleDownloadAndInstallUpdate}
            isInstallingUpdate={installingUpdate}
            showQuickSetup={showQuickSetup}
            quickSetupSettings={settings}
            onSaveQuickSetup={handleSaveSettings}
            onDismissQuickSetup={handleDismissQuickSetup}
            onBrowseTriconveyPath={() => void handleBrowseTriconveyPath()}
          />
          </div>
        }
      />
      <Route
        path="/analysis/upload"
        element={
          <UploadScreen
            onBack={goDashboard}
            userInitials={userInitials}
            onProfile={goProfile}
            onSettings={goSettings}
            onPolicy={goPolicy}
            onAbout={goAbout}
            onLogout={() => void handleLogout()}
            onUploadComplete={handleUploadComplete}
            errorMessage={uploadError}
            onResolveTriconveyReference={resolveTriconveyReference}
            initialFiles={pendingDropFiles.length ? pendingDropFiles : undefined}
            key={pendingDropFiles.length ? pendingDropFiles.map(f => f.name).join(",") : "upload"}
          />
        }
      />
      <Route
        path="/analysis/loading"
        element={
          loadingKind ? (
            <LoadingScreen
              message={loadingMessage}
              progress={loadingProgress}
              statusHeading={loadingStatusHeading}
              cancellable={loadingKind === "analysis" || Boolean(autofillJob && ["queued", "running", "cancelling", "awaiting_user"].includes(autofillJob.status))}
              onCancel={loadingKind === "analysis" ? handleCancelAnalysis : handleCancelAutofill}
              cancelLabel={
                loadingKind === "analysis"
                  ? "Stop processing"
                  : (autofillJob?.status === "cancelling" ? "Cancelling..." : "Cancel")
              }
              cancelHint={
                loadingKind === "analysis"
                  ? "Stops this screen only. Processing continues in the background and uploaded files are kept."
                  : "Cancelling autofill keeps your uploaded files and current matter."
              }
              actionLabel={autofillJob?.status === "awaiting_user" ? (autofillJob.manual_action?.cta || "Continue") : undefined}
              onAction={autofillJob?.status === "awaiting_user" ? handleContinueAutofill : undefined}
            />
          ) : (
            <Navigate to="/dashboard" replace />
          )
        }
      />
      <Route path="/analysis/:runId" element={<AnalysisRunRoute />} />
      <Route
        path="/settings"
        element={
          <SettingsScreen
            onBack={goDashboard}
            userInitials={userInitials}
            onProfile={goProfile}
            onSettings={goSettings}
            onPolicy={goPolicy}
            onAbout={goAbout}
            onLogout={() => void handleLogout()}
            settings={settings}
            onSaveSettings={handleSaveSettings}
            appInfo={appInfo}
            updateStatus={updateStatus}
            isCheckingUpdates={checkingUpdates}
            isInstallingUpdate={installingUpdate}
            onCheckForUpdates={() => void handleCheckForUpdates(true)}
            onInstallUpdate={() => void handleDownloadAndInstallUpdate()}
            onReinstallCurrentVersion={() => void handleReinstallCurrentVersion()}
            onBrowseTriconveyPath={() => void handleBrowseTriconveyPath()}
            onOpenLocalDataDir={() => void handleOpenLocalDataDir()}
            onThemePreview={applyThemePreference}
          />
        }
      />
      <Route
        path="/profile"
        element={
          <ProfileScreen
            onBack={goDashboard}
            onSettings={goSettings}
            onPolicy={goPolicy}
            onAbout={goAbout}
            onLogout={() => void handleLogout()}
          />
        }
      />
      <Route
        path="/about"
        element={
          <AboutScreen
            onBack={goDashboard}
            userInitials={userInitials}
            onProfile={goProfile}
            onSettings={goSettings}
            onPolicy={goPolicy}
            onLogout={() => void handleLogout()}
            appInfo={appInfo}
            updateStatus={updateStatus}
            updateMessage={updateError}
            isCheckingUpdates={checkingUpdates}
            isInstallingUpdate={installingUpdate}
            onCheckForUpdates={() => void handleCheckForUpdates(true)}
            onInstallUpdate={() => void handleDownloadAndInstallUpdate()}
            onReinstallCurrentVersion={() => void handleReinstallCurrentVersion()}
          />
        }
      />
      <Route
        path="/policy"
        element={
          <ClientPolicyScreen
            onBack={goDashboard}
            userInitials={userInitials}
            onProfile={goProfile}
            onSettings={goSettings}
            onAbout={goAbout}
            onLogout={() => void handleLogout()}
            settings={settings}
            onSaveSettings={handleSaveSettings}
          />
        }
      />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
