import React from "react";
import { motion } from "motion/react";
import { Bell, Bot, ChevronLeft, FolderOpen, Globe, KeyRound, Moon, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

interface SettingsScreenProps {
  onBack: () => void;
  settings: {
    language: string;
    openAiApiKey: string;
    defaultModelName: string;
    triconveyPath: string;
  };
  onSaveSettings: (settings: {
    language: string;
    openAiApiKey: string;
    defaultModelName: string;
    triconveyPath: string;
  }) => void;
}

export function SettingsScreen({ onBack, settings, onSaveSettings }: SettingsScreenProps) {
  const [form, setForm] = React.useState(settings);

  React.useEffect(() => {
    setForm(settings);
  }, [settings]);

  return (
    <div className="min-h-screen bg-slate-50 font-sans">
      <header className="h-16 border-b bg-white flex items-center px-6 sticky top-0 z-50">
        <div 
          onClick={onBack}
          className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center cursor-pointer hover:bg-slate-200 transition-colors mr-4"
        >
          <ChevronLeft className="w-4 h-4 text-foreground stroke-[2.5]" />
        </div>
        <h1 className="text-lg font-bold">Settings</h1>
      </header>

      <main className="max-w-4xl mx-auto p-8 space-y-8">
        <motion.div 
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-6"
        >
          <section className="rounded-3xl border border-slate-200 bg-white px-6 py-5 shadow-sm">
            <div className="flex items-start justify-between gap-6">
              <div>
                <h2 className="text-xl font-bold text-slate-900">Workspace Preferences</h2>
                <p className="mt-1 text-sm text-slate-500">Configure how Convey Agent reviews documents, talks to OpenAI, and launches Triconvey on this machine.</p>
              </div>
              <div className="rounded-2xl bg-primary/8 px-4 py-3 text-right">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Desktop Setup</p>
                <p className="mt-1 text-sm text-slate-600">Local-only settings for this device</p>
              </div>
            </div>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold text-slate-500 uppercase tracking-wider">General</h2>
            <Card className="border-slate-200 shadow-sm overflow-hidden">
              <CardContent className="p-0">
                <div className="flex items-center justify-between p-4 hover:bg-slate-50 cursor-pointer transition-colors border-b border-slate-100">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center text-blue-600">
                      <Globe className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold">Language</p>
                      <select
                        value={form.language}
                        onChange={(e) => setForm((prev) => ({ ...prev, language: e.target.value }))}
                        className="mt-1 h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:ring-1 focus:ring-primary"
                      >
                        <option value="English">English</option>
                      </select>
                    </div>
                  </div>
                </div>
                <div className="flex items-center justify-between p-4 hover:bg-slate-50 cursor-pointer transition-colors">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600">
                      <Moon className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold">Appearance</p>
                      <p className="text-xs text-slate-400">Light Mode</p>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold text-slate-500 uppercase tracking-wider">Integration</h2>
            <Card className="border-slate-200 shadow-sm overflow-hidden">
              <CardContent className="grid gap-5 p-5 md:grid-cols-2">
                <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                  <div className="flex items-center gap-3 text-sm font-semibold">
                    <div className="w-8 h-8 rounded-lg bg-violet-50 flex items-center justify-center text-violet-600">
                      <KeyRound className="w-4 h-4" />
                    </div>
                    OpenAI API Key
                  </div>
                  <p className="text-xs text-slate-500">Used for AI review, document chat, and fallback reasoning.</p>
                  <Input
                    type="password"
                    value={form.openAiApiKey}
                    onChange={(e) => setForm((prev) => ({ ...prev, openAiApiKey: e.target.value }))}
                    placeholder="sk-..."
                    className="h-10 bg-white border-slate-200"
                  />
                </div>
                <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                  <div className="flex items-center gap-3 text-sm font-semibold">
                    <div className="w-8 h-8 rounded-lg bg-sky-50 flex items-center justify-center text-sky-600">
                      <Bot className="w-4 h-4" />
                    </div>
                    Default model name
                  </div>
                  <p className="text-xs text-slate-500">Default model used for AI review and document assistant answers.</p>
                  <Input
                    value={form.defaultModelName}
                    onChange={(e) => setForm((prev) => ({ ...prev, defaultModelName: e.target.value }))}
                    placeholder="gpt-4.1-mini"
                    className="h-10 bg-white border-slate-200"
                  />
                </div>
                <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4 md:col-span-2">
                  <div className="flex items-center gap-3 text-sm font-semibold">
                    <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center text-emerald-600">
                      <FolderOpen className="w-4 h-4" />
                    </div>
                    Triconvey path location
                  </div>
                  <p className="text-xs text-slate-500">Optional explicit executable path used to launch Triconvey before review or autofill.</p>
                  <Input
                    value={form.triconveyPath}
                    onChange={(e) => setForm((prev) => ({ ...prev, triconveyPath: e.target.value }))}
                    placeholder="C:\\Program Files\\TriConvey\\TriConvey.exe"
                    className="h-10 bg-white border-slate-200"
                  />
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="space-y-4">
            <h2 className="text-sm font-bold text-slate-500 uppercase tracking-wider">Security</h2>
            <Card className="border-slate-200 shadow-sm overflow-hidden">
              <CardContent className="p-0">
                <div className="flex items-center justify-between p-4 hover:bg-slate-50 cursor-pointer transition-colors border-b border-slate-100">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center text-emerald-600">
                      <Shield className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold">Two-Factor Authentication</p>
                      <p className="text-xs text-slate-400">Disabled</p>
                    </div>
                  </div>
                </div>
                <div className="flex items-center justify-between p-4 hover:bg-slate-50 cursor-pointer transition-colors">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center text-amber-600">
                      <Bell className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold">Notifications</p>
                      <p className="text-xs text-slate-400">Run, review, and autofill notifications enabled</p>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </section>

          <div className="pt-4">
            <Button className="w-full bg-primary text-white font-bold h-12 rounded-xl" onClick={() => onSaveSettings(form)}>
              Save Changes
            </Button>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
