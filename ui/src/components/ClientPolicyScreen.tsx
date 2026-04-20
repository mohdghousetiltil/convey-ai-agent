import React from "react";
import { motion } from "motion/react";
import { ChevronDown, ChevronLeft, ChevronRight, Sparkles, Star } from "lucide-react";
import { Button } from "@/components/ui/button";

interface SettingsForm {
  language: string;
  openAiApiKey: string;
  anthropicApiKey: string;
  aiProvider: "openai" | "anthropic";
  defaultModelName: string;
  triconveyPath: string;
  preferredAutofillFields: string[];
}

interface ClientPolicyScreenProps {
  onBack: () => void;
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

function mergeTargets(existing: string[], next: string[]) {
  return Array.from(new Set([...existing, ...next]));
}

function removeTargets(existing: string[], next: string[]) {
  const removal = new Set(next);
  return existing.filter((item) => !removal.has(item));
}

export function ClientPolicyScreen({ onBack, settings, onSaveSettings }: ClientPolicyScreenProps) {
  const [selectedTargets, setSelectedTargets] = React.useState<string[]>(settings.preferredAutofillFields || []);
  const [expandedSections, setExpandedSections] = React.useState<Record<string, boolean>>(
    Object.fromEntries(SECTIONS.map((section) => [section.id, false])),
  );
  const [starredOpen, setStarredOpen] = React.useState(true);
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState("");

  React.useEffect(() => {
    setSelectedTargets(settings.preferredAutofillFields || []);
  }, [settings.preferredAutofillFields]);

  const items = React.useMemo(
    () => SECTIONS.flatMap((section) => section.nodes).filter((node): node is PreferenceItem => node.kind === "item"),
    [],
  );

  const starredItems = React.useMemo(
    () => items.filter((item) => item.targets.every((target) => selectedTargets.includes(target))),
    [items, selectedTargets],
  );

  const toggleSection = (id: string) => {
    setExpandedSections((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleItem = (item: PreferenceItem) => {
    const selected = item.targets.every((target) => selectedTargets.includes(target));
    setSelectedTargets((prev) => (selected ? removeTargets(prev, item.targets) : mergeTargets(prev, item.targets)));
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
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Could not save starred autofill preferences.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 font-sans">
      <header className="sticky top-0 z-50 flex h-16 items-center border-b bg-white px-6">
        <div
          onClick={onBack}
          className="mr-4 flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg bg-muted transition-colors hover:bg-slate-200"
        >
          <ChevronLeft className="h-4 w-4 stroke-[2.5] text-foreground" />
        </div>
        <div>
          <h1 className="text-lg font-bold">Custom Policy</h1>
          <p className="text-xs text-slate-400">Choose exactly which starred fields Convey should auto-fill.</p>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 p-6">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
            <button
              onClick={() => setStarredOpen((value) => !value)}
              className="flex w-full items-center justify-between px-6 py-5 text-left transition-colors hover:bg-slate-50"
            >
              <div className="flex items-center gap-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-600">
                  <Star className="h-5 w-5 fill-current" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Starred for Autofill</h2>
                  <p className="text-sm text-slate-500">
                    Only these selected questions will be filled in Convey. Unstarred fields will be skipped.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-bold uppercase tracking-wider text-amber-700">
                  {starredItems.length} selected
                </span>
                {starredOpen ? <ChevronDown className="h-5 w-5 text-slate-400" /> : <ChevronRight className="h-5 w-5 text-slate-400" />}
              </div>
            </button>
            {starredOpen ? (
              <div className="border-t border-slate-100 px-6 py-5">
                {starredItems.length ? (
                  <div className="flex flex-wrap gap-2">
                    {starredItems.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => toggleItem(item)}
                        className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-100"
                      >
                        <Star className="h-3.5 w-3.5 fill-current" />
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-500">
                    No starred items yet. Star the golden questions below, and they will appear here automatically.
                  </div>
                )}
              </div>
            ) : null}
          </section>

          <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-100 px-6 py-5">
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Choose your preference</h2>
                  <p className="mt-1 text-sm text-slate-500">
                    Headings are shown in bold for structure. Star the actual questions you want Brain E to fill inside Convey.
                  </p>
                </div>
              </div>
            </div>

            <div className="divide-y divide-slate-100">
              {SECTIONS.map((section) => {
                const open = expandedSections[section.id];
                return (
                  <div key={section.id}>
                    <button
                      onClick={() => toggleSection(section.id)}
                      className="flex w-full items-center justify-between px-6 py-4 text-left transition-colors hover:bg-slate-50"
                    >
                      <div>
                        <h3 className="text-base font-bold text-slate-900">{section.title}</h3>
                        <p className="mt-0.5 text-xs text-slate-400">
                          {section.nodes.filter((node) => node.kind === "item").length} selectable question(s)
                        </p>
                      </div>
                      {open ? <ChevronDown className="h-5 w-5 text-slate-400" /> : <ChevronRight className="h-5 w-5 text-slate-400" />}
                    </button>

                    {open ? (
                      <div className="space-y-2 px-6 pb-5">
                        {section.nodes.map((node) => {
                          if (node.kind === "heading") {
                            return (
                              <div key={node.id} className="pt-3 text-sm font-bold text-slate-900">
                                {node.label}
                              </div>
                            );
                          }

                          const active = node.targets.every((target) => selectedTargets.includes(target));
                          return (
                            <button
                              key={node.id}
                              onClick={() => toggleItem(node)}
                              className={[
                                "flex w-full items-start gap-3 rounded-2xl border px-4 py-3 text-left transition-all",
                                active
                                  ? "border-amber-200 bg-amber-50 shadow-sm"
                                  : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
                              ].join(" ")}
                            >
                              <span
                                className={[
                                  "mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border",
                                  active
                                    ? "border-amber-300 bg-amber-100 text-amber-700"
                                    : "border-slate-200 bg-slate-50 text-slate-300",
                                ].join(" ")}
                              >
                                <Star className={["h-3.5 w-3.5", active ? "fill-current" : ""].join(" ")} />
                              </span>
                              <div className="flex-1">
                                <div className="text-sm font-semibold text-slate-700">{node.label}</div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </section>

          <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-6 py-4 shadow-sm">
            <div className="space-y-1">
              <p className="text-sm text-slate-500">
                Save your starred preferences to make Brain E auto-fill only those selected questions in Convey.
              </p>
              {saveError ? <p className="text-sm font-medium text-rose-600">{saveError}</p> : null}
            </div>
            <Button className="rounded-xl px-6 py-2.5 font-bold" onClick={() => void handleSave()} disabled={isSaving}>
              {isSaving ? "Saving..." : "Save Preferences"}
            </Button>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
