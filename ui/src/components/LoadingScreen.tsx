import React, { useEffect, useMemo, useState } from "react";
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
  cancelHint?: string;
}

type Stage = "uploading" | "reading" | "understanding" | "extracting" | "scoring" | "finalizing";

const STAGE_PROGRESS: Record<Stage, number> = {
  uploading: 5,
  reading: 20,
  understanding: 40,
  extracting: 65,
  scoring: 85,
  finalizing: 100,
};

const STAGE_MESSAGES: Record<Exclude<Stage, "uploading">, string[]> = {
  reading: ["Reading document structure...", "Scanning pages and sections..."],
  understanding: ["Understanding document layout...", "Identifying key sections..."],
  extracting: ["Extracting fees, levies, and liabilities...", "Pulling structured data from text..."],
  scoring: ["Comparing possible answers...", "Ranking extracted values..."],
  finalizing: ["Finalizing answers...", "Preparing results..."],
};

function inferStage(progress: number, statusHeading?: string): Stage {
  const status = (statusHeading || "").toLowerCase();
  if (status.includes("upload")) return "uploading";
  if (status.includes("read") || status.includes("scan")) return "reading";
  if (status.includes("understand") || status.includes("classif")) return "understanding";
  if (status.includes("extract")) return "extracting";
  if (status.includes("score") || status.includes("rank")) return "scoring";
  if (status.includes("final") || status.includes("prepare")) return "finalizing";
  if (progress >= 95) return "finalizing";
  if (progress >= 80) return "scoring";
  if (progress >= 55) return "extracting";
  if (progress >= 30) return "understanding";
  if (progress >= 10) return "reading";
  return "uploading";
}

export function LoadingScreen({ 
  message, 
  cancellable = false, 
  onCancel, 
  cancelLabel = "Cancel", 
  actionLabel, 
  onAction,
  progress,
  statusHeading,
  cancelHint,
}: LoadingScreenProps) {
  const progressFromBackend = Math.max(0, Math.min(progress ?? 0, 100));
  const [displayedProgress, setDisplayedProgress] = useState(progressFromBackend);
  const [messageIndex, setMessageIndex] = useState(0);
  const [startedAt] = useState(() => Date.now());

  useEffect(() => {
    setDisplayedProgress((prev) => {
      if (progressFromBackend > prev) return progressFromBackend;
      return Math.min(prev + 0.2, 95);
    });
  }, [progressFromBackend]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setDisplayedProgress((p) => Math.min(p + 0.1, 92));
    }, 500);
    return () => window.clearInterval(interval);
  }, []);

  const stage = inferStage(displayedProgress, statusHeading);
  const stageFloor = STAGE_PROGRESS[stage];
  const effectiveProgress = Math.max(displayedProgress, stageFloor);

  useEffect(() => {
    const rotate = window.setInterval(() => setMessageIndex((i) => i + 1), 2400);
    return () => window.clearInterval(rotate);
  }, []);

  const dynamicMessage = useMemo(() => {
    if (stage === "uploading") return "Uploading files and preparing analysis...";
    const pool = STAGE_MESSAGES[stage];
    return pool[messageIndex % pool.length];
  }, [stage, messageIndex]);

  const elapsed = Date.now() - startedAt;
  const slowHint =
    elapsed > 30000
      ? "You can continue working — we will notify you when ready."
      : elapsed > 15000
        ? "Still working — this document is complex."
        : elapsed > 7000
          ? "Large document detected."
          : "";

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
          <p className="text-muted-foreground text-sm">{dynamicMessage || (message ?? "Extracting Section 32 answers, review flags, and autofill actions...")}</p>
          {slowHint ? <p className="text-xs text-muted-foreground">{slowHint}</p> : null}
        </div>

        {(statusHeading || stage) && (
          <p className="text-primary/60 font-bold text-[0.7rem] uppercase tracking-[0.25em] animate-pulse">
            {statusHeading || stage.replace("_", " ")}
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
        {cancellable && onCancel && cancelHint ? <p className="text-xs text-muted-foreground">{cancelHint}</p> : null}
      </motion.div>

      {/* Percentage in the bottom right (Red Box Area) */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.4 }}
        className="fixed bottom-10 right-10 flex flex-col items-end"
      >
        <div className="text-5xl font-black text-foreground/30 tabular-nums">
          {Math.floor(effectiveProgress)}<span className="text-2xl font-medium ml-1">%</span>
        </div>
        <div className="h-1 w-32 bg-muted mt-2 rounded-full overflow-hidden">
          <motion.div 
            className="h-full bg-foreground/20"
            animate={{ width: `${effectiveProgress}%` }}
            transition={{ type: "spring", bounce: 0, duration: 0.5 }}
          />
        </div>
      </motion.div>
    </div>
  );
}
