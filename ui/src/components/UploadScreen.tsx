import React, { useState, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Upload, FileText, X, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface UploadPageProps {
  onUploadComplete: (files: File[]) => void | Promise<void>;
  errorMessage?: string;
}

export function UploadScreen({ onUploadComplete, errorMessage }: UploadPageProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    const nextFiles = Array.from(fileList).filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    setFiles((prev) => {
      const seen = new Set(prev.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...prev, ...nextFiles.filter((file) => !seen.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
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
          <h1 className="text-4xl font-serif italic tracking-tight text-slate-900">TriConvey Agent</h1>
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
            accept=".pdf"
          />
          <div className="w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center text-primary">
            <Upload className="w-8 h-8" />
          </div>
          <div className="text-center">
            <p className="text-[1.1rem] font-semibold text-slate-700">Drag and drop files here</p>
            <p className="text-slate-400 text-sm mt-1">PDF files only, ready for canonical extraction and TriConvey autofill</p>
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
                        <p className="text-xs text-slate-400">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
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
