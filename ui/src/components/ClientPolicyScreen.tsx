import React from "react";
import { motion } from "motion/react";
import { ChevronLeft, Shield, CheckCircle2, AlertCircle, FileLock, Scale } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface ClientPolicyScreenProps {
  onBack: () => void;
}

export function ClientPolicyScreen({ onBack }: ClientPolicyScreenProps) {
  return (
    <div className="min-h-screen bg-slate-50 font-sans">
      <header className="h-16 border-b bg-white flex items-center px-6 sticky top-0 z-50">
        <div 
          onClick={onBack}
          className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center cursor-pointer hover:bg-slate-200 transition-colors mr-4"
        >
          <ChevronLeft className="w-4 h-4 text-foreground stroke-[2.5]" />
        </div>
        <h1 className="text-lg font-bold">Custom Policy</h1>
      </header>

      <main className="max-w-4xl mx-auto p-8 space-y-8">
        <motion.div 
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-8"
        >
          <div className="bg-primary/5 border border-primary/10 rounded-2xl p-6 flex items-start gap-4">
            <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
              <Shield className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-slate-900">Active TriConvey Policy</h2>
              <p className="text-slate-500 text-sm mt-1">
                This policy governs how TriConvey Agent analyzes, flags, and prepares Section 32 answers using your firm's legal standards.
              </p>
            </div>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            <Card className="border-slate-200 shadow-sm">
              <CardContent className="p-6 space-y-4">
                <div className="flex items-center gap-3 text-emerald-600">
                  <CheckCircle2 className="w-5 h-5" />
                  <h3 className="font-bold">Standard Checks</h3>
                </div>
                <ul className="space-y-3">
                  {["Title Search Verification", "Easement Disclosure", "Planning Scheme Accuracy", "Owners Corp Status"].map((item) => (
                    <li key={item} className="text-sm text-slate-600 flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-slate-300" />
                      {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            <Card className="border-slate-200 shadow-sm">
              <CardContent className="p-6 space-y-4">
                <div className="flex items-center gap-3 text-amber-600">
                  <AlertCircle className="w-5 h-5" />
                  <h3 className="font-bold">High Priority Flags</h3>
                </div>
                <ul className="space-y-3">
                  {["Unregistered Interests", "Contaminated Land", "Heritage Restrictions", "Building Violations"].map((item) => (
                    <li key={item} className="text-sm text-slate-600 flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-slate-300" />
                      {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>

          <section className="space-y-4">
            <h2 className="text-sm font-bold text-slate-500 uppercase tracking-wider">Policy Framework</h2>
            <div className="grid gap-4">
              <div className="flex items-center gap-4 p-4 bg-white border border-slate-200 rounded-xl">
                <FileLock className="w-5 h-5 text-slate-400" />
                <div>
                  <p className="text-sm font-semibold">Data Privacy Protocol</p>
                  <p className="text-xs text-slate-400">AES-256 encryption for all uploaded legal documents</p>
                </div>
              </div>
              <div className="flex items-center gap-4 p-4 bg-white border border-slate-200 rounded-xl">
                <Scale className="w-5 h-5 text-slate-400" />
                <div>
                  <p className="text-sm font-semibold">Regulatory Alignment</p>
                  <p className="text-xs text-slate-400">Updated for 2024 Victorian Property Law amendments</p>
                </div>
              </div>
            </div>
          </section>
        </motion.div>
      </main>
    </div>
  );
}
