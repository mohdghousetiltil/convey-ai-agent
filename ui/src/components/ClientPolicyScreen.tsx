import React from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  ChevronDown,
  ChevronRight,
  Sparkles,
  Star,
  Copy,
  Settings2,
  ArrowLeft,
  Search,
  Filter,
  Check,
  Zap,
  Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  createCopyRule,
  deleteCopyRule,
  listCopyRules,
  updateCopyRule,
  type CopyRulePayload,
} from "@/lib/api";
import { Header } from "./Header";

export interface SettingsForm {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  aiProvider: "openai" | "anthropic" | "hybrid" | "google" | "openrouter";
  aiMode?: "cost_efficient" | "all_time_best" | "turbo";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
  updateRepository?: string;
  includePrereleaseUpdates?: boolean;
  autoCheckForUpdates?: boolean;
  cloudSyncEnabled?: boolean;
}

interface ClientPolicyScreenProps {
  onBack: () => void;
  userInitials?: string;
  onProfile?: () => void;
  onSettings?: () => void;
  onAbout?: () => void;
  onLogout?: () => void;
  settings: SettingsForm;
  onSaveSettings: (settings: SettingsForm) => Promise<void> | void;
}

type PreferenceItem = {
  kind: "item";
  id: string;
  label: string;
  targets: string[];
};

type PreferenceHeading = {
  kind: "heading";
  id: string;
  label: string;
};

type PreferenceNode = PreferenceItem | PreferenceHeading;

type PreferenceSection = {
  id: string;
  title: string;
  nodes: PreferenceNode[];
};

type EditableCopyRule = {
  id: string;
  authority_name: string;
  annual_amount: string;
  notes: string;
  is_active: boolean;
  isNew?: boolean;
};

const ALL_OUTGOING_TARGETS = [
  "sec32_1.1_outgoing_1_authority",
  "sec32_1.1_outgoing_1_amount",
  "sec32_1.1_outgoing_2_authority",
  "sec32_1.1_outgoing_2_amount",
  "sec32_1.1_outgoing_3_authority",
  "sec32_1.1_outgoing_3_amount",
  "sec32_1.1_outgoing_4_authority",
  "sec32_1.1_outgoing_4_amount",
];

const SERVICES_TARGETS = [
  "sec32_8_electricity_not_connected",
  "sec32_8_gas_not_connected",
  "sec32_8_water_not_connected",
  "sec32_8_sewerage_not_connected",
  "sec32_8_telephone_not_connected",
];

const SECTIONS: PreferenceSection[] = [
  {
    id: "financials",
    title: "1. Financials",
    nodes: [
      { kind: "heading", id: "1.1", label: "1.1 Particulars of any Rates, Taxes, Charges or Other Similar Outgoings." },
      { kind: "item", id: "policy_1_certs_attached", label: "Are contained in attached certificate(s)", targets: ["policy_1_certs_attached"] },
      { kind: "item", id: "policy_1_total_does_not_exceed", label: "Their total does not exceed", targets: ["policy_1_total_does_not_exceed"] },
      { kind: "item", id: "policy_1_total_does_not_exceed_amount", label: "Total field", targets: ["policy_1_total_does_not_exceed_amount"] },
      { kind: "item", id: "policy_1_amounts_are_checked", label: "Their amounts are:", targets: ["policy_1_amounts_are_checked"] },
      { kind: "item", id: "outgoings_table", label: "Authority   Amount   Interest", targets: ALL_OUTGOING_TARGETS },
      { kind: "item", id: "financials_no_other_amounts", label: "There are NO amounts for which the purchaser may become liable other than:", targets: ["1.1 There are NO amounts for which the purchaser may become liable other than:"] },
      { kind: "heading", id: "1.2", label: "1.2 Particulars of any Charge imposed by or under any Act" },
      { kind: "item", id: "1.2_amount", label: "Amount", targets: ["1.2 Amount"] },
      { kind: "item", id: "1.2_to", label: "To", targets: ["1.2 To"] },
      { kind: "item", id: "1.2_other_particulars", label: "Other Particulars", targets: ["1.2 Other Particulars"] },
      { kind: "heading", id: "1.3", label: "1.3 Terms Contract" },
      { kind: "item", id: "1.3_terms_contract", label: "Terms Contract", targets: ["1.3 Terms Contract"] },
      { kind: "heading", id: "1.4", label: "1.4 Sale Subject to Mortgage" },
      { kind: "item", id: "sec32_1.4_sale_subject_to_mortgage", label: "Sale Subject to Mortgage", targets: ["sec32_1.4_sale_subject_to_mortgage", "1.4 Sale Subject to Mortgage"] },
      { kind: "heading", id: "1.5", label: "1.5 Commercial and Industrial Property Tax Reform Act 2024" },
      { kind: "item", id: "1.5_a", label: "(a) AVPCC allocated to the land", targets: ["1.5 (a) AVPCC allocated to the land"] },
      { kind: "item", id: "1.5_b", label: "(b) Land tax reform scheme land within the meaning of the CIPT Act", targets: ["1.5 (b) Land tax reform scheme land within the meaning of the CIPT Act"] },
      { kind: "item", id: "1.5_c", label: "(c) Entry Date for Tax Reform Scheme Land", targets: ["1.5 (c) Entry Date for Tax Reform Scheme Land"] },
    ],
  },
  {
    id: "damage",
    title: "2. Damage",
    nodes: [
      { kind: "heading", id: "2.1", label: "2.1 Damage and Destruction" },
      { kind: "item", id: "2.1_policy_attached", label: "Copy or extract of any policy attached", targets: ["2.1 Copy or extract of any policy attached"] },
      { kind: "item", id: "2.1_policy_particulars", label: "Particulars of any policy insurance as follows:", targets: ["2.1 Particulars of any policy insurance as follows:"] },
      { kind: "item", id: "sec32_2.1_insurance_company_name", label: "Company Name", targets: ["sec32_2.1_insurance_company_name", "2.1 Insurance Company Name"] },
      { kind: "item", id: "sec32_2.1_policy_type", label: "Type of Policy", targets: ["sec32_2.1_policy_type", "2.1 Type of Policy"] },
      { kind: "item", id: "sec32_2.1_expiry_date", label: "Expiry Date", targets: ["sec32_2.1_expiry_date", "2.1 Expiry Date"] },
      { kind: "item", id: "sec32_2.1_insurance_policy_no", label: "Policy No.", targets: ["sec32_2.1_insurance_policy_no", "2.1 Policy No."] },
      { kind: "item", id: "sec32_2.1_amount_insured", label: "Amount Insured", targets: ["sec32_2.1_amount_insured", "2.1 Amount Insured"] },
      { kind: "heading", id: "2.2", label: "2.2 Owner Builder" },
      { kind: "item", id: "2.2_owner_builder_attached", label: "Copy or extract of any policy attached", targets: ["2.2 Copy or extract of any policy attached"] },
      { kind: "item", id: "2.2_owner_builder_particulars", label: "Particulars of any required insurance as follows:", targets: ["2.2 Particulars of any required insurance as follows:"] },
      { kind: "item", id: "2.2_owner_builder_company", label: "Company Name", targets: ["2.2 Company Name"] },
      { kind: "item", id: "2.2_owner_builder_policy_no", label: "Policy No.", targets: ["2.2 Policy No."] },
      { kind: "item", id: "2.2_owner_builder_expiry", label: "Expiry Date", targets: ["2.2 Expiry Date"] },
    ],
  },
  {
    id: "land_use",
    title: "3. Land Use",
    nodes: [
      { kind: "heading", id: "3.1", label: "3.1 Easements, Covenants or Other Restrictions" },
      { kind: "item", id: "policy_2_title_in_attached", label: "Is in the attached copies of title documents", targets: ["policy_2_title_in_attached"] },
      { kind: "item", id: "3.1_as_follows", label: "Is as follows:", targets: ["3.1 Easements - Is as follows"] },
      { kind: "item", id: "policy_2_failure_checked", label: "Particulars of any existing failure to comply with easement, covenant or other similar restriction are:", targets: ["policy_2_failure_checked", "policy_2_failure_text"] },
      { kind: "heading", id: "3.2", label: "3.2 Road Access" },
      { kind: "item", id: "sec32_3.2_no_road_access", label: "No road access", targets: ["sec32_3.2_no_road_access"] },
      { kind: "heading", id: "3.3", label: "3.3 Designated Bushfire Prone Area" },
      { kind: "item", id: "sec32_3.3_bushfire_prone", label: "Land is in Designated Bushfire Prone Area", targets: ["sec32_3.3_bushfire_prone"] },
      { kind: "heading", id: "3.4", label: "3.4 Planning Scheme" },
      { kind: "item", id: "policy_2_planning_cert_attached", label: "Certificate with required information attached", targets: ["policy_2_planning_cert_attached"] },
      { kind: "item", id: "3.4_required_info", label: "Required information is as follows", targets: ["3.4 Required information is as follows"] },
      { kind: "item", id: "sec32_3.4_planning_scheme", label: "Name of planning scheme", targets: ["sec32_3.4_planning_scheme"] },
      { kind: "item", id: "sec32_3.4_responsible_authority", label: "Name of responsible authority", targets: ["sec32_3.4_responsible_authority"] },
      { kind: "item", id: "sec32_3.4_planning_zone", label: "Zoning of the land", targets: ["sec32_3.4_planning_zone"] },
      { kind: "item", id: "sec32_3.4_planning_overlay_name", label: "Name of planning overlay", targets: ["sec32_3.4_planning_overlay_name"] },
    ],
  },
  {
    id: "notices",
    title: "4. Notices",
    nodes: [
      { kind: "heading", id: "4.1", label: "4.1 Notice, Order, Declaration, Report or Recommendation" },
      { kind: "item", id: "4.1_attached", label: "Are contained in attached certificates/statements", targets: ["4.1 Are contained in attached certificates/statements"] },
      { kind: "item", id: "4.1_as_follows", label: "Are as follows:", targets: ["4.1 Are as follows:"] },
      { kind: "heading", id: "4.2", label: "4.2 Agricultural Chemicals" },
      { kind: "item", id: "4.2_none_other_than", label: "There are NO notices issued by public authority re agricultural chemicals other than:", targets: ["4.2 There are NO notices issued by public authority re agricultural chemicals other than:"] },
      { kind: "heading", id: "4.3", label: "4.3 Compulsory Acquisition" },
      { kind: "item", id: "4.3_particulars", label: "Particulars of any notices of intention to acquire (s6 of the Land Acquisition and Compensation Act) are as follows:", targets: ["4.3 Particulars of any notices of intention to acquire (s6 of the Land Acquisition and Compensation Act) are as follows:"] },
    ],
  },
  {
    id: "building_permits",
    title: "5. Building Permits",
    nodes: [
      { kind: "item", id: "5_attached", label: "Are contained in the attached certificate", targets: ["5. Building Permits - Are contained in the attached certificate"] },
      { kind: "item", id: "5_as_follows", label: "Are as follows:", targets: ["5. Building Permits - Are as follows"] },
      { kind: "item", id: "5_details", label: "Details field", targets: ["5. Building Permits - Details"] },
    ],
  },
  {
    id: "owners_corporation",
    title: "6. Owners Corporation",
    nodes: [
      { kind: "item", id: "policy_4_oc_cert_attached", label: "Attached is a current owners corporation certificate issued according to s151 of the Owners Corporations Act", targets: ["policy_4_oc_cert_attached"] },
      { kind: "item", id: "6_info_prescribed", label: "Attached is the information prescribed for the purposes of s151(4)(a) of the Owners Corporations Act", targets: ["6. Owners Corporation - Attached is the information prescribed for the purposes of s151(4)(a) of the Owners Corporations Act"] },
      { kind: "item", id: "sec32_oc_inactive", label: "Owners Corporation is inactive", targets: ["sec32_oc_inactive"] },
    ],
  },
  {
    id: "gaic",
    title: "7. Growth Areas Infrastructure Contribution (GAIC)",
    nodes: [
      { kind: "item", id: "sec32_7_gaic_applies", label: "GAIC applies", targets: ["sec32_7_gaic_applies"] },
      { kind: "heading", id: "7.1", label: "7.1 Work-in-Kind Agreement" },
      { kind: "item", id: "7.1_transferred", label: "Land IS to be transferred under the agreement", targets: ["7.1 Land IS to be transferred under the agreement"] },
      { kind: "item", id: "7.1_works", label: "Land IS land on which works are to be carried out under the agreement", targets: ["7.1 Land IS land on which works are to be carried out under the agreement"] },
      { kind: "item", id: "7.1_gaic_imposed", label: "Land IS land in respect of which a GAIC is imposed", targets: ["7.1 Land IS land in respect of which a GAIC is imposed"] },
      { kind: "heading", id: "7.2", label: "7.2 GAIC Recording" },
      { kind: "item", id: "7.2_release", label: "Any certificate of release from liability to pay a GAIC", targets: ["7.2 Any certificate of release from liability to pay a GAIC"] },
      { kind: "item", id: "7.2_deferral", label: "Any certificate of deferral of the liability to pay the whole or part of a GAIC", targets: ["7.2 Any certificate of deferral of the liability to pay the whole or part of a GAIC"] },
      { kind: "item", id: "7.2_exemption", label: "Any certificate of exemption from liability to pay a GAIC", targets: ["7.2 Any certificate of exemption from liability to pay a GAIC"] },
      { kind: "item", id: "7.2_staged", label: "Any certificate of staged payment approval", targets: ["7.2 Any certificate of staged payment approval"] },
      { kind: "item", id: "7.2_no_liability", label: "Any certificate of no GAIC liability", targets: ["7.2 Any certificate of no GAIC liability"] },
      { kind: "item", id: "7.2_reduction_notice", label: "Any notice providing evidence of the grant of a reduction of the liability for a GAIC or an exemption from that liability", targets: ["7.2 Any notice providing evidence of the grant of a reduction of the liability for a GAIC or an exemption from that liability"] },
      { kind: "item", id: "7.2_part9b", label: "Certificate issued under Part 9B of the Planning and Environment Act", targets: ["7.2 Certificate issued under Part 9B of the Planning and Environment Act"] },
    ],
  },
  {
    id: "services",
    title: "8. Services",
    nodes: [
      { kind: "item", id: "services_not_connected", label: "Check the box if service is NOT connected", targets: SERVICES_TARGETS },
    ],
  },
  {
    id: "title",
    title: "9. Title",
    nodes: [
      { kind: "item", id: "9_vendor_right_to_sell", label: "Evidence of the vendor's right or power to sell (where the vendor is not the registered proprietor) or the owner in fee simple", targets: ["9. Evidence of the vendor's right or power to sell (where the vendor is not the registered proprietor) or the owner in fee simple"] },
    ],
  },
  {
    id: "subdivision",
    title: "10. Subdivision",
    nodes: [
      { kind: "heading", id: "10.1", label: "10.1 Unregistered Subdivision" },
      { kind: "item", id: "10.1_certified_plan", label: "Attached is a copy of the plan of subdivision certified by relevant authority if the plan is not yet registered", targets: ["10.1 Attached is a copy of the plan of subdivision certified by relevant authority if the plan is not yet registered"] },
      { kind: "item", id: "10.1_latest_plan", label: "Attached is a copy of the latest version of the plan if the plan of subdivision has not yet been certified", targets: ["10.1 Attached is a copy of the latest version of the plan if the plan of subdivision has not yet been certified"] },
      { kind: "heading", id: "10.2", label: "10.2 Staged Subdivision" },
      { kind: "item", id: "10.2_first_stage", label: "Attached is a copy of the plan for the first stage if the land is in the second or subsequent stage", targets: ["10.2 Attached is a copy of the plan for the first stage if the land is in the second or subsequent stage"] },
      { kind: "item", id: "10.2_requirements", label: "Requirements in a statement of compliance re the stage in which the land is included that have not been complied with are as follows:", targets: ["10.2 Requirements in a statement of compliance re the stage in which the land is included that have not been complied with are as follows:"] },
      { kind: "item", id: "10.2_proposals", label: "Proposals re subsequent stages that are known to the vendor are as follows:", targets: ["10.2 Proposals re subsequent stages that are known to the vendor are as follows:"] },
      { kind: "item", id: "10.2_contents", label: "Contents of any permit under the Planning and Environment Act authorising the staged subdivision are as follows:", targets: ["10.2 Contents of any permit under the Planning and Environment Act authorising the staged subdivision are as follows:"] },
      { kind: "heading", id: "10.3", label: "10.3 Further Plan of Subdivision" },
      { kind: "item", id: "10.3_certified", label: "Attached is a copy of the plan which has been certified by the relevant authority (if the later plan has not been registered)", targets: ["10.3 Attached is a copy of the plan which has been certified by the relevant authority (if the later plan has not been registered)"] },
      { kind: "item", id: "10.3_latest", label: "Attached is a copy of the latest version of the plan (if the later plan has not yet been certified)", targets: ["10.3 Attached is a copy of the latest version of the plan (if the later plan has not yet been certified)"] },
    ],
  },
  {
    id: "energy",
    title: "11. Disclosure of Energy Information",
    nodes: [
      { kind: "item", id: "11_attached", label: "Are contained in the attached building energy efficiency certificate", targets: ["11. Are contained in the attached building energy efficiency certificate"] },
      { kind: "item", id: "11_as_follows", label: "Are as follows:", targets: ["11. Are as follows:"] },
    ],
  },
  {
    id: "due_diligence",
    title: "12. Due Diligence Checklist",
    nodes: [
      { kind: "item", id: "policy_6_due_diligence", label: "12. Due Diligence Checklist", targets: ["policy_6_due_diligence"] },
    ],
  },
  {
    id: "attachments",
    title: "13. Attachments",
    nodes: [
      { kind: "item", id: "policy_6_attachments", label: "13. Attachments", targets: ["policy_6_attachments"] },
    ],
  },
];

export const DEFAULT_PREFERRED_FIELDS: string[] = Array.from(
  new Set(
    SECTIONS.flatMap((section) =>
      section.nodes
        .filter((node): node is PreferenceItem => node.kind === "item")
        .flatMap((node) => node.targets),
    ),
  ),
);

function mergeTargets(existing: string[], next: string[]) {
  return Array.from(new Set([...existing, ...next]));
}

function removeTargets(existing: string[], next: string[]) {
  const removal = new Set(next);
  return existing.filter((item) => !removal.has(item));
}

export function ClientPolicyScreen({
  onBack,
  onProfile,
  onSettings,
  onLogout,
  settings,
  onSaveSettings,
}: ClientPolicyScreenProps) {
  const [currentSubView, setCurrentSubView] = React.useState<"menu" | "configure" | "copy">("menu");
  const [selectedTargets, setSelectedTargets] = React.useState<string[]>(
    settings.preferredAutofillFields?.length ? settings.preferredAutofillFields : DEFAULT_PREFERRED_FIELDS,
  );
  const [expandedSections, setExpandedSections] = React.useState<Record<string, boolean>>(
    Object.fromEntries(SECTIONS.map((section) => [section.id, false])),
  );
  const [starredOpen, setStarredOpen] = React.useState(true);
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState("");
  const [searchQuery, setSearchQuery] = React.useState("");
  const [copyRules, setCopyRules] = React.useState<EditableCopyRule[]>([]);
  const [copyRulesLoading, setCopyRulesLoading] = React.useState(false);
  const [copyRulesError, setCopyRulesError] = React.useState("");
  const [copyRulesSavingId, setCopyRulesSavingId] = React.useState<string>("");

  React.useEffect(() => {
    setSelectedTargets(
      settings.preferredAutofillFields?.length ? settings.preferredAutofillFields : DEFAULT_PREFERRED_FIELDS,
    );
  }, [settings.preferredAutofillFields]);

  React.useEffect(() => {
    if (currentSubView !== "copy") return;
    let cancelled = false;
    setCopyRulesLoading(true);
    setCopyRulesError("");
    void listCopyRules()
      .then((rows) => {
        if (cancelled) return;
        setCopyRules(rows.map(toEditableCopyRule));
      })
      .catch((error) => {
        if (cancelled) return;
        setCopyRulesError(error instanceof Error ? error.message : "Could not load water authorities.");
      })
      .finally(() => {
        if (!cancelled) setCopyRulesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentSubView]);

  const items = React.useMemo(
    () => SECTIONS.flatMap((section) => section.nodes).filter((node): node is PreferenceItem => node.kind === "item"),
    [],
  );

  const starredItems = React.useMemo(
    () => items.filter((item) => item.targets.every((target) => selectedTargets.includes(target))),
    [items, selectedTargets],
  );

  const filteredSections = React.useMemo(() => {
    if (!searchQuery) return SECTIONS;
    return SECTIONS.map((section) => ({
      ...section,
      nodes: section.nodes.filter(
        (node) =>
          node.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
          (node.kind === "item" && node.id.toLowerCase().includes(searchQuery.toLowerCase())),
      ),
    })).filter((section) => section.nodes.length > 0);
  }, [searchQuery]);

  const toggleSection = (id: string) => {
    setExpandedSections((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleItem = (item: PreferenceItem) => {
    const selected = item.targets.every((target) => selectedTargets.includes(target));
    setSelectedTargets((prev) => (selected ? removeTargets(prev, item.targets) : mergeTargets(prev, item.targets)));
  };

  const toggleEntireSection = (section: PreferenceSection) => {
    const sectionItems = section.nodes.filter((node): node is PreferenceItem => node.kind === "item");
    const allSelected = sectionItems.every((item) => item.targets.every((target) => selectedTargets.includes(target)));

    if (allSelected) {
      const targetsToRemove = sectionItems.flatMap((item) => item.targets);
      setSelectedTargets((prev) => removeTargets(prev, targetsToRemove));
    } else {
      const targetsToAdd = sectionItems.flatMap((item) => item.targets);
      setSelectedTargets((prev) => mergeTargets(prev, targetsToAdd));
    }
  };

  const handleSave = async () => {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError("");
    try {
      await onSaveSettings({
        ...settings,
        preferredAutofillFields: selectedTargets,
      });
      setCurrentSubView("menu");
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Could not save starred autofill preferences.");
    } finally {
      setIsSaving(false);
    }
  };

  function toEditableCopyRule(rule: CopyRulePayload): EditableCopyRule {
    return {
      id: rule.id,
      authority_name: rule.authority_name,
      annual_amount: String(rule.annual_amount.toFixed(2)),
      notes: rule.notes || "",
      is_active: rule.is_active,
    };
  }

  const updateCopyRuleDraft = (id: string, patch: Partial<EditableCopyRule>) => {
    setCopyRules((prev) => prev.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)));
  };

  const handleAddCopyRule = () => {
    setCopyRules((prev) => [
      {
        id: `new-${Date.now()}`,
        authority_name: "",
        annual_amount: "",
        notes: "",
        is_active: true,
        isNew: true,
      },
      ...prev,
    ]);
  };

  const handleSaveCopyRule = async (rule: EditableCopyRule) => {
    const annualAmount = Number(rule.annual_amount);
    if (!rule.authority_name.trim()) {
      setCopyRulesError("Authority name is required.");
      return;
    }
    if (!Number.isFinite(annualAmount) || annualAmount < 0) {
      setCopyRulesError("Annual amount must be a valid positive number.");
      return;
    }

    setCopyRulesSavingId(rule.id);
    setCopyRulesError("");
    try {
      const payload = {
        rule_type: "water_authority" as const,
        authority_name: rule.authority_name.trim(),
        annual_amount: annualAmount,
        notes: rule.notes.trim() || null,
        is_active: rule.is_active,
      };
      const saved = rule.isNew ? await createCopyRule(payload) : await updateCopyRule(rule.id, payload);
      setCopyRules((prev) => prev.map((item) => (item.id === rule.id ? toEditableCopyRule(saved) : item)));
    } catch (error) {
      setCopyRulesError(error instanceof Error ? error.message : "Could not save water authority rule.");
    } finally {
      setCopyRulesSavingId("");
    }
  };

  const handleDeleteCopyRule = async (rule: EditableCopyRule) => {
    if (rule.isNew) {
      setCopyRules((prev) => prev.filter((item) => item.id !== rule.id));
      return;
    }
    setCopyRulesSavingId(rule.id);
    setCopyRulesError("");
    try {
      await deleteCopyRule(rule.id);
      setCopyRules((prev) => prev.filter((item) => item.id !== rule.id));
    } catch (error) {
      setCopyRulesError(error instanceof Error ? error.message : "Could not delete water authority rule.");
    } finally {
      setCopyRulesSavingId("");
    }
  };

  const renderMenu = () => (
    <div className="mx-auto max-w-4xl py-12">
      <div className="mb-12 text-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="mb-6 inline-flex h-16 w-16 items-center justify-center rounded-3xl bg-primary/10 text-primary"
        >
          <Sparkles className="h-8 w-8" />
        </motion.div>
        <h1 className="text-4xl font-serif italic text-slate-900 dark:text-foreground">Custom Policy</h1>
        <p className="mt-2 text-lg text-slate-500 dark:text-muted-foreground">Manage how your AI assistant handles property law conventions.</p>
      </div>

      <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
        {[
          {
            id: "configure",
            title: "Configure Rules",
            desc: "Fine-tune which fields are automatically processed and starred in your workspace.",
            icon: Settings2,
            color: "bg-blue-50 text-blue-600 border-blue-100",
            action: () => setCurrentSubView("configure"),
          },
          {
            id: "copy",
            title: "Copy Rules",
            desc: "Import configuration sets from existing clients or pre-approved law templates.",
            icon: Copy,
            color: "bg-emerald-50 text-emerald-600 border-emerald-100",
            action: () => setCurrentSubView("copy"),
          },
        ].map((option, index) => (
          <motion.div
            key={option.id}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.1 }}
            onClick={option.action}
            className="group relative cursor-pointer"
          >
            <div className="h-full rounded-[2.5rem] policy-glass border p-8 transition-all hover:-translate-y-1 hover:shadow-2xl hover:shadow-slate-200/60 dark:hover:shadow-violet-900/20">
              <div className={`mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border ${option.color} transition-transform group-hover:scale-110`}>
                <option.icon className="h-7 w-7" />
              </div>
              <h3 className="mb-3 text-2xl font-bold text-slate-900 dark:text-foreground">{option.title}</h3>
              <p className="mb-6 leading-relaxed text-slate-500 dark:text-muted-foreground">{option.desc}</p>
              <div className="flex items-center text-sm font-bold text-primary transition-all group-hover:gap-2">
                Enter {option.title} <ChevronRight className="h-4 w-4" />
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );

  const renderConfigure = () => (
    <div className="mx-auto max-w-5xl">
      <div className="mb-10 flex flex-col justify-between gap-6 md:flex-row md:items-center">
        <div>
          <button
            onClick={() => setCurrentSubView("menu")}
            className="mb-2 flex items-center gap-2 text-sm font-bold text-slate-400 dark:text-muted-foreground transition-colors hover:text-slate-600 dark:hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" /> Back to Custom Policy
          </button>
          <h1 className="font-sans text-3xl font-black tracking-tight text-slate-900 dark:text-foreground">Configure Rules</h1>
          <p className="text-base text-slate-500 dark:text-muted-foreground">Fine-tune your autofill preferences and system conventions.</p>
        </div>

        <div className="group relative">
          <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-muted-foreground transition-colors group-focus-within:text-primary" />
          <input
            type="text"
            placeholder="Search rules or sections..."
            className="h-12 w-full rounded-2xl border border-slate-200 dark:border-border bg-white dark:bg-card pl-11 pr-6 text-sm shadow-sm dark:shadow-none transition-all focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 md:w-72"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </div>
      </div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-8">
        <section className="relative overflow-hidden rounded-[2.5rem] border border-slate-200 dark:border-border bg-white dark:bg-card shadow-xl shadow-slate-200/50 dark:shadow-none">
          <div className="absolute inset-0 pointer-events-none opacity-[0.03]" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, #000 1px, transparent 0)", backgroundSize: "24px 24px" }} />
          <div className="relative">
            <button
              onClick={() => setStarredOpen((value) => !value)}
              className="flex w-full items-center justify-between px-10 py-8 text-left transition-colors hover:bg-slate-50 dark:hover:bg-accent/60"
            >
              <div className="flex items-center gap-6">
                <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-violet-50 text-violet-400 shadow-inner transition-transform group-hover:rotate-6">
                  <Star className="h-8 w-8 fill-current" />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-slate-900 dark:text-foreground">Starred for Autofill</h2>
                  <p className="text-sm font-medium text-slate-400 dark:text-muted-foreground">
                    {starredItems.length === 0 ? "No fields selected" : `${starredItems.length} items will be auto-prioritized in processing`}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-4">
                <div className="rounded-full bg-slate-100 dark:bg-accent/50 px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-500 dark:text-muted-foreground">
                  {starredItems.length} ACTIVE
                </div>
                {starredOpen ? <ChevronDown className="h-5 w-5 text-slate-400 dark:text-muted-foreground" /> : <ChevronRight className="h-5 w-5 text-slate-400 dark:text-muted-foreground" />}
              </div>
            </button>

            <AnimatePresence>
              {starredOpen && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden border-t border-slate-100 dark:border-border/60"
                >
                  <div className="px-10 py-8">
                    {starredItems.length ? (
                      <div className="flex flex-wrap gap-3">
                        {starredItems.map((item) => (
                          <button
                            key={item.id}
                            onClick={() => toggleItem(item)}
                            className="group flex items-center gap-2.5 rounded-xl border border-slate-200 dark:border-border bg-white dark:bg-card px-4 py-2.5 text-xs font-bold text-slate-700 dark:text-foreground shadow-sm dark:shadow-none transition-all hover:scale-[1.03] hover:bg-slate-50 dark:hover:bg-accent/60 active:scale-[0.97]"
                          >
                            <Star className="h-3.5 w-3.5 fill-violet-400 text-violet-400" />
                            {item.label}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="rounded-[2rem] border-2 border-dashed border-slate-200 dark:border-border bg-slate-50/50 dark:bg-accent/30 px-10 py-12 text-center">
                        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-slate-100 dark:border-border/60 bg-white text-slate-300 shadow-sm">
                          <Star className="h-6 w-6" />
                        </div>
                        <p className="text-lg font-bold text-slate-900 dark:text-foreground">No starred preferences yet</p>
                        <p className="mx-auto mt-1 max-w-sm text-sm font-medium text-slate-400 dark:text-muted-foreground">Starred items are automatically filled by AI and prioritized in the review workspace.</p>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </section>

        <div className="space-y-6">
          <div className="flex items-center justify-between px-2">
            <div className="flex items-center gap-3">
              <Filter className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-black uppercase tracking-widest text-slate-400 dark:text-muted-foreground">Document Sections</h2>
            </div>
            <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400 dark:text-muted-foreground">
              Showing {filteredSections.length} of {SECTIONS.length}
            </div>
          </div>

          <div className="grid gap-4">
            {filteredSections.map((section, index) => {
              const open = expandedSections[section.id];
              const sectionItems = section.nodes.filter((node): node is PreferenceItem => node.kind === "item");
              const selectableCount = sectionItems.length;
              const activeCount = sectionItems.filter((item) => item.targets.every((target) => selectedTargets.includes(target))).length;
              const allSelected = activeCount === selectableCount;

              return (
                <motion.div
                  key={section.id}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.05 }}
                  className="overflow-hidden rounded-[2rem] border border-slate-200 dark:border-border bg-white dark:bg-card shadow-sm dark:shadow-none transition-all hover:shadow-md dark:hover:shadow-none"
                >
                  <div className="flex w-full items-center justify-between py-6 pl-6 pr-4">
                    <div className="flex flex-1 cursor-pointer items-center gap-5" onClick={() => toggleSection(section.id)}>
                      <div
                        className={[
                          "flex h-12 w-12 items-center justify-center rounded-2xl text-base font-bold shadow-inner transition-all",
                          activeCount > 0 ? "scale-110 bg-primary text-white" : "bg-slate-100 dark:bg-accent/50 text-slate-400 dark:text-muted-foreground",
                        ].join(" ")}
                      >
                        {section.title.split(".")[0]}
                      </div>
                      <div>
                        <h3 className="text-lg font-bold text-slate-900 dark:text-foreground transition-colors group-hover:text-primary">{section.title.replace(/^\d+\.\s*/, "")}</h3>
                        <p className="mt-0.5 text-xs font-semibold text-slate-400 dark:text-muted-foreground">{activeCount} items marked for autofill</p>
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      <button
                        onClick={() => toggleEntireSection(section)}
                        className={[
                          "flex h-10 items-center gap-2 rounded-xl border px-4 text-[10px] font-black uppercase tracking-tighter transition-all",
                          allSelected
                            ? "border-violet-300 bg-violet-400 text-white shadow-lg shadow-violet-300/20 dark:shadow-none"
                            : "border-slate-200 dark:border-border bg-white text-slate-500 dark:text-muted-foreground hover:bg-slate-50 dark:hover:bg-accent/60",
                        ].join(" ")}
                      >
                        <Star className={["h-3.5 w-3.5", allSelected ? "fill-current" : ""].join(" ")} />
                        {allSelected ? "Starred All" : "Star Section"}
                      </button>

                      <button
                        onClick={() => toggleSection(section.id)}
                        className="flex h-10 w-10 items-center justify-center rounded-xl transition-colors hover:bg-slate-50 dark:hover:bg-accent/60"
                      >
                        {open ? <ChevronDown className="h-5 w-5 text-slate-400 dark:text-muted-foreground" /> : <ChevronRight className="h-5 w-5 text-slate-400 dark:text-muted-foreground" />}
                      </button>
                    </div>
                  </div>

                  <AnimatePresence>
                    {open && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden border-t border-slate-100 dark:border-border/60"
                      >
                        <div className="grid grid-cols-1 gap-3 bg-slate-50/50 dark:bg-accent/30 px-8 py-8 md:grid-cols-2">
                          {section.nodes.map((node) => {
                            if (node.kind === "heading") {
                              return (
                                <div key={node.id} className="col-span-2 mb-2 flex items-center gap-3 pt-6 text-[0.7rem] font-black uppercase tracking-widest text-slate-400 dark:text-muted-foreground first:pt-0">
                                  <div className="h-px flex-1 bg-slate-200" />
                                  {node.label}
                                  <div className="h-px flex-1 bg-slate-200" />
                                </div>
                              );
                            }

                            const active = node.targets.every((target) => selectedTargets.includes(target));
                            return (
                              <button
                                key={node.id}
                                onClick={() => toggleItem(node)}
                                className={[
                                  "group flex w-full items-center gap-4 rounded-2xl border px-5 py-4 text-left transition-all",
                                  active ? "border-slate-300 dark:border-border bg-white dark:bg-card shadow-md dark:shadow-none" : "border-slate-100 dark:border-border/60 bg-white dark:bg-card hover:border-primary/30",
                                ].join(" ")}
                              >
                                <div
                                  className={[
                                    "flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.9rem] border transition-all",
                                    active
                                      ? "border-violet-200 bg-violet-50 text-violet-400"
                                      : "border-slate-100 dark:border-border/60 bg-slate-50 text-slate-300 group-hover:border-primary/30",
                                  ].join(" ")}
                                >
                                  <Star className={["h-5 w-5", active ? "fill-current" : ""].join(" ")} />
                                </div>
                                <div className="flex-1">
                                  <div
                                    className={[
                                      "text-[0.9rem] font-bold leading-tight transition-colors",
                                      active ? "text-slate-900 dark:text-foreground" : "text-slate-500 dark:text-muted-foreground group-hover:text-slate-700 dark:group-hover:text-foreground",
                                    ].join(" ")}
                                  >
                                    {node.label}
                                  </div>
                                  <div className="mt-1 flex items-center gap-1.5 text-[0.65rem] font-bold uppercase tracking-widest text-slate-400 dark:text-muted-foreground">
                                    <Zap className="h-2.5 w-2.5 text-slate-400 dark:text-muted-foreground" />
                                    {node.targets.length} autofill targets
                                  </div>
                                </div>
                                {active && (
                                  <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 shadow-lg shadow-emerald-500/20">
                                    <Check className="h-3 w-3 stroke-[3] text-white" />
                                  </div>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              );
            })}
          </div>
        </div>

        <div className="fixed bottom-10 right-10 z-50 flex items-center gap-4 rounded-3xl border border-white/40 dark:border-border bg-white/60 dark:bg-card/80 p-2 shadow-2xl shadow-indigo-200/50 dark:shadow-none backdrop-blur-2xl">
          {saveError ? (
            <p className="mr-2 whitespace-nowrap rounded-xl border border-destructive/20 bg-destructive/10 px-4 py-2 text-xs font-bold text-destructive shadow-sm">
              {saveError}
            </p>
          ) : null}
          <Button
            variant="outline"
            className="h-14 rounded-2xl border border-slate-200 dark:border-border bg-white/80 dark:bg-card px-8 font-bold text-slate-600 dark:text-foreground shadow-sm dark:shadow-none transition-all hover:bg-slate-50 dark:hover:bg-accent/60"
            onClick={() => setCurrentSubView("menu")}
          >
            Cancel
          </Button>
          <Button
            className="h-14 rounded-2xl bg-violet-500 px-10 font-bold text-white shadow-2xl shadow-violet-200 dark:shadow-none transition-all hover:bg-violet-600 hover:shadow-violet-300 dark:hover:shadow-none active:scale-[0.98]"
            onClick={() => void handleSave()}
            disabled={isSaving}
          >
            {isSaving ? (
              <div className="flex items-center gap-2">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/20 border-t-white" />
                Saving...
              </div>
            ) : "Confirm & Save"}
          </Button>
        </div>
      </motion.div>
    </div>
  );

  const renderCopy = () => (
    <div className="mx-auto max-w-4xl py-8">
      <div className="mb-10">
        <button
          onClick={() => setCurrentSubView("menu")}
          className="mb-2 flex items-center gap-2 text-sm font-bold text-slate-400 dark:text-muted-foreground transition-colors hover:text-slate-600"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Custom Policy
        </button>
        <h1 className="font-sans text-3xl font-black tracking-tight text-slate-900 dark:text-foreground">Copy Records</h1>
        <p className="text-base text-slate-500 dark:text-muted-foreground">Manage fallback authority tariffs used when a water authority amount cannot be extracted from the document.</p>
      </div>

      <div className="space-y-6">
        <div className="flex items-start gap-8 rounded-[2.5rem] border border-primary/10 bg-primary/5 p-10">
          <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-3xl bg-primary/10 text-primary">
            <Info className="h-10 w-10" />
          </div>
          <div className="space-y-4">
            <h3 className="text-2xl font-bold text-slate-900 dark:text-foreground">How water authority fallback works</h3>
            <p className="text-lg leading-relaxed text-slate-600">
              The water authority name is still extracted from the uploaded documents. If the annual amount is missing or fails to extract, the app matches that authority name against this table and uses the saved annual amount as the fallback.
            </p>
          </div>
        </div>

        <div className="space-y-4">
          <div className="flex items-center justify-between px-2">
            <h4 className="text-xs font-black uppercase tracking-widest text-slate-400 dark:text-muted-foreground">Water Authorities</h4>
            <Button className="rounded-2xl bg-violet-500 px-5 font-bold text-white hover:bg-violet-600" onClick={handleAddCopyRule}>
              + Add Authority
            </Button>
          </div>

          <div className="overflow-hidden rounded-[2rem] border border-slate-200 dark:border-border dark:border-border bg-white dark:bg-card shadow-sm">
            <div className="grid grid-cols-[2fr_1fr_1.3fr_160px] gap-3 border-b border-slate-100 dark:border-border/60 bg-slate-50 px-6 py-4 text-[11px] font-black uppercase tracking-widest text-slate-400 dark:text-muted-foreground">
              <div>Authority Name</div>
              <div>Annual Amount</div>
              <div>Notes</div>
              <div>Actions</div>
            </div>

            {copyRulesError ? <div className="px-6 py-3 text-sm font-semibold text-destructive">{copyRulesError}</div> : null}
            {copyRulesLoading ? <div className="px-6 py-6 text-sm text-slate-500 dark:text-muted-foreground">Loading water authority rules...</div> : null}
            {!copyRulesLoading && copyRules.length === 0 ? (
              <div className="px-6 py-8 text-sm text-slate-500 dark:text-muted-foreground">No water authority fallback rules yet. Add one to start using database fallback amounts.</div>
            ) : null}

            <div className="divide-y divide-slate-100">
              {copyRules.map((rule) => {
                const saving = copyRulesSavingId === rule.id;
                return (
                  <div key={rule.id} className="grid grid-cols-[2fr_1fr_1.3fr_160px] gap-3 px-6 py-4">
                    <input
                      value={rule.authority_name}
                      onChange={(event) => updateCopyRuleDraft(rule.id, { authority_name: event.target.value })}
                      placeholder="Yarra Valley Water"
                      className="h-12 rounded-2xl border border-slate-200 dark:border-border px-4 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <input
                      value={rule.annual_amount}
                      onChange={(event) => updateCopyRuleDraft(rule.id, { annual_amount: event.target.value })}
                      placeholder="774.72"
                      className="h-12 rounded-2xl border border-slate-200 dark:border-border px-4 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <input
                      value={rule.notes}
                      onChange={(event) => updateCopyRuleDraft(rule.id, { notes: event.target.value })}
                      placeholder="Optional note"
                      className="h-12 rounded-2xl border border-slate-200 dark:border-border px-4 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        className="rounded-2xl border-slate-200 dark:border-border font-bold"
                        onClick={() => void handleSaveCopyRule(rule)}
                        disabled={saving}
                      >
                        {saving ? "Saving..." : "Save"}
                      </Button>
                      <Button
                        variant="outline"
                        className="rounded-2xl border-rose-200 font-bold text-rose-600 hover:bg-rose-50"
                        onClick={() => void handleDeleteCopyRule(rule)}
                        disabled={saving}
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <div className="relative min-h-screen overflow-hidden bg-gradient-to-br from-slate-50 via-blue-50/30 to-violet-50/20 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 font-sans text-foreground">
      <style>{`
        .policy-orb-1 { position:fixed; top:-15%; left:-10%; width:55%; height:55%; background:radial-gradient(circle, rgba(139,92,246,0.10) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .policy-orb-2 { position:fixed; bottom:-10%; right:-5%; width:45%; height:45%; background:radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%); pointer-events:none; z-index:0; }
        .policy-glass { background:rgba(255,255,255,0.72) !important; backdrop-filter:blur(20px) saturate(160%); -webkit-backdrop-filter:blur(20px) saturate(160%); border-color:rgba(255,255,255,0.55) !important; }
        .dark .policy-glass { background:rgba(15,23,42,0.55) !important; border-color:rgba(255,255,255,0.08) !important; }
      `}</style>
      <div className="policy-orb-1" />
      <div className="policy-orb-2" />
      <Header
        onBack={currentSubView === "menu" ? onBack : () => setCurrentSubView("menu")}
        onProfile={onProfile || (() => {})}
        onSettings={onSettings || (() => {})}
        onPolicy={() => {}}
        onLogout={onLogout || (() => {})}
        title={currentSubView === "menu" ? "Custom Policy" : currentSubView === "configure" ? "Configure Rules" : "Copy Rules"}
      />

      <main className="custom-scrollbar relative z-10 h-[calc(100vh-64px)] flex-1 overflow-y-auto px-6 py-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentSubView}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
          >
            {currentSubView === "menu" && renderMenu()}
            {currentSubView === "configure" && renderConfigure()}
            {currentSubView === "copy" && renderCopy()}
          </motion.div>
        </AnimatePresence>
      </main>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 6px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: transparent;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: #cbd5e1;
          border-radius: 10px;
        }
      `}</style>
    </div>
  );
}

