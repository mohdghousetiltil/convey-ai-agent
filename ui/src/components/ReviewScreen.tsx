import React, { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, ChevronRight, Copy, Loader2, Sparkles, Upload, X } from "lucide-react";
import { useAuth } from "../lib/AuthContext";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AnswerUpdatePayload, ChatAnswerPayload, ReviewFieldItem, ReviewRunPayload, UpdateStatusPayload, pushS32ToSmokeball, useCopyRuleForRow } from "../lib/api";
import { Header } from "./Header";

type HistoryTurn = { role: "user" | "assistant"; content: string };
type ChatMode = "quick" | "standard" | "thorough";
import { Chatbot } from "./Chatbot";

type DraftValue = string | boolean | number | null;

function CopyOnlyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col group relative">
      <label className="text-[0.75rem] uppercase tracking-[0.05em] text-muted-foreground font-semibold mb-1.5 flex items-center gap-2">
        {label}
        <button onClick={handleCopy} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-accent rounded text-muted-foreground hover:text-primary">
          {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
        </button>
      </label>
      <div className="text-[0.95rem] font-medium text-foreground select-all cursor-default leading-snug">
        {value || <span className="text-muted-foreground">—</span>}
      </div>
    </div>
  );
}

function StatusPill({ item }: { item?: ReviewFieldItem }) {
  if (!item) {
    return <span className="rounded-full bg-muted px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground">Unmapped</span>;
  }
  if (item.needs_review) {
    return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">Review</span>;
  }
  if (item.presentation_hints?.answer_origin === "ai_review") {
    return <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-sky-700">AI</span>;
  }
  if (item.confidence >= 0.9) {
    return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-emerald-700">Auto</span>;
  }
  return <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-yellow-800 dark:bg-yellow-500/20 dark:text-yellow-300">Auto</span>;
}

function hasDisplayValue(value: DraftValue) {
  if (value === null || value === undefined) return false;
  return String(value).trim() !== "";
}

function FieldLabel({ text, item }: { text: string; item?: ReviewFieldItem }) {
  return (
    <span className="flex items-center gap-2">
      <span>{text}</span>
      <StatusPill item={item} />
    </span>
  );
}

interface ReviewScreenProps {
  run: ReviewRunPayload;
  onBack: () => void;
  onProfile: () => void;
  onSettings: () => void;
  onAbout: () => void;
  onPolicy: () => void;
  onLogout: () => void;
  onSaveReview: (updates: Record<string, AnswerUpdatePayload>) => Promise<void> | void;
  onAutofill: (updates: Record<string, AnswerUpdatePayload>) => Promise<void> | void;
  onCancelAutofill?: () => Promise<void> | void;
  onAskAssistant: (question: string, history: HistoryTurn[], mode: ChatMode, signal?: AbortSignal, sessionId?: string) => Promise<ChatAnswerPayload>;
  onApplyPatch?: (questionId: string, newValue: string, reason: string) => Promise<void>;
  onReviewAgain?: () => Promise<void>;
  onRunReprocessed?: () => void;
  isSaving: boolean;
  isAutofilling: boolean;
  isReanalysing?: boolean;
  errorMessage?: string;
  onDismissError?: () => void;
  updateStatus?: UpdateStatusPayload | null;
  onResolveTriconveyReference?: (payloadText: string) => Promise<{ resolved: Array<{ name: string; path: string }>; display_name: string; subtitle: string }>;
}

export function ReviewScreen(props: ReviewScreenProps) {
  const { run, onBack, onProfile, onSettings, onAbout, onPolicy, onLogout, onSaveReview, onAutofill, onCancelAutofill, onAskAssistant, onApplyPatch, onReviewAgain, onRunReprocessed, isSaving, isAutofilling, isReanalysing, errorMessage, onDismissError, updateStatus, onResolveTriconveyReference } = props;
  const { user } = useAuth();
  const userInitials = user?.name
    ? user.name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()
    : (user?.email?.[0] ?? "?").toUpperCase();
  const [activeTab, setActiveTab] = useState("page-1");
  const clientName = run.matter.client_name || "Matter Client";
  const volumeFolio = run.matter.volume_folio || "Volume / Folio";
  const propertyAddress = run.matter.property_address || "Property Address";
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({});
  const [reviewItemsCollapsed, setReviewItemsCollapsed] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);

  // ── Push to Smokeball state ───────────────────────────────────────────────
  const [pushDialogOpen, setPushDialogOpen] = useState(false);
  const [pushMatterNumber, setPushMatterNumber] = useState(run.matter.matter_ref ?? "");
  const [isPushing, setIsPushing] = useState(false);
  const [pushResult, setPushResult] = useState<{ success: boolean; message: string; warning?: string } | null>(null);
  const [isActionDropdownOpen, setIsActionDropdownOpen] = useState(false);
  const [primaryAction, setPrimaryAction] = useState<"push" | "autofill">("push");

  // ── DB pill (copy-rule override) state ───────────────────────────────────
  const [dbPillLoading, setDbPillLoading] = useState<Record<number, boolean>>({});
  const [dbPillToast, setDbPillToast] = useState<{ row: number; message: string; error: boolean } | null>(null);
  const [copyGlowRow, setCopyGlowRow] = useState<number | null>(null);

  async function handleUseCopyRule(rowNum: number) {
    const runId = run.manifest?.run_id;
    if (!runId) return;
    setDbPillLoading((prev) => ({ ...prev, [rowNum]: true }));
    setDbPillToast(null);
    try {
      setCopyGlowRow(rowNum);
      const result = await useCopyRuleForRow(runId, rowNum);
      setDraft(`sec32_1.1_outgoing_${rowNum}_amount`, result.amount);
      setDbPillToast({ row: rowNum, message: `Applied: ${result.amount} (matched "${result.matched_rule}")`, error: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      const isNotFound = msg.includes("No copy price") || msg.includes("404");
      setDbPillToast({
        row: rowNum,
        message: isNotFound ? "No copy price found for this authority." : msg,
        error: true,
      });
    } finally {
      setDbPillLoading((prev) => ({ ...prev, [rowNum]: false }));
      window.setTimeout(() => setCopyGlowRow((prev) => (prev === rowNum ? null : prev)), 700);
      window.setTimeout(() => setDbPillToast(null), 4000);
    }
  }

  const runIdRef = useRef(run.manifest?.run_id);
  useEffect(() => {
    const newRunId = run.manifest?.run_id;
    const isNewRun = newRunId !== runIdRef.current;
    runIdRef.current = newRunId;
    setDrafts({});
    setReviewItemsCollapsed(true);
    setPushMatterNumber(run.matter.matter_ref ?? "");
    setPushResult(null);
    if (isNewRun) setChatOpen(true);
  }, [run]);

  useEffect(() => {
    setReviewItemsCollapsed(true);
  }, [activeTab]);

  const pages = [
    { id: "page-1", label: "Sec 32 (1)", tab: "Sec. 32 (1)" },
    { id: "page-2", label: "Sec 32 (2)", tab: "Sec. 32 (2)" },
    { id: "page-3", label: "Sec 32 (3)", tab: "Sec. 32 (3)" },
    { id: "page-4", label: "Sec 32 (4)", tab: "Sec. 32 (4)" },
    { id: "page-5", label: "Sec 32 (5)", tab: "Sec. 32 (5)" },
    { id: "page-6", label: "Sec 32 (6)", tab: "Sec. 32 (6)" },
  ];

  const tabsByName = Object.fromEntries(run.tabs.map((tab) => [tab.tab, tab.items]));
  const fields = Object.fromEntries(run.tabs.flatMap((tab) => tab.items.map((item) => [item.question_id, item])));
  const currentPage = pages.find((page) => page.id === activeTab);
  const currentItems = currentPage ? tabsByName[currentPage.tab] ?? [] : [];

  const getDraft = (qid: string): DraftValue => (qid in drafts ? drafts[qid] : fields[qid]?.value ?? "");
  const setDraft = (qid: string, value: DraftValue) => setDrafts((prev) => ({ ...prev, [qid]: value }));
  const textValue = (qid: string) => {
    const value = getDraft(qid);
    return value === null || value === undefined ? "" : String(value);
  };
  const boolValue = (qid: string) => Boolean(getDraft(qid));
  const buildUpdates = (): Record<string, AnswerUpdatePayload> =>
    Object.fromEntries(Object.entries(drafts).map(([qid, value]) => [qid, { value, needs_review: false }]));

  const handlePushToSmokeball = async () => {
    if (!pushMatterNumber.trim()) return;
    setIsPushing(true);
    setPushResult(null);
    // Build answers from current drafts + existing field values
    const allAnswers: Record<string, unknown> = {};
    run.tabs.flatMap((t) => t.items).forEach((item) => {
      allAnswers[item.question_id] = item.question_id in drafts ? drafts[item.question_id] : item.value;
    });
    // Include volume_folio so title parsing works
    if (run.matter.volume_folio) allAnswers["volume_folio"] = run.matter.volume_folio;
    try {
      const result = await pushS32ToSmokeball(pushMatterNumber.trim(), allAnswers);
      setPushResult({
        success: true,
        message: `Pushed ${result.fields_pushed} field${result.fields_pushed !== 1 ? "s" : ""} via ${result.method?.replace(/_/g, " ")}`,
        warning: result.warning ?? undefined,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setPushResult({ success: false, message: msg });
    } finally {
      setIsPushing(false);
    }
  };
  const handleApplyPatchToReview = async (questionId: string, newValue: string, reason: string) => {
    const previous = questionId in drafts ? drafts[questionId] : fields[questionId]?.value ?? "";
    setDrafts((prev) => ({ ...prev, [questionId]: newValue }));
    if (!onApplyPatch) {
      return;
    }
    try {
      await onApplyPatch(questionId, newValue, reason);
    } catch (error) {
      setDrafts((prev) => ({ ...prev, [questionId]: previous }));
      throw error;
    }
  };

  const planningZoneOptions = fields["sec32_3.4_planning_zone"]?.options ?? [];

  const renderGeneric = () => (
    <div className="grid gap-5">
      {currentItems.length === 0 ? Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex flex-col gap-2">
          <Skeleton className={`h-3 ${i % 3 === 2 ? "w-2/5" : "w-[70%]"} bg-secondary`} />
          <div className="h-10 bg-card border border-border rounded-md" />
        </div>
      )) : currentItems.map((item) => (
        <div key={item.question_id} className="space-y-2 rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="text-sm font-semibold text-foreground"><FieldLabel text={item.label} item={item} /></div>
          {item.expected_type === "bool" ? (
            <Checkbox id={item.question_id} checked={boolValue(item.question_id)} onCheckedChange={(checked) => setDraft(item.question_id, Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
          ) : (
            <Textarea id={item.question_id} value={textValue(item.question_id)} onChange={(e) => setDraft(item.question_id, e.target.value)} className="min-h-[96px] bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
          )}
          {item.review_reasons[0] ? <p className="text-xs text-amber-700">{item.review_reasons[0]}</p> : null}
        </div>
      ))}
    </div>
  );

  return (
    <div className="flex h-screen bg-background font-sans text-foreground overflow-hidden">
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header
          onBack={onBack}
          userInitials={userInitials}
          onProfile={onProfile}
          onSettings={onSettings}
          onPolicy={onPolicy}
          onLogout={onLogout}
          showChatToggle
          isChatOpen={chatOpen}
          onChatToggle={() => setChatOpen((value) => !value)}
        />

      <section className="bg-card border-b border-border px-6 py-5 shrink-0 z-40 grid grid-cols-3 gap-8">
        <CopyOnlyField label="Client Name" value={clientName} />
        <CopyOnlyField label="Volume / Folio Number" value={volumeFolio} />
        <CopyOnlyField label="Property Address" value={propertyAddress} />
      </section>

      {(Object.keys(drafts).length > 0 || updateStatus?.update_available) && (
        <section className="bg-muted/50 border-b border-border px-6 py-3 shrink-0 flex flex-wrap items-center gap-3">
        {Object.keys(drafts).length > 0 ? <span className="rounded-full bg-blue-50 border border-blue-200 px-3 py-1 text-xs font-semibold text-blue-700">Unsaved changes {Object.keys(drafts).length}</span> : null}
        {updateStatus?.update_available ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
            Update ready {updateStatus.latest_version}
          </span>
        ) : null}
      </section>
      )}


      <div className="flex flex-1 overflow-hidden relative">
      {/* Re-analysis skeleton overlay */}
      {isReanalysing ? (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-background/90 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-base font-bold text-foreground">Re-analysing matter…</p>
            <p className="text-sm text-muted-foreground">Extracting answers from all documents</p>
          </div>
          <div className="w-64 space-y-3">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <div className={`h-3 rounded bg-muted animate-pulse`} style={{ width: `${55 + i * 8}%` }} />
                <div className="h-8 rounded-md border border-border bg-muted animate-pulse" />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <main className="flex-grow p-6 flex flex-col gap-5 overflow-hidden min-w-0">
        {errorMessage ? (
          <div className="flex items-start justify-between gap-4 rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <div className="flex items-start gap-2"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{errorMessage}</span></div>
            {onDismissError ? <button onClick={onDismissError} className="text-xs font-semibold uppercase tracking-wider text-destructive">Dismiss</button> : null}
          </div>
        ) : null}

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col gap-5 overflow-hidden">
          <TabsList className="bg-transparent p-0 h-auto gap-2 border-b border-border rounded-none pb-3 shrink-0 w-full justify-center">
            {pages.map((page) => <TabsTrigger key={page.id} value={page.id} className="rounded-md px-4 py-2 text-[0.85rem] font-semibold text-muted-foreground data-[state=active]:bg-primary data-[state=active]:text-white transition-all shadow-none">{page.label}</TabsTrigger>)}
          </TabsList>

          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
            <div className="flex flex-col gap-6 pt-2 pb-8">
                {activeTab === "page-1" ? (
                  <div className="space-y-6">
                    <div className="space-y-4">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">1. Financial Matters</h3>
                      <div className="grid gap-4 ml-1">
                        <div className="flex flex-wrap items-center gap-x-12 gap-y-4">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox id="policy_1_certs_attached" checked={boolValue("policy_1_certs_attached")} onCheckedChange={(checked) => setDraft("policy_1_certs_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Are contained in attached certificate(s)" item={fields["policy_1_certs_attached"]} /></Label>
                          </div>
                          <div className="flex items-center space-x-3 group">
                            <Checkbox id="policy_1_total_does_not_exceed" checked={boolValue("policy_1_total_does_not_exceed")} onCheckedChange={(checked) => setDraft("policy_1_total_does_not_exceed", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <div className="flex items-center gap-2">
                              <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Their total does not exceed" item={fields["policy_1_total_does_not_exceed"]} /></Label>
                              <Input id="policy_1_total_does_not_exceed_amount" value={textValue("policy_1_total_does_not_exceed_amount")} onChange={(e) => setDraft("policy_1_total_does_not_exceed_amount", e.target.value)} placeholder="$0.00" className="h-8 w-32 bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                          </div>
                        </div>

                        <div className="space-y-2.5">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox id="policy_1_amounts_are_checked" checked={boolValue("policy_1_amounts_are_checked")} onCheckedChange={(checked) => setDraft("policy_1_amounts_are_checked", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Their amounts are:" item={fields["policy_1_amounts_are_checked"]} /></Label>
                          </div>

                          <div className="ml-7 overflow-hidden rounded-lg border border-border bg-card shadow-sm">
                            {dbPillToast && (
                            <div className={`px-3 py-2 text-xs font-medium ${dbPillToast.error ? "bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300" : "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"}`}>
                                {dbPillToast.message}
                              </div>
                            )}
                            <Table>
                              <TableHeader className="bg-muted/50">
                                <TableRow className="hover:bg-transparent border-border">
                                  <TableHead className="w-[40%] font-bold text-foreground text-xs uppercase tracking-wider">Authority</TableHead>
                                  <TableHead className="font-bold text-foreground text-xs uppercase tracking-wider">Amount</TableHead>
                                  <TableHead className="font-bold text-foreground text-xs uppercase tracking-wider">Interest</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {[1, 2, 3, 4].map((row) => {
                                  const authorityId = `sec32_1.1_outgoing_${row}_authority`;
                                  const amountId = `sec32_1.1_outgoing_${row}_amount`;
                                  const authorityValue = getDraft(authorityId);
                                  const amountValue = getDraft(amountId);
                                  const showAuthorityStatus = hasDisplayValue(authorityValue);
                                  const showAmountStatus = hasDisplayValue(amountValue);
                                  const isDbLoading = dbPillLoading[row] ?? false;
                                  return (
                                    <TableRow key={row} className="border-border hover:bg-accent/30 transition-colors">
                                      <TableCell className="p-2 align-top">
                                        <div className="space-y-2 min-w-[220px]">
                                          <div className="flex items-center justify-between gap-2">
                                            {showAuthorityStatus ? <StatusPill item={fields[authorityId]} /> : <span />}
                                          </div>
                                          <Input id={authorityId} value={textValue(authorityId)} onChange={(e) => setDraft(authorityId, e.target.value)} placeholder="Enter authority..." className="h-9 border-transparent bg-transparent focus:bg-card focus:border-border shadow-none text-sm" />
                                        </div>
                                      </TableCell>
                                      <TableCell className="p-2 align-top">
                                        <div className="space-y-2 min-w-[140px]">
                                          <div className="flex items-center gap-1.5">
                                            {showAmountStatus ? <StatusPill item={fields[amountId]} /> : <span />}
                                            {showAuthorityStatus && (
                                              <button
                                                onClick={() => handleUseCopyRule(row)}
                                                disabled={isDbLoading}
                                                title="Apply copy rule price for this authority"
                                                className={[
                                                  "rounded-full px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed",
                                                  "bg-violet-100 text-violet-700 hover:bg-violet-200 dark:bg-violet-500/20 dark:text-violet-300 dark:hover:bg-violet-500/30",
                                                  copyGlowRow === row ? "ring-2 ring-violet-400/70 shadow-[0_0_18px_rgba(139,92,246,0.5)]" : "",
                                                ].join(" ")}
                                              >
                                                {isDbLoading ? "..." : "COPY"}
                                              </button>
                                            )}
                                          </div>
                                          <Input id={amountId} value={textValue(amountId)} onChange={(e) => setDraft(amountId, e.target.value)} placeholder="$0.00" className="h-9 border-transparent bg-transparent focus:bg-card focus:border-border shadow-none text-sm" />
                                        </div>
                                      </TableCell>
                                      <TableCell className="p-2"><Input value="0.00" disabled className="h-9 border-transparent bg-muted text-muted-foreground shadow-none text-sm" /></TableCell>
                                    </TableRow>
                                  );
                                })}
                              </TableBody>
                            </Table>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : activeTab === "page-2" ? (
                  <div className="space-y-8">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">3. Land Use</h3>
                      <div className="space-y-4 ml-1">
                        <p className="text-[0.9rem] font-semibold text-foreground">Description of any easement, covenant or other similar restriction:</p>
                        <div className="grid gap-4">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox id="policy_2_title_in_attached" checked={boolValue("policy_2_title_in_attached")} onCheckedChange={(checked) => setDraft("policy_2_title_in_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Is in the attached copies of title documents" item={fields["policy_2_title_in_attached"]} /></Label>
                          </div>
                          <div className="space-y-3">
                            <div className="flex items-center space-x-3 group">
                              <Checkbox id="policy_2_failure_checked" checked={boolValue("policy_2_failure_checked")} onCheckedChange={(checked) => setDraft("policy_2_failure_checked", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                              <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Particulars of any existing failure to comply with easement, covenant or other similar restriction are:" item={fields["policy_2_failure_checked"]} /></Label>
                            </div>
                            <Textarea id="policy_2_failure_text" value={textValue("policy_2_failure_text")} onChange={(e) => setDraft("policy_2_failure_text", e.target.value)} className="ml-7 min-h-[100px] max-w-2xl bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-foreground">3.2 Road Access</h4>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox id="sec32_3.2_no_road_access" checked={boolValue("sec32_3.2_no_road_access")} onCheckedChange={(checked) => setDraft("sec32_3.2_no_road_access", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="No road access" item={fields["sec32_3.2_no_road_access"]} /></Label>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-foreground">3.3 Designated Bushfire Prone Area</h4>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox id="sec32_3.3_bushfire_prone" checked={boolValue("sec32_3.3_bushfire_prone")} onCheckedChange={(checked) => setDraft("sec32_3.3_bushfire_prone", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Land is in Designated Bushfire Prone Area" item={fields["sec32_3.3_bushfire_prone"]} /></Label>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-foreground">3.4 Planning Scheme</h4>
                        <div className="grid gap-4 max-w-3xl">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox id="policy_2_planning_cert_attached" checked={boolValue("policy_2_planning_cert_attached")} onCheckedChange={(checked) => setDraft("policy_2_planning_cert_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Certificate with required information attached" item={fields["policy_2_planning_cert_attached"]} /></Label>
                          </div>
                          <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-muted-foreground font-semibold"><FieldLabel text="Name of planning scheme" item={fields["sec32_3.4_planning_scheme"]} /></Label>
                              <Input id="sec32_3.4_planning_scheme" value={textValue("sec32_3.4_planning_scheme")} onChange={(e) => setDraft("sec32_3.4_planning_scheme", e.target.value)} className="h-10 bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-muted-foreground font-semibold"><FieldLabel text="Name of responsible authority" item={fields["sec32_3.4_responsible_authority"]} /></Label>
                              <Input id="sec32_3.4_responsible_authority" value={textValue("sec32_3.4_responsible_authority")} onChange={(e) => setDraft("sec32_3.4_responsible_authority", e.target.value)} className="h-10 bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-muted-foreground font-semibold"><FieldLabel text="Planning zone" item={fields["sec32_3.4_planning_zone"]} /></Label>
                              <select id="sec32_3.4_planning_zone" value={textValue("sec32_3.4_planning_zone")} onChange={(e) => setDraft("sec32_3.4_planning_zone", e.target.value)} className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none focus:ring-1 focus:ring-primary">
                                <option value="">Select planning zone</option>
                                {planningZoneOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                              </select>
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-muted-foreground font-semibold"><FieldLabel text="Name of planning overlay" item={fields["sec32_3.4_planning_overlay_name"]} /></Label>
                              <Input id="sec32_3.4_planning_overlay_name" value={textValue("sec32_3.4_planning_overlay_name")} onChange={(e) => setDraft("sec32_3.4_planning_overlay_name", e.target.value)} className="h-10 bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : activeTab === "page-4" ? (
                  <div className="space-y-10">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">6. Owners Corporation</h3>
                      <div className="grid gap-4 ml-1">
                        <div className="flex items-center space-x-3 group">
                          <Checkbox id="policy_4_oc_cert_attached" checked={boolValue("policy_4_oc_cert_attached")} onCheckedChange={(checked) => setDraft("policy_4_oc_cert_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Attached is a current owners corporation certificate issued according to s151 of the Owners Corporations Act" item={fields["policy_4_oc_cert_attached"]} /></Label>
                        </div>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox id="sec32_oc_inactive" checked={boolValue("sec32_oc_inactive")} onCheckedChange={(checked) => setDraft("sec32_oc_inactive", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-muted-foreground group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Owners Corporation is inactive" item={fields["sec32_oc_inactive"]} /></Label>
                        </div>
                      </div>
                    </div>

                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">8. Services</h3>
                      <div className="space-y-4 ml-1">
                        <p className="text-[0.85rem] font-bold text-muted-foreground uppercase tracking-wider">Check the box if service is "NOT" connected</p>
                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                          {[
                            ["sec32_8_electricity_not_connected", "Electricity supply"],
                            ["sec32_8_gas_not_connected", "Gas supply"],
                            ["sec32_8_water_not_connected", "Water supply"],
                            ["sec32_8_sewerage_not_connected", "Sewerage"],
                            ["sec32_8_telephone_not_connected", "Telephone services"],
                          ].map(([qid, label]) => (
                            <div key={qid} className="flex items-center gap-3 p-3 rounded-lg border border-transparent hover:border-border hover:bg-accent/50 transition-all">
                              <Checkbox id={qid} checked={boolValue(qid)} onCheckedChange={(checked) => setDraft(qid, Boolean(checked))} className="border-border data-checked:bg-destructive data-checked:border-destructive" />
                              <Label className="text-[0.9rem] font-medium text-muted-foreground cursor-pointer"><FieldLabel text={label} item={fields[qid]} /></Label>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : activeTab === "page-6" ? (
                  <div className="space-y-10">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">12. Due Diligence Checklist</h3>
                      <div className="ml-1 max-w-md space-y-2">
                        <Label className="text-[0.8rem] uppercase tracking-wider text-muted-foreground font-semibold"><FieldLabel text="Checklist text" item={fields["policy_6_due_diligence"]} /></Label>
                        <Input id="policy_6_due_diligence" value={textValue("policy_6_due_diligence")} onChange={(e) => setDraft("policy_6_due_diligence", e.target.value)} className="h-9 bg-card border-border focus:ring-1 focus:ring-primary text-sm" />
                      </div>
                    </div>
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">13. Attachments</h3>
                      <div className="ml-1 max-w-3xl space-y-2">
                        <Textarea id="policy_6_attachments" value={textValue("policy_6_attachments")} onChange={(e) => setDraft("policy_6_attachments", e.target.value)} className="min-h-[300px] w-full bg-card border-border focus:ring-1 focus:ring-primary text-base p-4" />
                      </div>
                    </div>
                  </div>
                ) : renderGeneric()}
                {currentItems.filter((item) => item.needs_review).length > 0 ? (
                  <div className="rounded-2xl border border-orange-200 bg-orange-50/90 overflow-hidden dark:border-orange-400/35 dark:bg-orange-500/10">
                    <button
                      onClick={() => setReviewItemsCollapsed((c) => !c)}
                      className="w-full flex items-center justify-between px-5 py-4 hover:bg-orange-100/80 transition-colors dark:hover:bg-orange-500/15"
                    >
                      <div className="flex items-center gap-2">
                        <h4 className="text-sm font-bold uppercase tracking-wider text-orange-900 dark:text-orange-200">Review Items On This Tab</h4>
                        <span className="rounded-full bg-orange-200 px-2 py-0.5 text-xs font-bold text-orange-900 dark:bg-orange-400/25 dark:text-orange-200">{currentItems.filter((i) => i.needs_review).length}</span>
                      </div>
                      {reviewItemsCollapsed ? <ChevronRight className="w-4 h-4 text-orange-700 dark:text-orange-300" /> : <ChevronDown className="w-4 h-4 text-orange-700 dark:text-orange-300" />}
                    </button>
                    {!reviewItemsCollapsed && (
                      <div className="px-5 pb-5 grid gap-3">
                        {currentItems.filter((item) => item.needs_review).map((item) => (
                          <div key={item.question_id} className="rounded-xl bg-card px-4 py-3 shadow-sm">
                            <p className="text-sm font-semibold text-foreground">{item.label}</p>
                            <p className="mt-1 text-xs text-orange-700 dark:text-orange-300">{item.review_reasons.join(", ")}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
            </div>
          </div>
        </Tabs>

        <footer className="mt-auto flex flex-wrap items-center justify-end gap-3 border-t border-border px-4 py-4 shrink-0">
          <div className="flex flex-wrap justify-end gap-3">
            <Button
              variant="secondary"
              className="h-auto rounded-full border border-border bg-card px-5 py-2.5 font-semibold text-foreground shadow-sm transition-transform hover:scale-[1.01]"
              disabled={isSaving || isAutofilling}
              onClick={() => onSaveReview(buildUpdates())}
            >
              {isSaving ? "Saving..." : "Save review"}
            </Button>
            <div className="relative flex items-center h-11">
              {isAutofilling ? (
                <Button
                  className="h-full rounded-full px-6 py-2.5 font-bold shadow-md transition-transform hover:scale-[1.01] bg-rose-600 text-white hover:bg-rose-700"
                  disabled={isSaving}
                  onClick={() => void onCancelAutofill?.()}
                >
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Cancel autofill
                </Button>
              ) : (
                <>
                  <div className={[
                    "flex items-center h-full rounded-full transition-all overflow-hidden border",
                    primaryAction === "push" 
                      ? "border-emerald-500 bg-card"
                      : "border-foreground bg-foreground"
                  ].join(" ")}>
                    {primaryAction === "push" ? (
                      <button
                        onClick={() => { setPushResult(null); setPushDialogOpen(true); }}
                        disabled={isSaving || isAutofilling}
                        className="h-full px-6 text-sm font-bold text-emerald-600 transition-all hover:bg-emerald-50/50 active:scale-[0.98] disabled:opacity-50 flex items-center gap-2"
                      >
                        <Upload className="w-4 h-4" />
                        Push to Smokeball
                      </button>
                    ) : (
                      <button
                        onClick={() => void onAutofill(buildUpdates())}
                        disabled={isSaving || isAutofilling}
                        className="h-full px-6 text-sm font-bold text-background transition-all hover:bg-foreground/90 active:scale-[0.98] disabled:opacity-50 flex items-center gap-2"
                      >
                        Auto-fill Convey
                      </button>
                    )}
                    
                    <div className={[
                      "w-[1px] h-6 transition-colors",
                      primaryAction === "push" ? "bg-emerald-500" : "bg-white/20"
                    ].join(" ")} />

                    <button
                      onClick={() => setIsActionDropdownOpen(!isActionDropdownOpen)}
                      disabled={isSaving || isAutofilling}
                      className={[
                        "h-full px-3 transition-all active:scale-[0.98] disabled:opacity-50",
                        primaryAction === "push" 
                          ? "bg-card text-emerald-600 hover:bg-emerald-50/50" 
                          : "bg-foreground text-background hover:bg-foreground/90"
                      ].join(" ")}
                    >
                      <ChevronDown className={`w-4 h-4 transition-transform duration-200 ${isActionDropdownOpen ? "rotate-180" : ""}`} />
                    </button>
                  </div>

                  {isActionDropdownOpen && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={() => setIsActionDropdownOpen(false)} />
                      <div className="absolute bottom-full right-0 mb-3 w-56 rounded-2xl border border-border bg-card p-1.5 shadow-2xl z-50 animate-in fade-in slide-in-from-bottom-2">
                        {primaryAction === "autofill" ? (
                          <button
                            onClick={() => {
                              setPrimaryAction("push");
                              setIsActionDropdownOpen(false);
                            }}
                            className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-sm font-bold text-foreground hover:bg-accent transition-colors"
                          >
                            <Upload className="w-4 h-4 text-emerald-500" />
                            Push to Smokeball
                          </button>
                        ) : (
                          <button
                            onClick={() => {
                              setPrimaryAction("autofill");
                              setIsActionDropdownOpen(false);
                            }}
                            className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-sm font-bold text-foreground hover:bg-accent transition-colors"
                          >
                            Auto-fill Convey
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        </footer>

        {/* ── Push to Smokeball dialog ─────────────────────────────────── */}
        {pushDialogOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
            <div className="relative w-full max-w-md rounded-2xl bg-card border border-border shadow-2xl p-6 mx-4">
              <button
                className="absolute top-4 right-4 text-muted-foreground hover:text-foreground"
                onClick={() => setPushDialogOpen(false)}
              >
                <X className="w-5 h-5" />
              </button>

              <h2 className="text-lg font-bold text-foreground mb-1">Push S32 fields to Smokeball</h2>
              <p className="text-sm text-muted-foreground mb-5">
                Writes extracted Section 32 data directly into the open TriConvey matter.
                triConvey.exe must be running and logged in.
              </p>

              <div className="space-y-4">
                <div>
                  <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1.5 block">
                    Smokeball matter number
                  </Label>
                  <Input
                    value={pushMatterNumber}
                    onChange={(e) => setPushMatterNumber(e.target.value)}
                    placeholder="e.g. 2026-04/2329"
                    className="h-10 border-border focus:ring-1 focus:ring-emerald-500 text-sm"
                    disabled={isPushing}
                    onKeyDown={(e) => { if (e.key === "Enter" && !isPushing) void handlePushToSmokeball(); }}
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Find this in Smokeball under Matters → Matter Number column.
                  </p>
                </div>

                {pushResult && (
                  <div className={[
                    "rounded-xl border px-4 py-3 text-sm",
                    pushResult.success
                      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                      : "border-rose-200 bg-rose-50 text-rose-800",
                  ].join(" ")}>
                    <div className="font-semibold flex items-center gap-2">
                      {pushResult.success ? <Check className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                      {pushResult.success ? "Success" : "Failed"}
                    </div>
                    <div className="mt-1">{pushResult.message}</div>
                    {pushResult.warning && (
                      <div className="mt-2 text-amber-700 text-xs border-t border-amber-200 pt-2">
                        ⚠ {pushResult.warning}
                      </div>
                    )}
                  </div>
                )}

                <div className="flex gap-3 pt-1">
                  <Button
                    variant="secondary"
                    className="flex-1 rounded-full"
                    onClick={() => setPushDialogOpen(false)}
                    disabled={isPushing}
                  >
                    {pushResult?.success ? "Done" : "Cancel"}
                  </Button>
                  <Button
                    className="flex-1 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white font-semibold"
                    onClick={() => void handlePushToSmokeball()}
                    disabled={isPushing || !pushMatterNumber.trim()}
                  >
                    {isPushing ? "Pushing..." : "Push fields"}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
      <Chatbot
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        onAsk={onAskAssistant}
        onApplyPatch={handleApplyPatchToReview}
        initialConflicts={run.agent_context?.unresolved_conflicts ?? []}
        initialTurns={run.chat_history?.turns ?? []}
        runId={run.manifest.run_id}
        userName={user?.name}
        suppressMotion={isAutofilling}
        chatContext="matter"
        onReviewAgain={onReviewAgain}
        onRunReprocessed={onRunReprocessed}
        onResolveTriconveyReference={onResolveTriconveyReference}
      />
      </div>
      </div>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
      `}</style>
    </div>
  );
}
