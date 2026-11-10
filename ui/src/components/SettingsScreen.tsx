import React from "react";
import { motion } from "motion/react";
import {
  Bell,
  Download,
  FolderOpen,
  Globe,
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
import { AppInfoPayload, UpdateStatusPayload } from "../lib/api";
import { Header } from "./Header";

interface SettingsForm {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  googleApiKey: string;
  openRouterApiKey?: string;
  aiProvider: "openai" | "anthropic" | "google" | "hybrid" | "openrouter";
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
  settings: SettingsForm;
  onSaveSettings: (settings: SettingsForm) => Promise<void> | void;
  onThemePreview?: (theme: "light" | "dark" | "auto") => void;
  appInfo: AppInfoPayload | null;
  updateStatus: UpdateStatusPayload | null;
  isCheckingUpdates: boolean;
  isInstallingUpdate: boolean;
  onCheckForUpdates: () => void;
  onInstallUpdate: () => void;
  onReinstallCurrentVersion: () => void;
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
  settings,
  onSaveSettings,
  appInfo,
  updateStatus,
  isCheckingUpdates,
  isInstallingUpdate,
  onCheckForUpdates,
  onInstallUpdate,
  onReinstallCurrentVersion,
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
    <div className="relative min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/30 to-violet-50/20 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 font-sans overflow-hidden">
      <style>{`
        .settings-orb-1 { position:fixed; top:-15%; left:-10%; width:55%; height:55%; background:radial-gradient(circle, rgba(139,92,246,0.10) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .settings-orb-2 { position:fixed; bottom:-10%; right:-5%; width:45%; height:45%; background:radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .settings-glass { background:rgba(255,255,255,0.70) !important; backdrop-filter:blur(20px) saturate(160%); -webkit-backdrop-filter:blur(20px) saturate(160%); border-color:rgba(255,255,255,0.55) !important; }
        .dark .settings-glass { background:rgba(15,23,42,0.55) !important; border-color:rgba(255,255,255,0.08) !important; }
        .settings-stat { background:rgba(255,255,255,0.50); backdrop-filter:blur(8px); border:1px solid rgba(255,255,255,0.4); border-radius:0.75rem; }
        .dark .settings-stat { background:rgba(30,41,59,0.45); border-color:rgba(255,255,255,0.07); }
      `}</style>
      <div className="settings-orb-1" />
      <div className="settings-orb-2" />
      <Header
        onBack={onBack}
        userInitials={userInitials}
        onProfile={onProfile}
        onSettings={onSettings}
        onPolicy={onPolicy}
      />

      <main className="relative z-10 mx-auto grid max-w-7xl grid-cols-[280px_minmax(0,1fr)] gap-6 p-6">
        <aside className="sticky top-24 h-fit space-y-4">
          <Card className="settings-glass overflow-hidden border shadow-sm">
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
                <div className="settings-stat px-3 py-2">
                  <p className="font-semibold text-foreground">Language</p>
                  <p className="text-muted-foreground">{form.language}</p>
                </div>
                <div className="settings-stat px-3 py-2">
                  <p className="font-semibold text-foreground">App version</p>
                  <p className="text-muted-foreground">{appInfo?.version ?? "0.0.125"}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="settings-glass overflow-hidden border shadow-sm">
            <CardContent className="space-y-3 p-5 text-[0.78rem] text-muted-foreground">
              <div className="flex items-center gap-2 font-semibold text-foreground">
                <Shield className="h-4 w-4 text-emerald-500" />
                Storage notes
              </div>
              <p>Desktop autofill uses the saved Convey executable path when available.</p>
              <p>These settings are machine-local and are not intended for the shared run database.</p>
            </CardContent>
          </Card>
        </aside>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">General</h2>
            <Card className="settings-glass border shadow-sm">
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
            <Card className="settings-glass border shadow-sm">
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
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Desktop Behaviour</h2>
            <Card className="settings-glass border shadow-sm">
              <CardContent className="grid gap-4 p-6 md:grid-cols-2">
                <div className="rounded-2xl border border-border bg-muted/50 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <Shield className="h-4 w-4 text-emerald-500" />
                    Security
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">Your settings stay local to this desktop.</p>
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
                      <Button
                        type="button"
                        variant="outline"
                        className="h-10 rounded-xl border-border/60 text-muted-foreground hover:text-foreground"
                        disabled={isInstallingUpdate}
                        onClick={onReinstallCurrentVersion}
                        title="Re-download and run the installer for the currently installed version"
                      >
                        <FolderOpen className="mr-2 h-4 w-4" />
                        Reinstall current
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
