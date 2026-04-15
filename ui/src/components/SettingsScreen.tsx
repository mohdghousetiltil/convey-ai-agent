import React from "react";
import { motion } from "motion/react";
import { ChevronLeft, Settings as SettingsIcon, Bell, Shield, User, Globe, Moon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

interface SettingsScreenProps {
  onBack: () => void;
}

export function SettingsScreen({ onBack }: SettingsScreenProps) {
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

      <main className="max-w-3xl mx-auto p-8 space-y-8">
        <motion.div 
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-6"
        >
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
                      <p className="text-xs text-slate-400">English (US)</p>
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
            <Button className="w-full bg-primary text-white font-bold h-12 rounded-xl">
              Save Changes
            </Button>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
