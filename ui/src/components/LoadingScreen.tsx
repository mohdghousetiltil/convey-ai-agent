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
  progress?: number;
  statusHeading?: string;
}

export function LoadingScreen({ 
  message, 
  cancellable = false, 
  onCancel, 
  cancelLabel = "Cancel", 
  actionLabel, 
  onAction,
  progress,
  statusHeading 
}: LoadingScreenProps) {
  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-6 overflow-hidden relative">
      <div className="relative">
        <motion.div 
          animate={{ rotate: 360 }}
          transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
          className="w-24 h-24 rounded-full border-4 border-muted border-t-primary"
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
        className="mt-8 text-center space-y-4 max-w-md"
      >
        <div className="space-y-1">
          <h2 className="text-xl font-bold text-foreground">Analyzing Matter Documents</h2>
          <p className="text-muted-foreground text-sm">{message ?? "Extracting Section 32 answers, review flags, and autofill actions..."}</p>
        </div>

        {statusHeading && (
          <p className="text-primary/60 font-bold text-[0.7rem] uppercase tracking-[0.25em] animate-pulse">
            {statusHeading}
          </p>
        )}

        {(cancellable && onCancel) || (actionLabel && onAction) ? (
          <div className="pt-2 flex items-center justify-center gap-3">
            {actionLabel && onAction ? (
              <Button onClick={onAction}>
                {actionLabel}
              </Button>
            ) : null}
            {cancellable && onCancel ? (
              <Button variant="outline" onClick={onCancel} className="h-9 px-6 text-xs font-semibold">
                {cancelLabel}
              </Button>
            ) : null}
          </div>
        ) : null}
      </motion.div>

      {/* Percentage in the bottom right (Red Box Area) */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.4 }}
        className="fixed bottom-10 right-10 flex flex-col items-end"
      >
        <div className="text-5xl font-black text-foreground/30 tabular-nums">
          {(progress ?? 0).toFixed(1)}<span className="text-2xl font-medium ml-1">%</span>
        </div>
        <div className="h-1 w-32 bg-muted mt-2 rounded-full overflow-hidden">
          <motion.div 
            className="h-full bg-foreground/20"
            animate={{ width: `${progress ?? 0}%` }}
            transition={{ type: "spring", bounce: 0, duration: 0.5 }}
          />
        </div>
      </motion.div>
    </div>
  );
}
