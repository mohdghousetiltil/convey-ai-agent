import React, { useState, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Upload, FileText, X, ArrowRight } from "lucide-react";
import { Button } from "./ui/button";
import { Header } from "./Header";

interface UploadPageProps {
  onBack?: () => void;
  userInitials?: string;
  onProfile?: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onAbout?: () => void;
  onLogout?: () => void;
  onUploadComplete: (files: File[]) => void | Promise<void>;
  onResolveTriconveyReference?: (payloadText: string) => Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }>;
  errorMessage?: string;
}

interface UploadListItem {
  file: File;
  displayName?: string;
  subtitle?: string;
  resolving?: boolean;
}

function stableDropStamp(content: string): number {
  let hash = 0;
  for (let i = 0; i < content.length; i += 1) {
    hash = ((hash << 5) - hash + content.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

function makeReferenceFile(content: string): File {
  const stamp = stableDropStamp(content);
  return new File([content], `triconvey-drop-${stamp}.json`, {
    type: "application/json",
    lastModified: stamp,
  });
}

function extractLocalPaths(...rawValues: string[]): string[] {
  const paths = new Set<string>();
  const pushMatches = (text: string) => {
    for (const match of text.matchAll(/file:\/\/\/[^\s"'<>]+/gi)) {
      paths.add(match[0]);
    }
    for (const match of text.matchAll(/@?[A-Za-z]:\\[^\r\n"<>|?*]+/g)) {
      paths.add(match[0].replace(/^@+/, ""));
    }
  };
  for (const raw of rawValues) {
    const text = raw.trim();
    if (!text) continue;
    pushMatches(text);
    for (const token of text.split(/\s+/)) {
      const cleaned = token.trim().replace(/^@+/, "");
      if (cleaned.startsWith("file:///") || /^[A-Za-z]:\\/.test(cleaned)) {
        paths.add(cleaned);
      }
    }
  }
  return Array.from(paths);
}

function isTriconveyReferenceName(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    lower.endsWith(".smokeball.tmp") ||
    lower.endsWith(".smokeball.json") ||
    /^triconvey-drop-\d+\.json$/.test(lower) ||
    lower.endsWith("triconvey-drop.json")
  );
}

function extractDroppedFilePaths(files: File[]): string[] {
  const paths = new Set<string>();
  for (const file of files) {
    const candidate = (file as File & { path?: string }).path;
    if (typeof candidate === "string" && candidate.trim()) {
      paths.add(candidate.trim());
    }
  }
  return Array.from(paths);
}

function parseDroppedReferenceText(
  rawPlain: string,
  rawUriList = "",
  rawHtml = "",
): { localPaths?: string[]; matterPayload?: string } | null {
  const matterPayload = rawPlain.trim().includes('"MatterId"') ? rawPlain.trim() : undefined;
  const localPaths = extractLocalPaths(rawPlain, rawUriList, rawHtml);
  if (!matterPayload && localPaths.length === 0) return null;
  return { matterPayload, localPaths: localPaths.length ? localPaths : undefined };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function UploadScreen({
  onBack,
  userInitials,
  onProfile,
  onSettings,
  onPolicy,
  onAbout,
  onLogout,
  onUploadComplete,
  onResolveTriconveyReference,
  errorMessage,
}: UploadPageProps) {
  const [files, setFiles] = useState<UploadListItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isSupportedUpload = (file: File) => {
    const name = file.name.toLowerCase();
    return (
      file.type === "application/pdf" ||
      name.endsWith(".pdf") ||
      name.endsWith(".docx") ||
      name.endsWith(".doc") ||
      file.type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
      file.type === "application/msword" ||
      isTriconveyReferenceName(name)
    );
  };

  const appendFiles = async (nextFiles: File[]) => {
    const nextItems = nextFiles.map((file): UploadListItem => {
      if (!isTriconveyReferenceName(file.name) || !onResolveTriconveyReference) {
        return { file };
      }
      return {
        file,
        displayName: "TriConvey drop reference",
        subtitle: "PDFs loading...",
        resolving: true,
      };
    });
    setFiles((prev) => {
      const seen = new Set(prev.map((item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
      return [...prev, ...nextItems.filter((item) => !seen.has(`${item.file.name}:${item.file.size}:${item.file.lastModified}`))];
    });

    if (!onResolveTriconveyReference) {
      return;
    }

    await Promise.all(
      nextFiles.map(async (file) => {
        if (!isTriconveyReferenceName(file.name)) {
          return;
        }

        const key = `${file.name}:${file.size}:${file.lastModified}`;
        const payloadText = await file.text();
        let nextState: UploadListItem = {
          file,
          displayName: "TriConvey drop reference",
          subtitle: "No local documents resolved yet - try downloading",
          resolving: false,
        };

        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            const resolved = await onResolveTriconveyReference(payloadText);
            const resolvedNames = resolved.resolved.map((item) => item.name).filter(Boolean);
            if (resolvedNames.length) {
              nextState = {
                file,
                displayName: resolvedNames.join(", "),
                subtitle: `${resolvedNames.length} TriConvey document${resolvedNames.length === 1 ? "" : "s"}`,
                resolving: false,
              };
              break;
            }
          } catch {
            // Keep retrying briefly while Smokeball/TriConvey finishes materialising PDFs.
          }
          if (attempt < 2) {
            await delay(attempt === 0 ? 1000 : 1200);
          }
        }

        setFiles((prev) =>
          prev.map((item) =>
            `${item.file.name}:${item.file.size}:${item.file.lastModified}` === key
              ? { ...item, ...nextState }
              : item,
          ),
        );
      }),
    );
  };

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    void appendFiles(Array.from(fileList).filter(isSupportedUpload));
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const plainText = e.dataTransfer.getData("text/plain");
    const uriList = e.dataTransfer.getData("text/uri-list");
    const htmlText = e.dataTransfer.getData("text/html");
    const parsed = parseDroppedReferenceText(plainText, uriList, htmlText);
    const droppedFiles = Array.from(e.dataTransfer.files ?? []).filter(isSupportedUpload);
    const droppedFilePaths = extractDroppedFilePaths(droppedFiles);
    const droppedDocFiles = droppedFiles.filter((f) => !isTriconveyReferenceName(f.name));
    const hasOnlyTriconveyRefs =
      droppedFiles.length > 0 && droppedFiles.every((f) => isTriconveyReferenceName(f.name));

    if (droppedFilePaths.length) {
      void appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: droppedFilePaths }, null, 2))]);
      return;
    }
    if (parsed?.localPaths?.length) {
      void appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: parsed.localPaths }, null, 2))]);
      return;
    }
    if (droppedDocFiles.length > 0) {
      void appendFiles(droppedDocFiles);
      return;
    }
    if (parsed?.matterPayload) {
      void appendFiles([makeReferenceFile(parsed.matterPayload)]);
      return;
    }
    if (droppedFiles.length > 0 && !hasOnlyTriconveyRefs) {
      void appendFiles(droppedFiles);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <div className="flex min-h-screen flex-col bg-background font-sans">
      <Header
        onBack={onBack}
        userInitials={userInitials}
        onProfile={onProfile}
        onSettings={onSettings}
        onPolicy={onPolicy}
        onLogout={onLogout}
      />

      <div className="flex flex-1 items-center justify-center p-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-2xl space-y-8"
        >
          <div className="space-y-2 text-center">
            <h1 className="font-serif text-4xl italic tracking-tight text-foreground">Convey Agent</h1>
            <p className="text-[1.05rem] text-muted-foreground">
              Upload Section 32 source documents to begin extraction and review
            </p>
          </div>

          <div
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={[
              "relative flex flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed bg-card p-16 transition-all duration-200",
              isDragging ? "scale-[1.01] border-primary bg-primary/5" : "border-border hover:border-primary/40",
            ].join(" ")}
          >
            <input
              type="file"
              ref={fileInputRef}
              className="hidden"
              multiple
              onChange={(e) => handleFiles(e.target.files)}
              accept=".pdf,.docx,.doc,.json,.tmp"
            />
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted text-primary">
              <Upload className="h-8 w-8" />
            </div>
            <div className="text-center">
              <p className="text-[1.1rem] font-semibold text-foreground">Drag and drop files here</p>
              <p className="mt-1 text-sm text-muted-foreground">
                PDFs, Word documents, or TriConvey folder drops — ready for canonical extraction and Convey autofill
              </p>
            </div>
            <Button
              variant="outline"
              className="mt-2 border-border font-semibold"
              onClick={() => fileInputRef.current?.click()}
            >
              Browse Files
            </Button>
          </div>

          {errorMessage ? (
            <div className="rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {errorMessage}
            </div>
          ) : null}

          <div className="pt-2">
            <Button
              disabled={files.length === 0}
              onClick={() => onUploadComplete(files.map((item) => item.file))}
              className="h-14 w-full rounded-xl bg-primary text-[1.05rem] font-bold text-white shadow-lg shadow-primary/20 transition-all hover:bg-primary/90 disabled:opacity-50 disabled:shadow-none"
            >
              Start Review
              <ArrowRight className="ml-2 h-5 w-5" />
            </Button>
          </div>

          <AnimatePresence>
            {files.length > 0 && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="space-y-3"
              >
                <div className="flex items-center justify-between px-1">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
                    Ready for analysis ({files.length})
                  </h3>
                  <button
                    onClick={() => setFiles([])}
                    className="text-xs font-semibold text-muted-foreground transition-colors hover:text-destructive"
                  >
                    Clear all
                  </button>
                </div>
                <div className="grid gap-2">
                  {files.map((item, i) => (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="group flex items-center justify-between rounded-xl border border-border bg-card p-4 shadow-sm"
                    >
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                          <FileText className="h-5 w-5" />
                        </div>
                        <div>
                          <p className="max-w-[500px] truncate text-sm font-semibold text-foreground">
                            {/* <p className="text-sm font-semibold text-foreground break-words"></p> */}
                            {item.displayName || item.file.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {(() => {
                              const n = item.file.name.toLowerCase();
                              if (n.endsWith(".pdf") || n.endsWith(".docx") || n.endsWith(".doc"))
                                return `${(item.file.size / 1024 / 1024).toFixed(2)} MB`;
                              return item.subtitle || "TriConvey reference";
                            })()}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => removeFile(i)}
                        className="rounded-lg p-2 text-muted-foreground/50 transition-all hover:bg-accent hover:text-destructive"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </motion.div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>
    </div>
  );
}
