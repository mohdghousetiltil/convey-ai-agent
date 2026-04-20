import React from "react";
import { motion } from "motion/react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface LoadingScreenProps {
  message?: string;
  cancellable?: boolean;
  onCancel?: () => void;
  cancelLabel?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function LoadingScreen({ message, cancellable = false, onCancel, cancelLabel = "Cancel", actionLabel, onAction }: LoadingScreenProps) {
  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center p-6">
      <div className="relative">
        <motion.div 
          animate={{ rotate: 360 }}
          transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
          className="w-24 h-24 rounded-full border-4 border-slate-100 border-t-primary"
        />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
            <Loader2 className="w-6 h-6 text-primary animate-pulse" />
          </div>
        </div>
      </div>
      <motion.div 
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="mt-8 text-center space-y-2"
      >
        <h2 className="text-xl font-bold text-slate-900">Analyzing Matter Documents</h2>
        <p className="text-slate-400 text-sm">{message ?? "Extracting Section 32 answers, review flags, and autofill actions..."}</p>
        {cancellable && onCancel ? (
          <div className="pt-4 flex items-center justify-center gap-3">
            {actionLabel && onAction ? (
              <Button onClick={onAction}>
                {actionLabel}
              </Button>
            ) : null}
            <Button variant="outline" onClick={onCancel}>
              {cancelLabel}
            </Button>
          </div>
        ) : actionLabel && onAction ? (
          <div className="pt-4">
            <Button onClick={onAction}>
              {actionLabel}
            </Button>
          </div>
        ) : null}
      </motion.div>
    </div>
  );
}
