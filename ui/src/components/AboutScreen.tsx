import React from "react";
import { motion } from "motion/react";
import { Download, ExternalLink, Info, RefreshCw, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { AppInfoPayload, UpdateStatusPayload } from "@/lib/api";
import { Header } from "./Header";

interface AboutScreenProps {
  onBack: () => void;
  userInitials?: string;
  onProfile?: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onLogout?: () => void;
  appInfo: AppInfoPayload | null;
  updateStatus: UpdateStatusPayload | null;
  updateMessage: string;
  isCheckingUpdates: boolean;
  isInstallingUpdate: boolean;
  onCheckForUpdates: () => void;
  onInstallUpdate: () => void;
}

function ReleaseNotes({ notes }: { notes: string }) {
  const lines = notes.split(/\r?\n/).map((line) => line.trimEnd());

  return (
    <div className="space-y-3 text-sm text-muted-foreground">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={index} className="h-2" />;
        }
        if (trimmed.startsWith("### ")) {
          return <h4 key={index} className="text-sm font-bold uppercase tracking-wider text-foreground">{trimmed.slice(4)}</h4>;
        }
        if (trimmed.startsWith("## ")) {
          return <h3 key={index} className="text-base font-bold text-foreground">{trimmed.slice(3)}</h3>;
        }
        if (trimmed.startsWith("# ")) {
          return <h2 key={index} className="text-lg font-bold text-foreground">{trimmed.slice(2)}</h2>;
        }
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          return (
            <div key={index} className="flex items-start gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-primary" />
              <span>{trimmed.slice(2)}</span>
            </div>
          );
        }
        return <p key={index} className="leading-6">{trimmed}</p>;
      })}
    </div>
  );
}

export function AboutScreen({
  onBack,
  userInitials,
  onProfile,
  onSettings,
  onPolicy,
  onLogout,
  appInfo,
  updateStatus,
  updateMessage,
  isCheckingUpdates,
  isInstallingUpdate,
  onCheckForUpdates,
  onInstallUpdate,
}: AboutScreenProps) {
  const version = appInfo?.version || "0.0.125";
  const publisher = appInfo?.publisher || "Convey Agent";

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

      <main className="mx-auto max-w-6xl p-6">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-6">
            <Card className="overflow-hidden border-border shadow-sm">
              <CardContent className="space-y-4 p-6">
                <div className="flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                    <Info className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="text-sm font-bold text-foreground">Convey Agent</p>
                    <p className="text-xs text-muted-foreground">Desktop installation</p>
                  </div>
                </div>
                <div className="grid gap-3 text-[0.8rem]">
                  <div className="rounded-xl bg-muted px-3 py-2">
                    <p className="font-semibold text-foreground">App version</p>
                    <p className="text-muted-foreground">{version}</p>
                  </div>
                  <div className="rounded-xl bg-muted px-3 py-2">
                    <p className="font-semibold text-foreground">Publisher</p>
                    <p className="text-muted-foreground">{publisher}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="overflow-hidden border-border shadow-sm">
              <CardContent className="space-y-4 p-6">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Rocket className="h-4 w-4 text-emerald-600" />
                  Update actions
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="h-11 w-full rounded-xl border-border"
                  disabled={isCheckingUpdates}
                  onClick={onCheckForUpdates}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${isCheckingUpdates ? "animate-spin" : ""}`} />
                  {isCheckingUpdates ? "Checking..." : "Check for updates"}
                </Button>
                <Button
                  type="button"
                  className="h-11 w-full rounded-xl bg-primary"
                  disabled={!updateStatus?.update_available || isInstallingUpdate}
                  onClick={onInstallUpdate}
                >
                  <Download className="mr-2 h-4 w-4" />
                  {isInstallingUpdate ? "Preparing installer..." : "Download and install"}
                </Button>
              </CardContent>
            </Card>
          </aside>

          <section className="space-y-6">
            <Card className="border-border shadow-sm">
              <CardContent className="space-y-5 p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Current release status</p>
                    <h2 className="mt-1 text-2xl font-bold text-foreground">
                      {updateStatus?.update_available
                        ? `Version ${updateStatus.latest_version} is available`
                        : "You are up to date"}
                    </h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {updateStatus?.checked_at
                        ? `Last checked ${new Date(updateStatus.checked_at).toLocaleString()}.`
                        : "Run an update check to load the latest release details."}
                    </p>
                  </div>
                  {updateStatus?.release_url ? (
                    <a
                      href={updateStatus.release_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground hover:bg-accent"
                    >
                      Open release page
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  ) : null}
                </div>

                <div className="grid gap-4 md:grid-cols-3">
                  <div className="rounded-2xl border border-border bg-muted p-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Installed</p>
                    <p className="mt-2 text-lg font-bold text-foreground">{version}</p>
                  </div>
                  <div className="rounded-2xl border border-border bg-muted p-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Latest</p>
                    <p className="mt-2 text-lg font-bold text-foreground">{updateStatus?.latest_version ?? version}</p>
                  </div>
                  <div className="rounded-2xl border border-border bg-muted p-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Published</p>
                    <p className="mt-2 text-sm font-semibold text-foreground">
                      {updateStatus?.published_at ? new Date(updateStatus.published_at).toLocaleString() : "—"}
                    </p>
                  </div>
                </div>

              </CardContent>
            </Card>

            <Card className="border-border shadow-sm">
              <CardContent className="space-y-5 p-6">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Release notes</p>
                  <h3 className="mt-1 text-xl font-bold text-foreground">
                    {updateStatus?.release_name || updateStatus?.latest_version || "No release loaded"}
                  </h3>
                </div>
                {updateStatus?.notes ? (
                  <ReleaseNotes notes={updateStatus.notes} />
                ) : (
                  <p className="text-sm text-muted-foreground">No release notes are available yet. Run an update check to load the latest published release.</p>
                )}
              </CardContent>
            </Card>
          </section>
        </motion.div>
      </main>
    </div>
  );
}
