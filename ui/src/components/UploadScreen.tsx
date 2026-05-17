import React, { useState, useRef, useEffect } from "react";
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
  initialFiles?: File[];
}

interface UploadListItem {
  file: File;
  displayName?: string;
  subtitle?: string;
  resolving?: boolean;
  groupKey?: string;
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

async function hashFile(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
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
  initialFiles,
}: UploadPageProps) {
  const [files, setFiles] = useState<UploadListItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [duplicatesSkipped, setDuplicatesSkipped] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks SHA-256 hashes of all files added so far for content-based dedup
  const fileHashesRef = useRef<Set<string>>(new Set());
  const initialFilesHandled = useRef(false);

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
    // Hash all incoming files in parallel, then filter duplicates by content.
    // TriConvey reference files (tiny JSON payloads) are hashed the same way —
    // identical drops are naturally deduplicated.
    const hashed = await Promise.all(
      nextFiles.map(async (file) => ({ file, hash: await hashFile(file) })),
    );

    let skipped = 0;
    const fresh = hashed.filter(({ hash }) => {
      if (fileHashesRef.current.has(hash)) { skipped++; return false; }
      fileHashesRef.current.add(hash);
      return true;
    });

    if (skipped > 0) setDuplicatesSkipped((n) => n + skipped);
    if (fresh.length === 0) return;

    const nextItems = fresh.map(({ file }): UploadListItem => {
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
    setFiles((prev) => [...prev, ...nextItems]);

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

        let resolvedNames: string[] = [];
        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            const resolved = await onResolveTriconveyReference(payloadText);
            resolvedNames = resolved.resolved.map((item) => item.name).filter(Boolean);
            if (resolvedNames.length) break;
          } catch {
            // Keep retrying briefly while Smokeball/TriConvey finishes materialising PDFs.
          }
          if (attempt < 2) {
            await delay(attempt === 0 ? 1000 : 1200);
          }
        }

        setFiles((prev) => {
          const idx = prev.findIndex(
            (item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}` === key,
          );
          if (idx === -1) return prev;

          if (resolvedNames.length > 1) {
            const expanded: UploadListItem[] = resolvedNames.map((name) => ({
              file,
              displayName: name,
              subtitle: "TriConvey document",
              resolving: false,
              groupKey: key,
            }));
            return [...prev.slice(0, idx), ...expanded, ...prev.slice(idx + 1)];
          }

          const single: UploadListItem =
            resolvedNames.length === 1
              ? { file, displayName: resolvedNames[0], subtitle: "TriConvey document", resolving: false, groupKey: key }
              : { ...nextState };
          return prev.map((item, i) => (i === idx ? single : item));
        });
      }),
    );
  };

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    void appendFiles(Array.from(fileList).filter(isSupportedUpload));
  };

  useEffect(() => {
    if (initialFilesHandled.current || !initialFiles?.length) return;
    initialFilesHandled.current = true;
    void appendFiles(initialFiles.filter(isSupportedUpload));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    setFiles((prev) => {
      const target = prev[index];
      if (target?.groupKey) {
        return prev.filter((item) => item.groupKey !== target.groupKey);
      }
      return prev.filter((_, i) => i !== index);
    });
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-gradient-to-br from-slate-50 via-blue-50/30 to-violet-50/20 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 font-sans overflow-hidden">
      <style>{`
        .upload-orb-1 { position:fixed; top:-15%; left:-10%; width:55%; height:55%; background:radial-gradient(circle, rgba(139,92,246,0.10) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .upload-orb-2 { position:fixed; bottom:-10%; right:-5%; width:45%; height:45%; background:radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .upload-glass { background:rgba(255,255,255,0.70); backdrop-filter:blur(20px) saturate(160%); -webkit-backdrop-filter:blur(20px) saturate(160%); border:1px solid rgba(255,255,255,0.6); }
        .dark .upload-glass { background:rgba(15,23,42,0.55); border-color:rgba(255,255,255,0.08); }
        .upload-file-card { background:rgba(255,255,255,0.65); backdrop-filter:blur(12px); border:1px solid rgba(255,255,255,0.5); }
        .dark .upload-file-card { background:rgba(30,41,59,0.50); border-color:rgba(255,255,255,0.07); }
      `}</style>
      <div className="upload-orb-1" />
      <div className="upload-orb-2" />
      <Header
        onBack={onBack}
        userInitials={userInitials}
        onProfile={onProfile}
        onSettings={onSettings}
        onPolicy={onPolicy}
        onLogout={onLogout}
      />

      <div className="relative z-10 flex flex-1 items-center justify-center p-6">
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
              "upload-glass relative flex flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed p-16 transition-all duration-200",
              isDragging ? "scale-[1.01] border-primary bg-primary/5" : "border-white/40 dark:border-white/10 hover:border-primary/40",
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
                PDFs, Word documents, or TriConvey folder drops â€” ready for canonical extraction and Convey autofill
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

          {duplicatesSkipped > 0 && (
            <div className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 px-4 py-2.5 text-sm text-amber-700 dark:text-amber-400">
              <span>
                {duplicatesSkipped} duplicate file{duplicatesSkipped !== 1 ? "s" : ""} skipped — identical content already in the list.
              </span>
              <button
                onClick={() => setDuplicatesSkipped(0)}
                className="ml-3 shrink-0 text-amber-500 hover:text-amber-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          )}

          <div className="pt-2">
            <Button
              disabled={files.length === 0}
              onClick={() => {
                const seen = new Set<string>();
                const unique = files.map((item) => item.file).filter((f) => {
                  const k = `${f.name}:${f.size}:${f.lastModified}`;
                  return seen.has(k) ? false : (seen.add(k), true);
                });
                onUploadComplete(unique);
              }}
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
                    onClick={() => { setFiles([]); fileHashesRef.current.clear(); setDuplicatesSkipped(0); }}
                    className="text-xs font-semibold text-muted-foreground transition-colors hover:text-destructive"
                  >
                    Clear all
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {files.map((item, i) => {
                    const isLastOdd = files.length % 2 !== 0 && i === files.length - 1;
                    return (
                      <motion.div
                        key={`${item.groupKey ?? item.file.name}-${i}`}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        className={[
                          "upload-file-card group flex min-h-[72px] items-center justify-between rounded-xl p-4 shadow-sm",
                          isLastOdd ? "col-span-2" : "",
                        ].join(" ")}
                      >
                        <div className="flex min-w-0 flex-1 items-center gap-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                            <FileText className="h-5 w-5" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-foreground">
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
                    );
                  })}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>
    </div>
  );
}
