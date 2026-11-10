/**
 * useMatterIntake — the list of documents queued for a new matter.
 *
 * Accepts files from the file picker or a drop anywhere on the dashboard,
 * including TriConvey drops that arrive as local paths or a matter payload.
 */

import { useRef, useState } from "react";

export interface IntakeItem {
  file: File;
  displayName?: string;
  subtitle?: string;
  resolving?: boolean;
  groupKey?: string;
}

type ResolveReference = (
  payloadText: string,
) => Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }>;

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
  for (const raw of rawValues) {
    const text = raw.trim();
    if (!text) continue;
    for (const match of text.matchAll(/file:\/\/\/[^\s"'<>]+/gi)) paths.add(match[0]);
    for (const match of text.matchAll(/@?[A-Za-z]:\\[^\r\n"<>|?*]+/g)) paths.add(match[0].replace(/^@+/, ""));
    for (const token of text.split(/\s+/)) {
      const cleaned = token.trim().replace(/^@+/, "");
      if (cleaned.startsWith("file:///") || /^[A-Za-z]:\\/.test(cleaned)) paths.add(cleaned);
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

function isSupportedUpload(file: File): boolean {
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
}

function droppedFilePaths(files: File[]): string[] {
  const paths = new Set<string>();
  for (const file of files) {
    const candidate = (file as File & { path?: string }).path;
    if (typeof candidate === "string" && candidate.trim()) paths.add(candidate.trim());
  }
  return Array.from(paths);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function hashFile(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function itemKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

export function useMatterIntake(onResolveReference?: ResolveReference) {
  const [items, setItems] = useState<IntakeItem[]>([]);
  const [duplicatesSkipped, setDuplicatesSkipped] = useState(0);
  const [unsupportedSkipped, setUnsupportedSkipped] = useState(0);
  const hashesRef = useRef<Set<string>>(new Set());

  const resolveReference = async (file: File) => {
    const payloadText = await file.text();
    let names: string[] = [];
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const resolved = await onResolveReference!(payloadText);
        names = resolved.resolved.map((item) => item.name).filter(Boolean);
        if (names.length) break;
      } catch {
        // TriConvey may still be writing the PDFs, so try again briefly.
      }
      if (attempt < 2) await delay(attempt === 0 ? 1000 : 1200);
    }

    const key = itemKey(file);
    setItems((prev) => {
      const index = prev.findIndex((item) => itemKey(item.file) === key);
      if (index === -1) return prev;
      const replacement: IntakeItem[] = names.length
        ? names.map((name) => ({ file, displayName: name, subtitle: "TriConvey document", groupKey: key }))
        : [{ file, displayName: "TriConvey documents", subtitle: "Not found yet. Download them in TriConvey, then drop again." }];
      return [...prev.slice(0, index), ...replacement, ...prev.slice(index + 1)];
    });
  };

  const addFiles = async (incoming: File[]) => {
    const supported = incoming.filter(isSupportedUpload);
    if (supported.length < incoming.length) {
      setUnsupportedSkipped((n) => n + incoming.length - supported.length);
    }

    const hashed = await Promise.all(supported.map(async (file) => ({ file, hash: await hashFile(file) })));
    const fresh: File[] = [];
    let duplicates = 0;
    for (const { file, hash } of hashed) {
      if (hashesRef.current.has(hash)) {
        duplicates += 1;
      } else {
        hashesRef.current.add(hash);
        fresh.push(file);
      }
    }
    if (duplicates > 0) setDuplicatesSkipped((n) => n + duplicates);
    if (fresh.length === 0) return;

    setItems((prev) => [
      ...prev,
      ...fresh.map((file): IntakeItem =>
        isTriconveyReferenceName(file.name) && onResolveReference
          ? { file, displayName: "TriConvey documents", subtitle: "Finding documents...", resolving: true }
          : { file },
      ),
    ]);

    if (onResolveReference) {
      await Promise.all(fresh.filter((file) => isTriconveyReferenceName(file.name)).map(resolveReference));
    }
  };

  /** Reads the drop synchronously, because browsers clear the data after the event. */
  const addFromDrop = (data: DataTransfer) => {
    const plain = data.getData("text/plain");
    const uriList = data.getData("text/uri-list");
    const html = data.getData("text/html");
    const dropped = Array.from(data.files ?? []);

    const filePaths = droppedFilePaths(dropped);
    if (filePaths.length) {
      void addFiles([makeReferenceFile(JSON.stringify({ LocalPaths: filePaths }, null, 2))]);
      return;
    }
    const textPaths = extractLocalPaths(plain, uriList, html);
    if (textPaths.length) {
      void addFiles([makeReferenceFile(JSON.stringify({ LocalPaths: textPaths }, null, 2))]);
      return;
    }
    if (dropped.length) {
      void addFiles(dropped);
      return;
    }
    if (plain.trim().includes('"MatterId"')) {
      void addFiles([makeReferenceFile(plain.trim())]);
    }
  };

  const removeItem = (index: number) => {
    setItems((prev) => {
      const target = prev[index];
      return target?.groupKey ? prev.filter((item) => item.groupKey !== target.groupKey) : prev.filter((_, i) => i !== index);
    });
  };

  const dismissNotices = () => {
    setDuplicatesSkipped(0);
    setUnsupportedSkipped(0);
  };

  const clear = () => {
    setItems([]);
    hashesRef.current.clear();
    dismissNotices();
  };

  /** One File per upload: a TriConvey drop can list several documents from one file. */
  const uploadFiles = (): File[] => {
    const seen = new Set<string>();
    return items
      .map((item) => item.file)
      .filter((file) => {
        const key = itemKey(file);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  };

  return { items, duplicatesSkipped, unsupportedSkipped, addFiles, addFromDrop, removeItem, dismissNotices, clear, uploadFiles };
}

export type MatterIntake = ReturnType<typeof useMatterIntake>;
