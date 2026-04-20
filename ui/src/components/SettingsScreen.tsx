import React from "react";
import { motion } from "motion/react";
import {
  Bell,
  Bot,
  ChevronLeft,
  FolderOpen,
  Globe,
  KeyRound,
  Shield,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const OPENAI_MODELS = [
  { id: "gpt-4.1", label: "GPT-4.1", badge: "Latest" },
  { id: "gpt-4o", label: "GPT-4o", badge: "Smart" },
  { id: "gpt-4.1-mini", label: "GPT-4.1 Mini", badge: "Fast" },
  { id: "gpt-4o-mini", label: "GPT-4o Mini", badge: "Fast" },
  { id: "gpt-4.1-nano", label: "GPT-4.1 Nano", badge: "Fastest" },
  { id: "o3-mini", label: "o3-mini", badge: "Reasoning" },
  { id: "o1", label: "o1", badge: "Reasoning" },
] as const;

const ANTHROPIC_MODELS = [
  { id: "claude-opus-4-7", label: "Claude Opus 4", badge: "Most capable" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4", badge: "Balanced" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4", badge: "Fastest" },
] as const;

const BADGE_COLORS: Record<string, string> = {
  Latest: "bg-violet-100 text-violet-700",
  Smart: "bg-blue-100 text-blue-700",
  Fast: "bg-sky-100 text-sky-700",
  Fastest: "bg-emerald-100 text-emerald-700",
  Reasoning: "bg-amber-100 text-amber-700",
  "Most capable": "bg-indigo-100 text-indigo-700",
  Balanced: "bg-teal-100 text-teal-700",
};

interface SettingsForm {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  aiProvider: "openai" | "anthropic";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
}

interface SettingsScreenProps {
  onBack: () => void;
  settings: SettingsForm;
  onSaveSettings: (settings: SettingsForm) => Promise<void> | void;
}

export function SettingsScreen({ onBack, settings, onSaveSettings }: SettingsScreenProps) {
  const [form, setForm] = React.useState<SettingsForm>({
    anthropicApiKey: "",
    ...settings,
  });
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState("");

  React.useEffect(() => {
    setForm({ anthropicApiKey: "", ...settings });
  }, [settings]);

  const models = form.aiProvider === "anthropic" ? ANTHROPIC_MODELS : OPENAI_MODELS;

  function handleProviderChange(provider: "openai" | "anthropic") {
    const defaultModel = provider === "anthropic" ? ANTHROPIC_MODELS[1].id : OPENAI_MODELS[2].id;
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
    <div className="min-h-screen bg-slate-50 font-sans">
      <header className="sticky top-0 z-50 flex h-16 items-center border-b bg-white px-6">
        <div onClick={onBack} className="mr-4 flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg bg-muted transition-colors hover:bg-slate-200">
          <ChevronLeft className="h-4 w-4 stroke-[2.5] text-foreground" />
        </div>
        <div>
          <h1 className="text-lg font-bold">Settings</h1>
          <p className="text-xs text-slate-400">Local machine configuration for Convey Agent</p>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl grid-cols-[280px_minmax(0,1fr)] gap-6 p-6">
        <aside className="sticky top-24 h-fit space-y-4">
          <Card className="overflow-hidden border-slate-200 shadow-sm">
            <CardContent className="space-y-4 p-5">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-sm font-bold text-slate-900">Desktop Workspace</p>
                  <p className="text-xs text-slate-500">Stored locally on this PC</p>
                </div>
              </div>

              <div className="grid gap-2 text-[0.78rem]">
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="font-semibold text-slate-700">Provider</p>
                  <p className="text-slate-500">{form.aiProvider === "openai" ? "OpenAI" : "Anthropic"}</p>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="font-semibold text-slate-700">Model</p>
                  <p className="break-all text-slate-500">{form.defaultModelName}</p>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="font-semibold text-slate-700">Language</p>
                  <p className="text-slate-500">{form.language}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="overflow-hidden border-slate-200 shadow-sm">
            <CardContent className="space-y-3 p-5 text-[0.78rem] text-slate-500">
              <div className="flex items-center gap-2 font-semibold text-slate-700">
                <Shield className="h-4 w-4 text-emerald-600" />
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
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500">General</h2>
            <Card className="border-slate-200 shadow-sm">
              <CardContent className="grid gap-6 p-6 md:grid-cols-2">
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Globe className="h-4 w-4 text-blue-600" />
                    Language
                  </label>
                  <select
                    value={form.language}
                    onChange={(e) => setForm((prev) => ({ ...prev, language: e.target.value }))}
                    className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                  >
                    <option value="English">English</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <FolderOpen className="h-4 w-4 text-emerald-600" />
                    Triconvey executable path
                  </label>
                  <Input
                    value={form.triconveyPath}
                    onChange={(e) => setForm((prev) => ({ ...prev, triconveyPath: e.target.value }))}
                    placeholder="C:\Program Files\TriConvey\TriConvey.exe"
                    className="h-11 border-slate-200"
                  />
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500">AI Provider</h2>
            <Card className="border-slate-200 shadow-sm">
              <CardContent className="space-y-6 p-6">
                <div className="grid gap-4 md:grid-cols-2">
                  {(["openai", "anthropic"] as const).map((provider) => {
                    const active = form.aiProvider === provider;
                    return (
                      <button
                        key={provider}
                        onClick={() => handleProviderChange(provider)}
                        className={[
                          "rounded-2xl border-2 p-5 text-left transition-all",
                          active ? "border-primary bg-primary/5 shadow-sm" : "border-slate-200 bg-slate-50 hover:border-slate-300",
                        ].join(" ")}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1">
                            <p className="text-sm font-bold capitalize text-slate-900">{provider === "openai" ? "OpenAI" : "Anthropic"}</p>
                            <p className="text-xs text-slate-500">
                              {provider === "openai" ? "GPT-4.1, GPT-4o, o-series reasoning models" : "Claude Opus, Sonnet, and Haiku models"}
                            </p>
                          </div>
                          <div className={["rounded-full px-2 py-0.5 text-[0.62rem] font-bold uppercase tracking-wider", active ? "bg-primary text-white" : "bg-white text-slate-500"].join(" ")}>
                            {active ? "Active" : "Available"}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                      <KeyRound className="h-4 w-4 text-violet-600" />
                      OpenAI API key
                    </div>
                    <Input
                      type="password"
                      value={form.openAiApiKey}
                      onChange={(e) => setForm((prev) => ({ ...prev, openAiApiKey: e.target.value }))}
                      placeholder="sk-..."
                      className="h-11 border-slate-200 bg-white"
                    />
                  </div>
                  <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                      <KeyRound className="h-4 w-4 text-orange-600" />
                      Anthropic API key
                    </div>
                    <Input
                      type="password"
                      value={form.anthropicApiKey}
                      onChange={(e) => setForm((prev) => ({ ...prev, anthropicApiKey: e.target.value }))}
                      placeholder="sk-ant-..."
                      className="h-11 border-slate-200 bg-white"
                    />
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Bot className="h-4 w-4 text-slate-500" />
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
                            active ? "border-primary bg-primary/5" : "border-slate-200 bg-white hover:border-slate-300",
                          ].join(" ")}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <p className={["text-sm font-semibold", active ? "text-primary" : "text-slate-800"].join(" ")}>{model.label}</p>
                              <p className="mt-0.5 text-[10px] text-slate-400">{model.id}</p>
                            </div>
                            <span className={["rounded-full px-2 py-0.5 text-[10px] font-bold", BADGE_COLORS[model.badge] ?? "bg-slate-100 text-slate-600"].join(" ")}>
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
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500">Desktop Behaviour</h2>
            <Card className="border-slate-200 shadow-sm">
              <CardContent className="grid gap-4 p-6 md:grid-cols-2">
                <div className="rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Shield className="h-4 w-4 text-emerald-600" />
                    Security
                  </div>
                  <p className="mt-2 text-sm text-slate-500">API keys stay local to this desktop setup.</p>
                </div>
                <div className="rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <Bell className="h-4 w-4 text-amber-600" />
                    Review workflow
                  </div>
                  <p className="mt-2 text-sm text-slate-500">Saved settings are used by upload, chat, and Convey autofill.</p>
                </div>
              </CardContent>
            </Card>
          </section>

          <div className="flex justify-end">
            <div className="space-y-2 text-right">
              {saveError ? <p className="text-sm font-medium text-rose-600">{saveError}</p> : null}
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
