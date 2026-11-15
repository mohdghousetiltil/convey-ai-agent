/**
 * Pieces of an assistant answer: formatted text, the pages it read, and a
 * viewer that opens the exact page a source points to.
 */

import React, { useState } from "react";
import { ChevronDown, ChevronUp, FileSearch, FileText, Loader2, ShieldCheck, Sparkles } from "lucide-react";
import { ChatCitation, PageRef, getRunPage } from "../../lib/api";

// ---------------------------------------------------------------------------
// Formatted answer text
// ---------------------------------------------------------------------------

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  // **bold** only. Everything else stays plain text, so nothing from the model is
  // ever injected as HTML.
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={`${keyPrefix}-${index}`} className="font-semibold text-foreground">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <React.Fragment key={`${keyPrefix}-${index}`}>{part}</React.Fragment>
    ),
  );
}

type Block = { kind: "p"; lines: string[] } | { kind: "ul" | "ol"; items: string[] } | { kind: "h"; text: string };

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    const last = blocks[blocks.length - 1];
    if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] });
    } else if (heading) {
      blocks.push({ kind: "h", text: heading[1] });
    } else if (bullet) {
      if (last?.kind === "ul") last.items.push(bullet[1]);
      else blocks.push({ kind: "ul", items: [bullet[1]] });
    } else if (numbered) {
      if (last?.kind === "ol") last.items.push(numbered[1]);
      else blocks.push({ kind: "ol", items: [numbered[1]] });
    } else if (last?.kind === "p") {
      last.lines.push(line);
    } else {
      blocks.push({ kind: "p", lines: [line] });
    }
  }
  return blocks.filter((block) => block.kind !== "p" || block.lines.length > 0);
}

/** Paragraphs, lists, headings and bold, from the model's plain-text answer. */
export function RichText({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {toBlocks(text).map((block, index) => {
        if (block.kind === "h") {
          return (
            <p key={index} className="font-semibold text-foreground">
              {renderInline(block.text, `h${index}`)}
            </p>
          );
        }
        if (block.kind === "p") {
          return (
            <p key={index} className="whitespace-pre-wrap">
              {renderInline(block.lines.join("\n"), `p${index}`)}
            </p>
          );
        }
        const items = block.items.map((item, itemIndex) => (
          <li key={itemIndex}>{renderInline(item, `l${index}-${itemIndex}`)}</li>
        ));
        return block.kind === "ul" ? (
          <ul key={index} className="list-disc space-y-1 pl-5 marker:text-muted-foreground">{items}</ul>
        ) : (
          <ol key={index} className="list-decimal space-y-1 pl-5 marker:text-muted-foreground">{items}</ol>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Model and retrieval summary
// ---------------------------------------------------------------------------

/** "anthropic/claude-sonnet-5.5" -> "Claude Sonnet 5.5". */
export function modelLabel(model?: string | null): string {
  if (!model) return "";
  const name = model.split("/").pop() ?? model;
  return name
    .split("-")
    .map((word) => (/^\d/.test(word) ? word : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(" ")
    .replace(/^Gpt/, "GPT");
}

function plural(count: number, word: string): string {
  return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
}

export function AnswerMeta({
  pagesSearched,
  documentsSearched,
  pagesRead,
  model,
  reviewed,
}: {
  pagesSearched?: number;
  documentsSearched?: number;
  pagesRead: number;
  model?: string | null;
  reviewed?: boolean;
}) {
  const label = modelLabel(model);
  if (!pagesSearched && !pagesRead && !label && !reviewed) return null;
  const chip = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.67rem] font-medium";
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-1">
      {pagesSearched ? (
        <span
          className={`${chip} bg-muted text-muted-foreground`}
          title={`Searched every page of ${plural(documentsSearched || 0, "document")}, then read only the pages that matched.`}
        >
          <FileSearch className="h-2.5 w-2.5" aria-hidden="true" />
          Searched {plural(pagesSearched, "page")}
          {pagesRead ? `, read ${pagesRead}` : ""}
        </span>
      ) : pagesRead ? (
        <span className={`${chip} bg-muted text-muted-foreground`}>
          <FileSearch className="h-2.5 w-2.5" aria-hidden="true" />
          Read {plural(pagesRead, "page")}
        </span>
      ) : null}
      {reviewed ? (
        <span className={`${chip} bg-violet-100 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300`}>
          <ShieldCheck className="h-2.5 w-2.5" aria-hidden="true" />
          Double-checked
        </span>
      ) : null}
      {label ? (
        <span className={`${chip} bg-primary/10 text-primary`}>
          <Sparkles className="h-2.5 w-2.5" aria-hidden="true" />
          {label}
        </span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sources: the pages behind the answer
// ---------------------------------------------------------------------------

function shortName(file: string): string {
  return file.replace(/\.pdf$/i, "");
}

function Highlighted({ text, quote }: { text: string; quote?: string | null }) {
  const needle = (quote || "").replace(/^…|…$/g, "").trim();
  const at = needle.length >= 6 ? text.indexOf(needle) : -1;
  if (at === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-500/30">{needle}</mark>
      {text.slice(at + needle.length)}
    </>
  );
}

function SourceRow({ runId, source }: { runId?: string; source: ChatCitation }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<{ text: string; truncated?: boolean } | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const file = source.file || "Unknown document";
  const canOpen = Boolean(runId && source.file && source.page);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || page || !canOpen) return;
    setState("loading");
    try {
      const result = await getRunPage(runId!, source.file!, source.page!);
      setPage({ text: result.text, truncated: result.truncated });
      setState("idle");
    } catch {
      setState("error");
    }
  };

  return (
    <li className="overflow-hidden rounded-xl border border-border bg-card text-[0.73rem] shadow-sm">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <FileText className="h-3.5 w-3.5 shrink-0 text-primary/70" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold text-foreground" title={file}>{shortName(file)}</span>
          {source.quote && !open ? (
            <span className="block truncate text-muted-foreground">{source.quote}</span>
          ) : null}
        </span>
        {source.page ? (
          <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 font-semibold tabular-nums text-muted-foreground">
            p.{source.page}
            {source.page_count ? ` of ${source.page_count}` : ""}
          </span>
        ) : null}
        {open ? <ChevronUp className="h-3 w-3 shrink-0 text-muted-foreground" /> : <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />}
      </button>
      {open ? (
        <div className="border-t border-border bg-muted/30 px-3 py-2.5">
          {state === "loading" ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              Opening page…
            </p>
          ) : page ? (
            <>
              <div className="custom-scrollbar max-h-64 overflow-y-auto whitespace-pre-wrap font-mono text-[0.7rem] leading-relaxed text-foreground/90">
                <Highlighted text={page.text} quote={source.quote} />
              </div>
              {page.truncated ? <p className="mt-1.5 text-muted-foreground">Showing the first part of a long page.</p> : null}
            </>
          ) : (
            <>
              {source.quote ? <p className="italic leading-relaxed text-muted-foreground">"{source.quote}"</p> : null}
              {state === "error" ? (
                <p className="mt-1 text-muted-foreground">This page couldn't be opened. Open the document to check it.</p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** The pages an answer came from. Each one opens to the page text, with the quote marked. */
export function PageSources({ runId, sources, pagesRead }: { runId?: string; sources: ChatCitation[]; pagesRead?: PageRef[] }) {
  const [showAll, setShowAll] = useState(false);
  if (!sources.length) return null;
  const fromRead = Boolean(pagesRead?.length);
  const visible = showAll ? sources : sources.slice(0, 3);
  return (
    <div className="w-full space-y-1.5">
      <p className="px-1 text-[0.68rem] font-bold uppercase tracking-wider text-muted-foreground">
        {fromRead ? "Pages used" : "Related pages"}
      </p>
      <ul className="space-y-1.5">
        {visible.map((source, index) => (
          <SourceRow key={`${source.file}-${source.page}-${index}`} runId={runId} source={source} />
        ))}
      </ul>
      {sources.length > 3 ? (
        <button
          type="button"
          onClick={() => setShowAll((value) => !value)}
          className="px-1 text-[0.7rem] font-semibold text-muted-foreground transition-colors hover:text-foreground"
        >
          {showAll ? "Show fewer" : `Show ${sources.length - 3} more`}
        </button>
      ) : null}
    </div>
  );
}
