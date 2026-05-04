import React from "react";
import { motion } from "motion/react";
import {
  Bell,
  Bot,
  Download,
  FolderOpen,
  Globe,
  KeyRound,
  Monitor,
  Moon,
  RefreshCw,
  Shield,
  Sparkles,
  Sun,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AppInfoPayload, UpdateStatusPayload } from "@/lib/api";
import { Header } from "./Header";

const OPENAI_MODELS = [
  { id: "gpt-4.1-mini", label: "gpt 4.1 mini", badge: "Fast" },
  { id: "gpt-5.4", label: "gpt 5.4", badge: "Next-gen" },
  { id: "gpt-4.1", label: "gpt 4.1", badge: "Latest" },
  { id: "gpt-4o", label: "gpt 4o", badge: "Smart" },
] as const;

const ANTHROPIC_MODELS = [
  { id: "claude-opus-4.1", label: "claude opus 4.1", badge: "Most capable" },
  { id: "claude-sonnet-4.1", label: "claude sonnet 4.1", badge: "Balanced" },
  { id: "claude-haiku-4.5", label: "claude haiku 4.5", badge: "Fastest" },
] as const;

// Feature 1: Google Gemini models
const GOOGLE_MODELS = [
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", badge: "Fast" },
  { id: "gemini-3.1-flash", label: "Gemini 3.1 Flash", badge: "Fast" },
  { id: "gemini-3-deep-think", label: "Gemini 3 Deep Think", badge: "Reasoning" },
  { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro", badge: "Smart" },
] as const;

const MULTIMODEL_MODELS = [
  { id: "gpt4o-claude-sonnet", label: "gpt-4o + claude-sonnet-4.1", badge: "Balanced" },
  { id: "gpt4o-gemini-pro", label: "gpt-4o + gemini-3.1-pro", badge: "Broad" },
  { id: "all-pro-models", label: "gpt-4o + claude-opus + gemini-pro", badge: "Maximum" },
] as const;

const BADGE_COLORS: Record<string, string> = {
  Latest: "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-400",
  Smart: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
  Fast: "bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-400",
  Fastest: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400",
  Reasoning: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400",
  "Most capable": "bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400",
  Balanced: "bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400",
  "Next-gen": "bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-400",
  Broad: "bg-cyan-100 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-400",
  Maximum: "bg-foreground text-background",
};

interface SettingsForm {
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
  theme: "light" | "dark" | "auto";
}

interface SettingsScreenProps {
  onBack: () => void;
  userInitials?: string;
  onProfile?: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onAbout?: () => void;
  onLogout?: () => void;
  settings: SettingsForm;
  onSaveSettings: (settings: SettingsForm) => Promise<void> | void;
  onThemePreview?: (theme: "light" | "dark" | "auto") => void;
  appInfo: AppInfoPayload | null;
  updateStatus: UpdateStatusPayload | null;
  isCheckingUpdates: boolean;
  isInstallingUpdate: boolean;
  onCheckForUpdates: () => void;
  onInstallUpdate: () => void;
  onBrowseTriconveyPath: () => void;
  onOpenLocalDataDir: () => void;
}

export function SettingsScreen({
  onBack,
  userInitials,
  onProfile,
  onSettings,
  onPolicy,
  onAbout,
  onLogout,
  settings,
  onSaveSettings,
  appInfo,
  updateStatus,
  isCheckingUpdates,
  isInstallingUpdate,
  onCheckForUpdates,
  onInstallUpdate,
  onBrowseTriconveyPath,
  onOpenLocalDataDir,
  onThemePreview,
}: SettingsScreenProps) {
  const [form, setForm] = React.useState<SettingsForm>({
    anthropicApiKey: "",
    googleApiKey: "",
    theme: "light",
    ...settings,
  });
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState("");

  React.useEffect(() => {
    setForm({ ...settings, anthropicApiKey: settings.anthropicApiKey || "", googleApiKey: settings.googleApiKey || "" });
  }, [settings]);

  // Feature 1: pick model list based on provider
  const models =
    form.aiProvider === "anthropic"
      ? ANTHROPIC_MODELS
      : form.aiProvider === "google"
        ? GOOGLE_MODELS
        : form.aiProvider === "hybrid"
          ? MULTIMODEL_MODELS
          : OPENAI_MODELS;

  function handleProviderChange(provider: "openai" | "anthropic" | "google" | "hybrid") {
    const defaultModel =
      provider === "anthropic"
        ? ANTHROPIC_MODELS[1].id
        : provider === "google"
          ? GOOGLE_MODELS[0].id
          : provider === "hybrid"
            ? MULTIMODEL_MODELS[0].id
            : OPENAI_MODELS[2].id;
    setForm((prev) => ({ ...prev, aiProvider: provider, defaultModelName: defaultModel }));
  }

  async function handleSave() {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError("");
    try {
      await onSaveSettings(form);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Could not save settings.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-background font-sans">
      <Header
        onBack={onBack}
        userInitials={userInitials}
        onProfile={onProfile}
        onSettings={onSettings}
        onPolicy={onPolicy}
        onLogout={onLogout}
      />

      <main className="mx-auto grid max-w-7xl grid-cols-[280px_minmax(0,1fr)] gap-6 p-6">
        <aside className="sticky top-24 h-fit space-y-4">
          <Card className="overflow-hidden border-border shadow-sm">
            <CardContent className="space-y-4 p-5">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-sm font-bold text-foreground">Desktop Workspace</p>
                  <p className="text-xs text-muted-foreground">Stored locally on this PC</p>
                </div>
              </div>

              <div className="grid gap-2 text-[0.78rem]">
                <div className="rounded-xl bg-muted px-3 py-2">
                  <p className="font-semibold text-foreground">Provider</p>
                  <p className="text-muted-foreground">
                    {form.aiProvider === "openai"
                      ? "OpenAI"
                      : form.aiProvider === "anthropic"
                        ? "Anthropic"
                        : form.aiProvider === "google"
                          ? "Google Gemini"
                          : "MultiModel"}
                  </p>
                </div>
                <div className="rounded-xl bg-muted px-3 py-2">
                  <p className="font-semibold text-foreground">Model</p>
                  <p className="break-all text-muted-foreground">{form.defaultModelName}</p>
                </div>
                <div className="rounded-xl bg-muted px-3 py-2">
                  <p className="font-semibold text-foreground">Language</p>
                  <p className="text-muted-foreground">{form.language}</p>
                </div>
                <div className="rounded-xl bg-muted px-3 py-2">
                  <p className="font-semibold text-foreground">App version</p>
                  <p className="text-muted-foreground">{appInfo?.version ?? "0.0.125"}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="overflow-hidden border-border shadow-sm">
            <CardContent className="space-y-3 p-5 text-[0.78rem] text-muted-foreground">
              <div className="flex items-center gap-2 font-semibold text-foreground">
                <Shield className="h-4 w-4 text-emerald-500" />
                Storage notes
              </div>
              <p>API keys are stored locally on this machine.</p>
              <p>Desktop autofill uses the saved Convey executable path when available.</p>
              <p>These settings are machine-local and are not intended for the shared run database.</p>
            </CardContent>
          </Card>
        </aside>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">General</h2>
            <Card className="border-border shadow-sm">
              <CardContent className="grid gap-6 p-6 md:grid-cols-2">
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Globe className="h-4 w-4 text-blue-500" />
                    Language
                  </label>
                  <select
                    value={form.language}
                    onChange={(e) => setForm((prev) => ({ ...prev, language: e.target.value }))}
                    className="h-11 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                  >
                    <option value="English">English</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <FolderOpen className="h-4 w-4 text-emerald-500" />
                    Triconvey executable path
                  </label>
                  <div className="flex gap-2">
                    <Input
                      value={form.triconveyPath}
                      onChange={(e) => setForm((prev) => ({ ...prev, triconveyPath: e.target.value }))}
                      placeholder="C:\Program Files\TriConvey\TriConvey.exe"
                      className="h-11 border-border"
                    />
                    <Button type="button" variant="outline" className="h-11 rounded-xl" onClick={onBrowseTriconveyPath}>
                      Browse
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">Clients can set this directly here after installing the app. No manual project-file edits are needed.</p>
                </div>
              </CardContent>
            </Card>
          </section>

          {/* Theme toggle */}
          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Appearance</h2>
            <Card className="border-border shadow-sm">
              <CardContent className="p-6">
                <div className="space-y-3">
                  <label className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Sun className="h-4 w-4 text-amber-500" />
                    Theme
                  </label>
                  <div className="grid grid-cols-3 gap-3">
                    {(["light", "dark", "auto"] as const).map((t) => {
                      const Icon = t === "light" ? Sun : t === "dark" ? Moon : Monitor;
                      const label = t === "light" ? "Light" : t === "dark" ? "Dark" : "Auto";
                      const desc = t === "light" ? "Always light" : t === "dark" ? "Always dark" : "Follow system";
                      const active = form.theme === t;
                      return (
                        <button
                          key={t}
                          type="button"
                          onClick={() => {
                            setForm((prev) => ({ ...prev, theme: t }));
                            onThemePreview?.(t);
                          }}
                          className={[
                            "flex flex-col items-center gap-2 rounded-2xl border-2 p-4 transition-all",
                            active ? "border-primary bg-primary/8 shadow-sm" : "border-border bg-muted hover:border-primary/40",
                          ].join(" ")}
                        >
                          <Icon className={`h-5 w-5 ${active ? "text-primary" : "text-muted-foreground"}`} />
                          <span className={`text-sm font-semibold ${active ? "text-primary" : "text-foreground"}`}>{label}</span>
                          <span className="text-[10px] text-muted-foreground">{desc}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">AI Provider</h2>
            <Card className="border-border shadow-sm">
              <CardContent className="space-y-6 p-6">
                <div className="grid gap-4 md:grid-cols-2">
                  {(["openai", "anthropic", "google", "hybrid"] as const).map((provider) => {
                    const active = form.aiProvider === provider;
                    const labels: Record<string, string> = {
                      openai: "OpenAI",
                      anthropic: "Anthropic",
                      google: "Google Gemini",
                      hybrid: "MultiModel",
                    };
                    const descriptions: Record<string, string> = {
                      openai: "Parallel OpenAI reviewers",
                      anthropic: "Parallel Claude reviewers",
                      google: "Google Gemini 3.1 Pro / Flash",
                      hybrid: "Cross-check synthesis with multiple providers",
                    };
                    const apiKeyUrls: Record<string, string> = {
                      openai: "https://platform.openai.com/settings/organization/api-keys",
                      anthropic: "https://platform.claude.com/settings/keys",
                      google: "https://aistudio.google.com/app/api-keys",
                    };
                    return (
                      <button
                        key={provider}
                        onClick={() => handleProviderChange(provider)}
                        className={[
                          "group relative rounded-2xl border-2 p-5 text-left transition-all",
                          active ? "border-primary bg-primary/8 shadow-sm" : "border-border bg-muted hover:border-primary/40",
                        ].join(" ")}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1">
                            <p className="text-sm font-bold capitalize text-foreground">{labels[provider]}</p>
                            <p className="text-xs text-muted-foreground">{descriptions[provider]}</p>
                            {apiKeyUrls[provider] && (
                              <a
                                href={apiKeyUrls[provider]}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                className="mt-2 inline-block text-[10px] font-bold text-primary hover:underline"
                              >
                                Get API Key →
                              </a>
                            )}
                          </div>
                          <div className={["rounded-full px-2 py-0.5 text-[0.62rem] font-bold uppercase tracking-wider", active ? "bg-primary text-white" : "bg-card text-muted-foreground border border-border"].join(" ")}>
                            {active ? "Active" : "Available"}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div className="grid gap-4 lg:grid-cols-3">
                  <div className="space-y-2 rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <KeyRound className="h-4 w-4 text-violet-500" />
                      OpenAI API key
                    </div>
                    <Input
                      type="password"
                      value={form.openAiApiKey}
                      onChange={(e) => setForm((prev) => ({ ...prev, openAiApiKey: e.target.value }))}
                      placeholder="sk-..."
                      className="h-11 border-border bg-card"
                    />
                  </div>
                  <div className="space-y-2 rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <KeyRound className="h-4 w-4 text-orange-500" />
                      Anthropic API key
                    </div>
                    <Input
                      type="password"
                      value={form.anthropicApiKey}
                      onChange={(e) => setForm((prev) => ({ ...prev, anthropicApiKey: e.target.value }))}
                      placeholder="sk-ant-..."
                      className="h-11 border-border bg-card"
                    />
                  </div>
                  <div className="space-y-2 rounded-2xl border border-border bg-muted/50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <KeyRound className="h-4 w-4 text-green-500" />
                      Google API key
                    </div>
                    <Input
                      type="password"
                      value={form.googleApiKey}
                      onChange={(e) => setForm((prev) => ({ ...prev, googleApiKey: e.target.value }))}
                      placeholder="AIza..."
                      className="h-11 border-border bg-card"
                    />
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Bot className="h-4 w-4 text-muted-foreground" />
                    Default model
                  </div>
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {models.map((model) => {
                      const active = form.defaultModelName === model.id;
                      return (
                        <button
                          key={model.id}
                          onClick={() => setForm((prev) => ({ ...prev, defaultModelName: model.id }))}
                          className={[
                            "rounded-2xl border-2 px-4 py-3 text-left transition-all",
                            active ? "border-primary bg-primary/8" : "border-border bg-card hover:border-primary/40",
                          ].join(" ")}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <p className={["text-sm font-semibold", active ? "text-primary" : "text-foreground"].join(" ")}>{model.label}</p>
                              <p className="mt-0.5 text-[10px] text-muted-foreground">{model.id}</p>
                            </div>
                            <span className={["rounded-full px-2 py-0.5 text-[10px] font-bold", BADGE_COLORS[model.badge] ?? "bg-muted text-muted-foreground"].join(" ")}>
                              {model.badge}
                            </span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Desktop Behaviour</h2>
            <Card className="border-border shadow-sm">
              <CardContent className="grid gap-4 p-6 md:grid-cols-2">
                <div className="rounded-2xl border border-border bg-muted/50 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Shield className="h-4 w-4 text-emerald-500" />
                    Security
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">API keys stay local to this desktop setup unless you choose to move them to a managed cloud backend later.</p>
                </div>
                <div className="rounded-2xl border border-border bg-muted/50 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Bell className="h-4 w-4 text-amber-500" />
                    Review workflow
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">Saved settings are used by upload, chat, and Convey autofill.</p>
                </div>
                <label className="flex items-center gap-3 md:col-span-2">
                  <input
                    type="checkbox"
                    checked={form.autoCheckForUpdates}
                    onChange={(e) => setForm((prev) => ({ ...prev, autoCheckForUpdates: e.target.checked }))}
                    className="h-4 w-4 rounded border-border"
                  />
                  <span className="text-sm text-foreground">Automatically check for updates on startup</span>
                </label>
                <label className="flex items-center gap-3 md:col-span-2">
                  <input
                    type="checkbox"
                    checked={form.includePrereleaseUpdates}
                    onChange={(e) => setForm((prev) => ({ ...prev, includePrereleaseUpdates: e.target.checked }))}
                    className="h-4 w-4 rounded border-border"
                  />
                  <span className="text-sm text-foreground">Include prerelease builds when checking GitHub Releases</span>
                </label>
                <div className="rounded-2xl border border-border bg-card p-4 md:col-span-2">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-foreground">Updates</div>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {updateStatus?.update_available
                          ? `Version ${updateStatus.latest_version} is available.`
                          : updateStatus?.checked_at
                            ? `Last checked at ${new Date(updateStatus.checked_at).toLocaleString()}.`
                            : "No update check has run yet."}
                      </p>
                    </div>
                    <div className="flex gap-3">
                      <Button
                        type="button"
                        variant="outline"
                        className="h-10 rounded-xl"
                        disabled={isCheckingUpdates}
                        onClick={onCheckForUpdates}
                      >
                        <RefreshCw className={`mr-2 h-4 w-4 ${isCheckingUpdates ? "animate-spin" : ""}`} />
                        {isCheckingUpdates ? "Checking..." : "Check for updates"}
                      </Button>
                      <Button
                        type="button"
                        className="h-10 rounded-xl bg-primary text-white"
                        disabled={!updateStatus?.update_available || isInstallingUpdate}
                        onClick={onInstallUpdate}
                      >
                        <Download className="mr-2 h-4 w-4" />
                        {isInstallingUpdate ? "Preparing..." : "Install update"}
                      </Button>
                    </div>
                  </div>
                  {updateStatus?.release_url ? (
                    <a
                      href={updateStatus.release_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-3 inline-block text-sm font-semibold text-primary hover:underline"
                    >
                      Open release page
                    </a>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          </section>

          <div className="flex justify-end">
            <div className="space-y-2 text-right">
              {saveError ? <p className="text-sm font-medium text-destructive">{saveError}</p> : null}
              <Button className="h-12 rounded-xl bg-primary px-8 font-bold text-white" onClick={() => void handleSave()} disabled={isSaving}>
                {isSaving ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
