import React from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

export type AppToastTone = "success" | "error" | "info";

export interface AppToastItem {
  id: string;
  title: string;
  message?: string;
  tone: AppToastTone;
}

const toneStyles: Record<AppToastTone, { shell: string; icon: React.ReactNode }> = {
  success: {
    shell: "border-emerald-200 bg-emerald-50/95 text-emerald-950",
    icon: <CheckCircle2 className="h-5 w-5 text-emerald-600" />,
  },
  error: {
    shell: "border-rose-200 bg-rose-50/95 text-rose-950",
    icon: <AlertCircle className="h-5 w-5 text-rose-600" />,
  },
  info: {
    shell: "border-sky-200 bg-card/95 text-foreground",
    icon: <Info className="h-5 w-5 text-sky-600" />,
  },
};

interface AppToastCenterProps {
  toasts: AppToastItem[];
  onDismiss: (id: string) => void;
}

export function AppToastCenter({ toasts, onDismiss }: AppToastCenterProps) {
  return (
    <div className="pointer-events-none fixed right-5 top-5 z-[140] flex w-full max-w-sm flex-col gap-3">
      <AnimatePresence>
        {toasts.map((toast) => {
          const tone = toneStyles[toast.tone];
          return (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, y: -12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.18 }}
              className={`pointer-events-auto rounded-2xl border px-4 py-3 shadow-xl backdrop-blur ${tone.shell}`}
            >
              <div className="flex items-start gap-3">
                <div className="mt-0.5 shrink-0">{tone.icon}</div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">{toast.title}</p>
                  {toast.message ? <p className="mt-1 text-sm text-muted-foreground">{toast.message}</p> : null}
                </div>
                <button
                  type="button"
                  onClick={() => onDismiss(toast.id)}
                  className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-card/60 hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
