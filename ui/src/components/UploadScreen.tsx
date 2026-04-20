import React, { useState, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Upload, FileText, X, ArrowRight } from "lucide-react";
import { Button } from "./ui/button";

interface UploadPageProps {
  onUploadComplete: (files: File[]) => void | Promise<void>;
  errorMessage?: string;
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

function parseDroppedReferenceText(rawPlain: string, rawUriList = "", rawHtml = ""): { localPaths?: string[]; matterPayload?: string } | null {
  const matterPayload = rawPlain.trim().includes("\"MatterId\"") ? rawPlain.trim() : undefined;
  const localPaths = extractLocalPaths(rawPlain, rawUriList, rawHtml);

  if (!matterPayload && localPaths.length === 0) return null;
  return {
    matterPayload,
    localPaths: localPaths.length ? localPaths : undefined,
  };
}

export function UploadScreen({ onUploadComplete, errorMessage }: UploadPageProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isSupportedUpload = (file: File) => {
    const name = file.name.toLowerCase();
    return (
      file.type === "application/pdf" ||
      name.endsWith(".pdf") ||
      isTriconveyReferenceName(name)
    );
  };

  const appendFiles = (nextFiles: File[]) => {
    setFiles((prev) => {
      const seen = new Set(prev.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...prev, ...nextFiles.filter((file) => !seen.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  };

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    appendFiles(Array.from(fileList).filter(isSupportedUpload));
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
    const droppedPdfFiles = droppedFiles.filter((file) => !isTriconveyReferenceName(file.name));

    const hasOnlyTriconveyRefs =
      droppedFiles.length > 0 &&
      droppedFiles.every((file) => isTriconveyReferenceName(file.name));

    // In the desktop app, dropped File objects can carry a native `path`
    // property. Prefer those concrete local paths over matter metadata so we
    // import the exact cached PDF TriConvey just downloaded.
    if (droppedFilePaths.length) {
      appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: droppedFilePaths }, null, 2))]);
      return;
    }

    // Next preference: explicit paths from drag text. Browsers often expose the
    // dropped `.smokeball.tmp` items as virtual files that cannot actually be
    // uploaded, which causes fetch to fail before the backend sees `/api/runs`.
    if (parsed?.localPaths?.length) {
      appendFiles([makeReferenceFile(JSON.stringify({ LocalPaths: parsed.localPaths }, null, 2))]);
      return;
    }
    if (droppedPdfFiles.length > 0) {
      appendFiles(droppedPdfFiles);
      return;
    }
    if (parsed?.matterPayload) {
      appendFiles([makeReferenceFile(parsed.matterPayload)]);
      return;
    }

    if (droppedFiles.length > 0 && !hasOnlyTriconveyRefs) {
      appendFiles(droppedFiles);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 font-sans">
      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-2xl space-y-8"
      >
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-serif italic tracking-tight text-slate-900">Convey Agent</h1>
          <p className="text-slate-500 text-[1.05rem]">Upload Section 32 source documents to begin extraction and review</p>
        </div>

        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          className={`
            relative border-2 border-dashed rounded-2xl p-12 transition-all duration-200 flex flex-col items-center justify-center gap-4 bg-white
            ${isDragging ? 'border-primary bg-primary/5 scale-[1.01]' : 'border-slate-200 hover:border-slate-300'}
          `}
        >
          <input 
            type="file" 
            ref={fileInputRef}
            className="hidden" 
            multiple 
            onChange={(e) => handleFiles(e.target.files)}
            accept=".pdf,.json,.tmp"
          />
          <div className="w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center text-primary">
            <Upload className="w-8 h-8" />
          </div>
          <div className="text-center">
            <p className="text-[1.1rem] font-semibold text-slate-700">Drag and drop files here</p>
            <p className="text-slate-400 text-sm mt-1">PDFs or TriConvey folder drops, ready for canonical extraction and Convey autofill</p>
          </div>
          <Button 
            variant="outline" 
            className="mt-2 border-slate-200 font-semibold"
            onClick={() => fileInputRef.current?.click()}
          >
            Browse Files
          </Button>
        </div>

        {errorMessage ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {errorMessage}
          </div>
        ) : null}

        <div className="pt-2">
          <Button 
            disabled={files.length === 0}
            onClick={() => onUploadComplete(files)}
            className="w-full h-14 text-[1.05rem] font-bold bg-primary hover:bg-primary/90 text-white rounded-xl shadow-lg shadow-primary/20 transition-all disabled:opacity-50 disabled:shadow-none"
          >
            Start Review
            <ArrowRight className="ml-2 w-5 h-5" />
          </Button>
        </div>

        <AnimatePresence>
          {files.length > 0 && (
            <motion.div 
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="space-y-3"
            >
              <div className="flex items-center justify-between px-1">
                <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider">Ready for analysis ({files.length})</h3>
                <button onClick={() => setFiles([])} className="text-xs font-semibold text-slate-400 hover:text-destructive transition-colors">Clear all</button>
              </div>
              <div className="grid gap-2">
                {files.map((file, i) => (
                  <motion.div 
                    key={i}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="flex items-center justify-between p-4 bg-white border border-slate-100 rounded-xl shadow-sm group"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-slate-50 flex items-center justify-center text-slate-400">
                        <FileText className="w-5 h-5" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-slate-700 truncate max-w-[300px]">{file.name}</p>
                        <p className="text-xs text-slate-400">
                          {file.name.toLowerCase().endsWith(".pdf")
                            ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
                            : "TriConvey reference"}
                        </p>
                      </div>
                    </div>
                    <button 
                      onClick={() => removeFile(i)}
                      className="p-2 hover:bg-slate-50 rounded-lg text-slate-300 hover:text-destructive transition-all"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </motion.div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
