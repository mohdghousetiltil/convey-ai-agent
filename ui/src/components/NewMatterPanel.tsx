import React, { useRef } from "react";
import { AlertTriangle, Check, FileText, Loader2, UploadCloud, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MatterIntake } from "../lib/useMatterIntake";

const STEPS = [
  { label: "Reading documents", until: 20 },
  { label: "Answering questions", until: 50 },
  { label: "Checking answers", until: 75 },
  { label: "Preparing your review", until: 101 },
];

interface NewMatterPanelProps {
  intake: MatterIntake;
  isAnalysing: boolean;
  progress: number;
  onStartReview: (files: File[]) => void;
  onRunInBackground: () => void;
  errorMessage?: string;
  onDismissError?: () => void;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function Progress({ documentCount, progress, onRunInBackground }: { documentCount: number; progress: number; onRunInBackground: () => void }) {
  const percent = Math.max(4, Math.min(progress, 100));
  const activeStep = Math.max(0, STEPS.findIndex((step) => progress < step.until));

  return (
    <div className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-bold text-foreground">
            {documentCount > 0 ? `Reviewing ${plural(documentCount, "document")}` : "Reviewing documents"}
          </h3>
          <p className="mt-0.5 text-sm text-muted-foreground">You can keep working. The matter opens here when it's ready.</p>
        </div>
        <Button
          type="button"
          variant="outline"
          className="h-10 rounded-xl font-semibold"
          onClick={onRunInBackground}
          title="Start another matter now. We'll notify you when this one is ready."
        >
          Run in background
        </Button>
      </div>

      <div
        role="progressbar"
        aria-label="Review progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
        className="mt-5 h-2 overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full origin-left rounded-full bg-primary transition-transform duration-500 ease-out motion-reduce:transition-none"
          style={{ transform: `scaleX(${percent / 100})` }}
        />
      </div>

      <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-live="polite">
        {STEPS.map((step, index) => {
          const done = index < activeStep;
          const active = index === activeStep;
          return (
            <li
              key={step.label}
              aria-current={active ? "step" : undefined}
              className={`flex items-center gap-2 text-sm ${active ? "font-semibold text-foreground" : "text-muted-foreground"}`}
            >
              {done ? (
                <Check className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="Done" />
              ) : active ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-label="In progress" />
              ) : (
                <span className="ml-1 mr-1 h-2 w-2 shrink-0 rounded-full bg-muted-foreground/30" aria-hidden="true" />
              )}
              {step.label}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function NewMatterPanel({ intake, isAnalysing, progress, onStartReview, onRunInBackground, errorMessage, onDismissError }: NewMatterPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { items, duplicatesSkipped, unsupportedSkipped } = intake;
  const isResolving = items.some((item) => item.resolving);

  const notices = [
    duplicatesSkipped > 0 ? `${plural(duplicatesSkipped, "duplicate")} skipped.` : "",
    unsupportedSkipped > 0 ? `${plural(unsupportedSkipped, "file")} not added. Only PDF and Word documents can be reviewed.` : "",
  ].filter(Boolean).join(" ");

  return (
    <section aria-label="New matter" className="glass-card overflow-hidden rounded-3xl">
      {isAnalysing ? (
        <Progress documentCount={items.length} progress={progress} onRunInBackground={onRunInBackground} />
      ) : (
        <div className="p-6">
          <input
            ref={inputRef}
            type="file"
            multiple
            hidden
            accept=".pdf,.docx,.doc,.json,.tmp"
            onChange={(event) => {
              void intake.addFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />

          <div className="flex flex-wrap items-center gap-4 rounded-2xl border-2 border-dashed border-border/70 bg-background/30 px-6 py-6">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <UploadCloud className="h-6 w-6" aria-hidden="true" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-lg font-bold text-foreground">{items.length ? "Add more documents" : "Start a new matter"}</h3>
              <p className="mt-0.5 text-sm text-muted-foreground">Drop the property's documents anywhere on this page. PDF, Word, or a matter from TriConvey.</p>
            </div>
            <Button type="button" variant="outline" className="h-11 rounded-xl px-5 font-semibold" onClick={() => inputRef.current?.click()}>
              Choose files
            </Button>
          </div>

          {items.length > 0 ? (
            <div className="mt-5">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-semibold text-foreground">{plural(items.length, "document")} ready</p>
                <button type="button" onClick={intake.clear} className="rounded text-sm font-semibold text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  Clear all
                </button>
              </div>
              <ul className="custom-scrollbar grid max-h-56 gap-2 overflow-y-auto pr-1 md:grid-cols-2">
                {items.map((item, index) => {
                  const name = item.displayName || item.file.name;
                  const isDocument = /\.(pdf|docx?)$/i.test(item.file.name);
                  return (
                    <li key={`${item.groupKey ?? item.file.name}-${index}`} className="flex items-center gap-3 rounded-xl border border-border/50 bg-background/40 py-2 pl-3 pr-1">
                      {item.resolving ? (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
                      ) : (
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-foreground" title={name}>{name}</p>
                        <p className="text-xs text-muted-foreground">
                          {isDocument ? `${(item.file.size / 1024 / 1024).toFixed(2)} MB` : item.subtitle || "TriConvey documents"}
                        </p>
                      </div>
                      <button
                        type="button"
                        aria-label={`Remove ${name}`}
                        onClick={() => intake.removeItem(index)}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <X className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
              <div className="mt-4 flex justify-end">
                <Button
                  type="button"
                  disabled={isResolving}
                  onClick={() => {
                    intake.dismissNotices();
                    onStartReview(intake.uploadFiles());
                  }}
                  className="h-12 rounded-xl bg-primary px-8 text-base font-bold text-white shadow-lg shadow-primary/20 hover:bg-primary/90"
                >
                  {isResolving ? "Finding documents..." : "Start review"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {notices ? (
        <div role="status" className="flex items-center justify-between gap-3 border-t border-amber-200/70 bg-amber-50 px-6 py-3 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          <span>{notices}</span>
          <button type="button" aria-label="Dismiss notice" onClick={intake.dismissNotices} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-amber-100 dark:hover:bg-amber-900/40">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {errorMessage ? (
        <div role="alert" className="flex items-center gap-3 border-t border-destructive/20 bg-destructive/10 px-6 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">{errorMessage}</span>
          {onDismissError ? (
            <button type="button" aria-label="Dismiss error" onClick={onDismissError} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-destructive/10">
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
