import React, { useEffect, useState } from "react";
import { AlertCircle, Check, ChevronDown, ChevronLeft, ChevronRight, Copy, Info, LogOut, MessageSquare, Settings, Shield, User } from "lucide-react";
import { useAuth } from "../lib/AuthContext";
import { motion, AnimatePresence } from "motion/react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AnswerUpdatePayload, ChatAnswerPayload, ReviewFieldItem, ReviewRunPayload, UpdateStatusPayload } from "../lib/api";

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
        <button onClick={handleCopy} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-primary">
          {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
        </button>
      </label>
      <div className="text-[0.95rem] font-medium text-slate-700 select-all cursor-default leading-snug">
        {value || <span className="text-slate-400">—</span>}
      </div>
    </div>
  );
}

function StatusPill({ item }: { item?: ReviewFieldItem }) {
  if (!item) {
    return <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-slate-500">Unmapped</span>;
  }
  if (item.needs_review) {
    return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-amber-700">Review</span>;
  }
  if (item.confidence >= 0.9) {
    return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-emerald-700">Auto</span>;
  }
  return <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-[0.68rem] font-semibold uppercase tracking-wider text-yellow-800">Auto</span>;
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
  onAskAssistant: (question: string, history: HistoryTurn[], mode: ChatMode, signal?: AbortSignal) => Promise<ChatAnswerPayload>;
  onApplyPatch?: (questionId: string, newValue: string, reason: string) => Promise<void>;
  isSaving: boolean;
  isAutofilling: boolean;
  errorMessage?: string;
  onDismissError?: () => void;
  updateStatus?: UpdateStatusPayload | null;
}

export function ReviewScreen(props: ReviewScreenProps) {
  const { run, onBack, onProfile, onSettings, onAbout, onPolicy, onLogout, onSaveReview, onAutofill, onAskAssistant, onApplyPatch, isSaving, isAutofilling, errorMessage, onDismissError, updateStatus } = props;
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
  const [chatOpen, setChatOpen] = useState(false);

  const runIdRef = React.useRef(run.manifest?.run_id);
  useEffect(() => {
    const newRunId = run.manifest?.run_id;
    const isNewRun = newRunId !== runIdRef.current;
    runIdRef.current = newRunId;
    setDrafts({});
    setReviewItemsCollapsed(true);
    // Open the AI side agent automatically for new runs.
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
          <div className="h-10 bg-white border border-border rounded-md" />
        </div>
      )) : currentItems.map((item) => (
        <div key={item.question_id} className="space-y-2 rounded-xl border border-border bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-700"><FieldLabel text={item.label} item={item} /></div>
          {item.expected_type === "bool" ? (
            <Checkbox checked={boolValue(item.question_id)} onCheckedChange={(checked) => setDraft(item.question_id, Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
          ) : (
            <Textarea value={textValue(item.question_id)} onChange={(e) => setDraft(item.question_id, e.target.value)} className="min-h-[96px] bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
          )}
          {item.review_reasons[0] ? <p className="text-xs text-amber-700">{item.review_reasons[0]}</p> : null}
        </div>
      ))}
    </div>
  );

  return (
    <div className="flex h-screen bg-background font-sans text-foreground overflow-hidden">
      <div className="flex flex-col flex-1 overflow-hidden">
      <header className="h-16 border-b bg-white flex items-center justify-between px-6 shrink-0 z-50">
        <div className="w-10 flex items-center">
          <div onClick={onBack} className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center cursor-pointer hover:bg-slate-200 transition-colors">
            <ChevronLeft className="w-4 h-4 text-foreground stroke-[2.5]" />
          </div>
        </div>
        <h1 className="text-2xl font-serif italic tracking-tight text-foreground">Convey Agent</h1>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setChatOpen((value) => !value)}
            className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${chatOpen ? "bg-primary text-white shadow-lg shadow-primary/30" : "bg-slate-50 text-slate-400 hover:bg-slate-100"}`}
            title="Toggle document assistant"
          >
            <MessageSquare className="w-5 h-5" />
          </button>
          <div className="w-px h-6 bg-slate-200" />
          <DropdownMenu>
            <DropdownMenuTrigger nativeButton={false} render={<div className="w-10 h-10 rounded-full bg-slate-900 text-white flex items-center justify-center text-[0.8rem] font-semibold cursor-pointer hover:opacity-90 transition-opacity ring-2 ring-white shadow-md">{userInitials}</div>} />
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuGroup><DropdownMenuLabel>My Account</DropdownMenuLabel></DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="cursor-pointer" onClick={onProfile}><User className="mr-2 h-4 w-4" /><span>Profile</span></DropdownMenuItem>
              <DropdownMenuItem className="cursor-pointer" onClick={onSettings}><Settings className="mr-2 h-4 w-4" /><span>Settings</span></DropdownMenuItem>
              <DropdownMenuItem className="cursor-pointer" onClick={onPolicy}><Shield className="mr-2 h-4 w-4" /><span>Custom Policy</span></DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="cursor-pointer" onClick={onAbout}><Info className="mr-2 h-4 w-4" /><span>About & Updates</span></DropdownMenuItem>
              <DropdownMenuItem className="cursor-pointer text-destructive focus:text-destructive" onClick={onLogout}><LogOut className="mr-2 h-4 w-4" /><span>Logout</span></DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      <section className="bg-white border-b px-6 py-5 shrink-0 z-40 grid grid-cols-3 gap-8">
        <CopyOnlyField label="Client Name" value={clientName} />
        <CopyOnlyField label="Volume / Folio Number" value={volumeFolio} />
        <CopyOnlyField label="Property Address" value={propertyAddress} />
      </section>

      <section className="bg-slate-50/80 border-b px-6 py-3 shrink-0 flex flex-wrap items-center gap-3">
        <span className="rounded-full bg-white border border-border px-3 py-1 text-xs font-semibold text-slate-600">Run {run.manifest.run_id}</span>
        <span className="rounded-full bg-emerald-50 border border-emerald-200 px-3 py-1 text-xs font-semibold text-emerald-700">Auto ready {run.metrics.auto_ready}</span>
        <span className="rounded-full bg-amber-50 border border-amber-200 px-3 py-1 text-xs font-semibold text-amber-700">Needs review {run.metrics.needs_review}</span>
        <span className="rounded-full bg-white border border-border px-3 py-1 text-xs font-semibold text-slate-600">Actions {run.metrics.action_count}</span>
        {Object.keys(drafts).length > 0 ? <span className="rounded-full bg-blue-50 border border-blue-200 px-3 py-1 text-xs font-semibold text-blue-700">Unsaved changes {Object.keys(drafts).length}</span> : null}
        {updateStatus?.update_available ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
            Update ready {updateStatus.latest_version}
          </span>
        ) : null}
      </section>

      <div className="flex flex-1 overflow-hidden">
      <main className="flex-grow p-6 flex flex-col gap-5 overflow-hidden min-w-0">
        {errorMessage ? (
          <div className="flex items-start justify-between gap-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <div className="flex items-start gap-2"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{errorMessage}</span></div>
            {onDismissError ? <button onClick={onDismissError} className="text-xs font-semibold uppercase tracking-wider text-rose-700">Dismiss</button> : null}
          </div>
        ) : null}

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col gap-5 overflow-hidden">
          <TabsList className="bg-transparent p-0 h-auto gap-2 border-b border-border rounded-none pb-3 shrink-0 w-full justify-center">
            {pages.map((page) => <TabsTrigger key={page.id} value={page.id} className="rounded-md px-4 py-2 text-[0.85rem] font-semibold text-muted-foreground data-[state=active]:bg-primary data-[state=active]:text-white transition-all shadow-none">{page.label}</TabsTrigger>)}
          </TabsList>

          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
            <AnimatePresence mode="wait">
              <motion.div key={activeTab} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }} className="flex flex-col gap-6 pt-2 pb-8">
                {activeTab === "page-1" ? (
                  <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="space-y-4">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">1. Financial Matters</h3>
                      <div className="grid gap-4 ml-1">
                        <div className="flex flex-wrap items-center gap-x-12 gap-y-4">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox checked={boolValue("policy_1_certs_attached")} onCheckedChange={(checked) => setDraft("policy_1_certs_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Are contained in attached certificate(s)" item={fields["policy_1_certs_attached"]} /></Label>
                          </div>
                          <div className="flex items-center space-x-3 group">
                            <Checkbox checked={boolValue("policy_1_total_does_not_exceed")} onCheckedChange={(checked) => setDraft("policy_1_total_does_not_exceed", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <div className="flex items-center gap-2">
                              <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Their total does not exceed" item={fields["policy_1_total_does_not_exceed"]} /></Label>
                              <Input value={textValue("policy_1_total_does_not_exceed_amount")} onChange={(e) => setDraft("policy_1_total_does_not_exceed_amount", e.target.value)} placeholder="$0.00" className="h-8 w-32 bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                          </div>
                        </div>

                        <div className="space-y-2.5">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox checked={boolValue("policy_1_amounts_are_checked")} onCheckedChange={(checked) => setDraft("policy_1_amounts_are_checked", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Their amounts are:" item={fields["policy_1_amounts_are_checked"]} /></Label>
                          </div>

                          <div className="ml-7 overflow-hidden rounded-lg border border-border bg-white shadow-sm">
                            <Table>
                              <TableHeader className="bg-slate-50/50">
                                <TableRow className="hover:bg-transparent border-border">
                                  <TableHead className="w-[40%] font-bold text-slate-900 text-xs uppercase tracking-wider">Authority</TableHead>
                                  <TableHead className="font-bold text-slate-900 text-xs uppercase tracking-wider">Amount</TableHead>
                                  <TableHead className="font-bold text-slate-900 text-xs uppercase tracking-wider">Interest</TableHead>
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
                                  return (
                                    <TableRow key={row} className="border-border hover:bg-slate-50/30 transition-colors">
                                      <TableCell className="p-2 align-top">
                                        <div className="space-y-2 min-w-[220px]">
                                          <div className="flex items-center justify-between gap-2">
                                            {showAuthorityStatus ? <StatusPill item={fields[authorityId]} /> : <span />}
                                          </div>
                                          <Input value={textValue(authorityId)} onChange={(e) => setDraft(authorityId, e.target.value)} placeholder="Enter authority..." className="h-9 border-transparent bg-transparent focus:bg-white focus:border-border shadow-none text-sm" />
                                        </div>
                                      </TableCell>
                                      <TableCell className="p-2 align-top">
                                        <div className="space-y-2 min-w-[140px]">
                                          <div className="flex items-center justify-between gap-2">
                                            {showAmountStatus ? <StatusPill item={fields[amountId]} /> : <span />}
                                          </div>
                                          <Input value={textValue(amountId)} onChange={(e) => setDraft(amountId, e.target.value)} placeholder="$0.00" className="h-9 border-transparent bg-transparent focus:bg-white focus:border-border shadow-none text-sm" />
                                        </div>
                                      </TableCell>
                                      <TableCell className="p-2"><Input value="N/A" disabled className="h-9 border-transparent bg-slate-50 text-slate-400 shadow-none text-sm" /></TableCell>
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
                  <div className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">3. Land Use</h3>
                      <div className="space-y-4 ml-1">
                        <p className="text-[0.9rem] font-semibold text-slate-700">Description of any easement, covenant or other similar restriction:</p>
                        <div className="grid gap-4">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox checked={boolValue("policy_2_title_in_attached")} onCheckedChange={(checked) => setDraft("policy_2_title_in_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Is in the attached copies of title documents" item={fields["policy_2_title_in_attached"]} /></Label>
                          </div>
                          <div className="space-y-3">
                            <div className="flex items-center space-x-3 group">
                              <Checkbox checked={boolValue("policy_2_failure_checked")} onCheckedChange={(checked) => setDraft("policy_2_failure_checked", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                              <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Particulars of any existing failure to comply with easement, covenant or other similar restriction are:" item={fields["policy_2_failure_checked"]} /></Label>
                            </div>
                            <Textarea value={textValue("policy_2_failure_text")} onChange={(e) => setDraft("policy_2_failure_text", e.target.value)} className="ml-7 min-h-[100px] max-w-2xl bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-slate-800">3.2 Road Access</h4>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox checked={boolValue("sec32_3.2_no_road_access")} onCheckedChange={(checked) => setDraft("sec32_3.2_no_road_access", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="No road access" item={fields["sec32_3.2_no_road_access"]} /></Label>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-slate-800">3.3 Designated Bushfire Prone Area</h4>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox checked={boolValue("sec32_3.3_bushfire_prone")} onCheckedChange={(checked) => setDraft("sec32_3.3_bushfire_prone", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Land is in Designated Bushfire Prone Area" item={fields["sec32_3.3_bushfire_prone"]} /></Label>
                        </div>
                      </div>

                      <div className="space-y-4 ml-1 pt-2">
                        <h4 className="text-[0.95rem] font-bold text-slate-800">3.4 Planning Scheme</h4>
                        <div className="grid gap-4 max-w-3xl">
                          <div className="flex items-center space-x-3 group">
                            <Checkbox checked={boolValue("policy_2_planning_cert_attached")} onCheckedChange={(checked) => setDraft("policy_2_planning_cert_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                            <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Certificate with required information attached" item={fields["policy_2_planning_cert_attached"]} /></Label>
                          </div>
                          <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-slate-500 font-semibold"><FieldLabel text="Name of planning scheme" item={fields["sec32_3.4_planning_scheme"]} /></Label>
                              <Input value={textValue("sec32_3.4_planning_scheme")} onChange={(e) => setDraft("sec32_3.4_planning_scheme", e.target.value)} className="h-10 bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-slate-500 font-semibold"><FieldLabel text="Name of responsible authority" item={fields["sec32_3.4_responsible_authority"]} /></Label>
                              <Input value={textValue("sec32_3.4_responsible_authority")} onChange={(e) => setDraft("sec32_3.4_responsible_authority", e.target.value)} className="h-10 bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-slate-500 font-semibold"><FieldLabel text="Planning zone" item={fields["sec32_3.4_planning_zone"]} /></Label>
                              <select value={textValue("sec32_3.4_planning_zone")} onChange={(e) => setDraft("sec32_3.4_planning_zone", e.target.value)} className="h-10 w-full rounded-md border border-border bg-white px-3 text-sm text-slate-700 outline-none focus:ring-1 focus:ring-primary">
                                <option value="">Select planning zone</option>
                                {planningZoneOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                              </select>
                            </div>
                            <div className="space-y-2">
                              <Label className="text-[0.8rem] uppercase tracking-wider text-slate-500 font-semibold"><FieldLabel text="Name of planning overlay" item={fields["sec32_3.4_planning_overlay_name"]} /></Label>
                              <Input value={textValue("sec32_3.4_planning_overlay_name")} onChange={(e) => setDraft("sec32_3.4_planning_overlay_name", e.target.value)} className="h-10 bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : activeTab === "page-4" ? (
                  <div className="space-y-10 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">6. Owners Corporation</h3>
                      <div className="grid gap-4 ml-1">
                        <div className="flex items-center space-x-3 group">
                          <Checkbox checked={boolValue("policy_4_oc_cert_attached")} onCheckedChange={(checked) => setDraft("policy_4_oc_cert_attached", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Attached is a current owners corporation certificate issued according to s151 of the Owners Corporations Act" item={fields["policy_4_oc_cert_attached"]} /></Label>
                        </div>
                        <div className="flex items-center space-x-3 group">
                          <Checkbox checked={boolValue("sec32_oc_inactive")} onCheckedChange={(checked) => setDraft("sec32_oc_inactive", Boolean(checked))} className="border-border data-checked:bg-primary data-checked:border-primary" />
                          <Label className="text-[0.9rem] font-medium text-slate-600 group-hover:text-foreground transition-colors cursor-pointer"><FieldLabel text="Owners Corporation is inactive" item={fields["sec32_oc_inactive"]} /></Label>
                        </div>
                      </div>
                    </div>

                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">8. Services</h3>
                      <div className="space-y-4 ml-1">
                        <p className="text-[0.85rem] font-bold text-slate-500 uppercase tracking-wider">Check the box if service is "NOT" connected</p>
                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                          {[
                            ["sec32_8_electricity_not_connected", "Electricity supply"],
                            ["sec32_8_gas_not_connected", "Gas supply"],
                            ["sec32_8_water_not_connected", "Water supply"],
                            ["sec32_8_sewerage_not_connected", "Sewerage"],
                            ["sec32_8_telephone_not_connected", "Telephone services"],
                          ].map(([qid, label]) => (
                            <div key={qid} className="flex items-center gap-3 p-3 rounded-lg border border-transparent hover:border-border hover:bg-slate-50/50 transition-all">
                              <Checkbox checked={boolValue(qid)} onCheckedChange={(checked) => setDraft(qid, Boolean(checked))} className="border-border data-checked:bg-destructive data-checked:border-destructive" />
                              <Label className="text-[0.9rem] font-medium text-slate-600 cursor-pointer"><FieldLabel text={label} item={fields[qid]} /></Label>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : activeTab === "page-6" ? (
                  <div className="space-y-10 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">12. Due Diligence Checklist</h3>
                      <div className="ml-1 max-w-md space-y-2">
                        <Label className="text-[0.8rem] uppercase tracking-wider text-slate-500 font-semibold"><FieldLabel text="Checklist text" item={fields["policy_6_due_diligence"]} /></Label>
                        <Input value={textValue("policy_6_due_diligence")} onChange={(e) => setDraft("policy_6_due_diligence", e.target.value)} className="h-9 bg-white border-border focus:ring-1 focus:ring-primary text-sm" />
                      </div>
                    </div>
                    <div className="space-y-6">
                      <h3 className="text-lg font-bold text-foreground border-l-4 border-primary pl-3">13. Attachments</h3>
                      <div className="ml-1 max-w-3xl space-y-2">
                        <Textarea value={textValue("policy_6_attachments")} onChange={(e) => setDraft("policy_6_attachments", e.target.value)} className="min-h-[300px] w-full bg-white border-border focus:ring-1 focus:ring-primary text-base p-4" />
                      </div>
                    </div>
                  </div>
                ) : renderGeneric()}
                {currentItems.filter((item) => item.needs_review).length > 0 ? (
                  <div className="rounded-2xl border border-amber-200 bg-amber-50/80 overflow-hidden">
                    <button
                      onClick={() => setReviewItemsCollapsed((c) => !c)}
                      className="w-full flex items-center justify-between px-5 py-4 hover:bg-amber-100/60 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <h4 className="text-sm font-bold uppercase tracking-wider text-amber-800">Review Items On This Tab</h4>
                        <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-bold text-amber-900">{currentItems.filter((i) => i.needs_review).length}</span>
                      </div>
                      {reviewItemsCollapsed ? <ChevronRight className="w-4 h-4 text-amber-700" /> : <ChevronDown className="w-4 h-4 text-amber-700" />}
                    </button>
                    {!reviewItemsCollapsed && (
                      <div className="px-5 pb-5 grid gap-3">
                        {currentItems.filter((item) => item.needs_review).map((item) => (
                          <div key={item.question_id} className="rounded-xl bg-white px-4 py-3 shadow-sm">
                            <p className="text-sm font-semibold text-slate-800">{item.label}</p>
                            <p className="mt-1 text-xs text-amber-700">{item.review_reasons.join(", ")}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
              </motion.div>
            </AnimatePresence>
          </div>
        </Tabs>

        <footer className="mt-auto flex justify-between items-center gap-4 pt-5 shrink-0">
          <div className="text-sm text-slate-500">
            {run.metrics.review_gate_required ? "Review gate is still active for some fields." : "Run is clear for direct autofill."}
          </div>
          <div className="flex justify-end gap-3">
            <Button variant="secondary" className="bg-secondary text-foreground font-semibold px-6 py-2.5 h-auto rounded-md border-none" disabled={isSaving || isAutofilling} onClick={() => onSaveReview(buildUpdates())}>
              {isSaving ? "Saving..." : "Save Review"}
            </Button>
            <Button className="bg-primary text-white font-semibold px-6 py-2.5 h-auto rounded-md border-none" disabled={isSaving || isAutofilling} onClick={() => onAutofill(buildUpdates())}>
              {isAutofilling ? "Starting..." : "Auto-fill Convey"}
            </Button>
          </div>
        </footer>
      </main>
      <Chatbot
        isOpen={chatOpen}
        onClose={() => setChatOpen(false)}
        onAsk={onAskAssistant}
        onApplyPatch={handleApplyPatchToReview}
        initialConflicts={run.agent_context?.unresolved_conflicts ?? []}
        initialTurns={run.chat_history?.turns ?? []}
        runId={run.manifest.run_id}
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
