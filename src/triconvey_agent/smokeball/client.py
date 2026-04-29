"""Smokeball/TriConvey direct API client.

Provides JWT extraction from the live triConvey.exe process, matter lookup
via the Smokeball browse-graphql endpoint, and S32 conveyancing data push
via the appgw REST API (with local SQLite fallback).

Architecture:
  - get_smokeball_token()  → extracts live Bearer token from process memory
  - SmokeballClient        → authenticated HTTP client with auto-refresh
  - push_s32_fields()      → writes Section 32 conveyancing fields to a matter
"""
from __future__ import annotations

import ctypes
import json
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

LOG = logging.getLogger(__name__)


def _dbg(event: str, **fields: Any) -> None:
    """Append a structured debug line to the app's smokeball_push_debug.log."""
    try:
        try:
            from triconvey_agent.backend.runtime import get_runtime_paths
            log_dir = get_runtime_paths().local_app_dir
        except Exception:
            import os
            log_dir = Path(os.getenv("LOCALAPPDATA", "")) / "TriConveyAgent"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "smokeball_push_debug.log"
        from datetime import datetime as _dt
        ts = _dt.now().isoformat(timespec="seconds")
        parts = [f"ts={ts}", f"event={event}"]
        for k, v in fields.items():
            parts.append(f"{k}={str(v).replace(chr(10), ' ')[:500]}")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(" | ".join(parts) + "\n")
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

SMOKEBALL_APPGW = "https://appgw.smokeball.com.au"
GRAPHQL_URL = f"{SMOKEBALL_APPGW}/mattermanagement/browse-graphql/graphql"

LOCAL_DB   = Path(r"C:\Program Files\Smokeball\dataAu\data.db")
LAYOUTS_DB = Path(r"C:\Program Files\Smokeball\dataAu\layouts.db")
BROWSE_DB  = Path(r"C:\Program Files\Smokeball\dataAu\mm-browsematters.db")

# FirmId discovered from local data.db (constant per installation)
FIRM_ID = "7a0434d3-675f-4272-b2da-292508e06c71"

# S32 field key prefix in the LayoutValue system
S32_KEY_PREFIX = "Matter/ConveyancingDetails"

# ── Process memory token extraction ──────────────────────────────────────────

def _find_triconvey_pids() -> list[int]:
    """Return PIDs of all running triConvey processes (any casing/suffix)."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"], text=True, timeout=5
        )
        pids = []
        for line in out.strip().splitlines():
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 2 and "convey" in parts[0].lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return pids
    except Exception:
        return []


class _MBI64(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION64 — correct layout for 64-bit Windows."""
    _fields_ = [
        ("BaseAddress",      ctypes.c_ulonglong),
        ("AllocationBase",   ctypes.c_ulonglong),
        ("AllocationProtect", ctypes.c_ulong),
        ("_align1",          ctypes.c_ulong),
        ("RegionSize",       ctypes.c_ulonglong),
        ("State",            ctypes.c_ulong),   # MEM_COMMIT = 0x1000
        ("Protect",          ctypes.c_ulong),
        ("Type",             ctypes.c_ulong),
        ("_align2",          ctypes.c_ulong),
    ]


_JWT_RE = re.compile(rb"Bearer (eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)")

_PAGE_CHUNK = 4 * 1024 * 1024  # 4 MB read buffer


def get_smokeball_token(pid: int | None = None) -> str | None:
    """Scan triConvey process memory and return the best live Cognito id_token.

    Prefers token_use='id' (Cognito id_token) over access_token.
    If *pid* is not supplied, auto-detects the running triConvey process.
    Uses PROCESS_QUERY_INFORMATION|PROCESS_VM_READ (0x0410) — no admin needed
    as long as triConvey runs in the same user session.
    """
    import ctypes.wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.VirtualQueryEx.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.c_ulonglong,
        ctypes.POINTER(_MBI64), ctypes.c_size_t,
    ]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.ReadProcessMemory.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.c_ulonglong,
        ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.ReadProcessMemory.restype = ctypes.c_bool

    if pid is None:
        pids = _find_triconvey_pids()
        if not pids:
            LOG.warning("triConvey not running — cannot extract token")
            return None
        pid = pids[0]

    # Minimal rights sufficient for same-session process
    PROCESS_QUERY_VM_READ = 0x0410
    handle = k32.OpenProcess(PROCESS_QUERY_VM_READ, False, pid)
    if not handle:
        LOG.error("OpenProcess failed PID=%d err=%d", pid, ctypes.get_last_error())
        return None

    import base64, json as _json
    from datetime import datetime, timezone

    def _decode_payload(tok: str) -> dict:
        try:
            parts = tok.split(".")
            if len(parts) < 2:
                return {}
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            return _json.loads(base64.urlsafe_b64decode(padded))
        except Exception:
            return {}

    jwt_re = re.compile(
        rb'eyJ[A-Za-z0-9_\-]{50,}\.[A-Za-z0-9_\-]{50,}\.[A-Za-z0-9_\-]{20,}'
    )

    try:
        mbi = _MBI64()
        buf = ctypes.create_string_buffer(_PAGE_CHUNK)
        bytes_read = ctypes.c_size_t(0)
        addr = 0
        candidates: list[tuple[int, str]] = []  # (priority, token)
        now = datetime.now(timezone.utc).timestamp()

        while True:
            ret = k32.VirtualQueryEx(handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi))
            if ret == 0:
                break
            prot = mbi.Protect & 0xFF
            if mbi.State == 0x1000 and prot not in (0x01,) and not (mbi.Protect & 0x100):
                offset = 0
                while offset < mbi.RegionSize:
                    read_size = min(_PAGE_CHUNK, mbi.RegionSize - offset)
                    ok = k32.ReadProcessMemory(
                        handle, addr + offset, buf, read_size, ctypes.byref(bytes_read)
                    )
                    if ok and bytes_read.value > 0:
                        for m in jwt_re.finditer(buf.raw[:bytes_read.value]):
                            tok = m.group(0).decode("ascii", "ignore")
                            payload = _decode_payload(tok)
                            exp = payload.get("exp", 0)
                            if exp and exp < now:
                                continue  # expired
                            use = payload.get("token_use", "")
                            priority = 0 if use == "id" else (1 if use == "access" else 2)
                            if not any(tok == c[1] for c in candidates):
                                candidates.append((priority, tok))
                    offset += _PAGE_CHUNK
            addr += mbi.RegionSize if mbi.RegionSize > 0 else 0x10000
    finally:
        k32.CloseHandle(handle)

    if not candidates:
        LOG.warning("No valid JWT found in PID %d memory", pid)
        _dbg("token_scan_empty", pid=pid)
        return None

    candidates.sort(key=lambda x: x[0])
    best = candidates[0][1]
    payload = _decode_payload(best)
    LOG.info("Extracted token (use=%s) from PID %d", payload.get("token_use", "?"), pid)
    _dbg("token_extracted", pid=pid, token_use=payload.get("token_use"), exp=payload.get("exp"),
         sub=str(payload.get("sub", ""))[:8])
    return best


# ── SmokeballClient ───────────────────────────────────────────────────────────

class SmokeballClient:
    """Authenticated HTTP client for the Smokeball appgw API.

    Usage::

        client = SmokeballClient()
        matter = client.find_matter_by_number("2026-04/2329")
        client.push_s32_fields(matter["id"], s32_data)
    """

    def __init__(self, token: str | None = None, firm_id: str = FIRM_ID):
        self.firm_id = firm_id
        LOG.info("[smokeball] SmokeballClient init — firm_id=%s  token_provided=%s", firm_id, bool(token))
        self._token = token or get_smokeball_token()
        if not self._token:
            LOG.error("[smokeball] No token available — triConvey.exe may not be running")
            raise RuntimeError(
                "Cannot initialise SmokeballClient: triConvey.exe is not running "
                "or no Bearer token could be extracted from process memory."
            )
        import base64 as _b64, json as _j
        try:
            parts = self._token.split(".")
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = _j.loads(_b64.urlsafe_b64decode(padded))
            LOG.info(
                "[smokeball] token: use=%s  exp=%s  sub=%s",
                payload.get("token_use"), payload.get("exp"), str(payload.get("sub", ""))[:8],
            )
        except Exception:
            pass
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            timeout=30,
        )

    def _refresh_token(self) -> None:
        fresh = get_smokeball_token()
        if fresh:
            self._token = fresh
            self._http.headers["Authorization"] = f"Bearer {fresh}"

    # ── GraphQL helpers ───────────────────────────────────────────────────────

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        payload = {"query": query, "variables": variables or {}}
        _dbg("gql_request", url=GRAPHQL_URL, query_preview=query.strip()[:120], variables=variables)
        resp = self._http.post(GRAPHQL_URL, json=payload)
        _dbg("gql_response", status=resp.status_code, body=resp.text[:800])
        if resp.status_code == 401:
            LOG.info("[smokeball] 401 — refreshing token and retrying")
            _dbg("gql_token_refresh")
            self._refresh_token()
            resp = self._http.post(GRAPHQL_URL, json=payload)
            _dbg("gql_response_retry", status=resp.status_code, body=resp.text[:800])
        if not resp.is_success:
            LOG.error(
                "[smokeball] GQL failed: status=%d\nRequest: %s\nResponse: %s",
                resp.status_code, json.dumps(payload)[:1000], resp.text[:1000],
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            LOG.warning("[smokeball] GQL returned errors: %s", body["errors"])
            _dbg("gql_errors", errors=body["errors"])
        return body

    def list_matters(self, limit: int = 200) -> list[dict]:
        """Return matters visible to the current user."""
        q = """
        {
          matters {
            items {
              id
              matterNumber
              description
              status
            }
          }
        }
        """
        LOG.info("[smokeball] list_matters: querying GraphQL")
        data = self._gql(q)
        items = data.get("data", {}).get("matters", {}).get("items", [])
        LOG.info("[smokeball] list_matters: got %d matters", len(items))
        return items[:limit]

    def find_matter_by_number(self, matter_number: str) -> dict | None:
        """Find a matter by its human-readable number (e.g. '2026-03/2313').

        Checks the local mm-browsematters.db first (always complete), then
        falls back to the GraphQL browse API (may be paginated/incomplete).
        """
        LOG.info("[smokeball] find_matter_by_number: looking up '%s'", matter_number)

        # ── Local browse DB (fast, complete) ─────────────────────────────────
        if BROWSE_DB.exists():
            try:
                con = sqlite3.connect(str(BROWSE_DB))
                rows = con.execute(
                    "SELECT id, view FROM data WHERE type='browse_matters_matter' AND view LIKE ?",
                    (f"%{matter_number}%",)
                ).fetchall()
                con.close()
                for row_id, view in rows:
                    if matter_number in view:
                        _dbg("find_matter_local_hit", matter_number=matter_number, matter_id=row_id)
                        LOG.info("[smokeball] found matter in local DB: id=%s", row_id)
                        return {"id": row_id, "matterNumber": matter_number}
                _dbg("find_matter_local_miss", matter_number=matter_number)
            except Exception as exc:
                LOG.warning("[smokeball] browse DB lookup failed: %s", exc)

        # ── GraphQL fallback ──────────────────────────────────────────────────
        matters = self.list_matters(limit=500)
        for m in matters:
            if m.get("matterNumber") == matter_number:
                LOG.info("[smokeball] found matter via GraphQL: id=%s", m.get("id"))
                return m
        LOG.warning("[smokeball] matter '%s' not found in local DB or %d GQL results",
                    matter_number, len(matters))
        _dbg("find_matter_not_found", matter_number=matter_number, gql_count=len(matters))
        return None

    def get_matter(self, matter_id: str) -> dict | None:
        """Fetch full matter detail by UUID."""
        q = """
        query GetMatter($id: UUID!) {
          matter(id: $id) {
            id
            matterNumber
            description
            status
          }
        }
        """
        data = self._gql(q, {"id": matter_id})
        return data.get("data", {}).get("matter")

    # ── REST write path ───────────────────────────────────────────────────────

    def _rest_patch_conveyancing(self, matter_id: str, payload: dict) -> bool:
        """Try PATCH to the matter conveyancing REST endpoint. Returns True on success."""
        # The Smokeball appgw REST API path pattern for conveyancing details
        candidates = [
            f"{SMOKEBALL_APPGW}/matters/{self.firm_id}/{matter_id}/conveyancingdetails",
            f"{SMOKEBALL_APPGW}/matters/{matter_id}/conveyancingdetails",
            f"{SMOKEBALL_APPGW}/mattermanagement/{self.firm_id}/{matter_id}/conveyancing",
        ]
        for url in candidates:
            try:
                r = self._http.patch(url, json=payload)
                if r.status_code in (200, 201, 204):
                    LOG.info("REST PATCH succeeded at %s", url)
                    return True
                LOG.debug("REST PATCH %s → %d", url, r.status_code)
            except Exception as exc:
                LOG.debug("REST PATCH %s error: %s", url, exc)
        return False

    def _rest_put_layout_values(self, matter_id: str, kv: dict[str, str]) -> bool:
        """Try PUT layout-values endpoint (key/value pairs). Returns True on success."""
        body = [{"key": k, "value": v} for k, v in kv.items()]
        candidates = [
            f"{SMOKEBALL_APPGW}/mattermanagement/{self.firm_id}/{matter_id}/layoutvalues",
            f"{SMOKEBALL_APPGW}/matters/{self.firm_id}/{matter_id}/layoutvalues",
        ]
        for url in candidates:
            try:
                r = self._http.put(url, json=body)
                if r.status_code in (200, 201, 204):
                    LOG.info("Layout-values PUT succeeded at %s", url)
                    return True
                LOG.debug("Layout-values PUT %s → %d", url, r.status_code)
            except Exception as exc:
                LOG.debug("Layout-values PUT %s error: %s", url, exc)
        return False

    # ── SQLite fallback write path ─────────────────────────────────────────────

    def _sqlite_write_layouts(self, matter_id: str, fields: "S32Fields") -> bool:
        """Write S32 fields directly to layouts.db by patching the Layouts.Matter XML."""
        LOG.info("[smokeball] sqlite_write: matter_id=%s  db=%s", matter_id, LAYOUTS_DB)
        if not LAYOUTS_DB.exists():
            LOG.warning("[smokeball] layouts.db not found at %s", LAYOUTS_DB)
            return False
        kv = fields.to_layout_key_values()
        if not kv:
            LOG.warning("[smokeball] no fields to write for matter %s", matter_id)
            return False
        LOG.info("[smokeball] sqlite_write: writing %d keys: %s", len(kv), list(kv.keys()))
        try:
            con = sqlite3.connect(str(LAYOUTS_DB))
            row = con.execute(
                "SELECT view FROM data WHERE id=? AND type='Layouts.Matter'",
                (matter_id,)
            ).fetchone()
            if not row:
                con.close()
                LOG.error("[smokeball] no Layouts.Matter row for matter_id=%s", matter_id)
                return False
            xml_len_before = len(row[0])
            xml_str = _patch_layout_xml(row[0], kv)
            LOG.info(
                "[smokeball] sqlite_write: xml size before=%d after=%d (delta=%+d)",
                xml_len_before, len(xml_str), len(xml_str) - xml_len_before,
            )
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            with con:
                con.execute(
                    "UPDATE data SET view=?, timestamp=? WHERE id=? AND type='Layouts.Matter'",
                    (xml_str, now, matter_id),
                )
            con.close()
            LOG.info("[smokeball] sqlite_write: SUCCESS — %d fields written for matter %s", len(kv), matter_id)
            return True
        except Exception as exc:
            LOG.error("[smokeball] sqlite_write FAILED: %s", exc, exc_info=True)
            return False

    # ── Public API ────────────────────────────────────────────────────────────

    def push_s32_fields(
        self,
        matter_id: str,
        fields: "S32Fields",
        *,
        use_sqlite_fallback: bool = True,
    ) -> "PushResult":
        """Push S32 conveyancing fields to a matter.

        Tries in order:
          1. REST PATCH to the conveyancing detail endpoint
          2. REST PUT to the layout-values endpoint
          3. (optional) Direct SQLite write

        Returns a PushResult describing what succeeded or failed.
        """
        kv = fields.to_layout_key_values()
        result = PushResult(matter_id=matter_id, fields_attempted=list(kv.keys()))

        # Try structured REST endpoint
        rest_payload = fields.to_rest_payload()
        if self._rest_patch_conveyancing(matter_id, rest_payload):
            result.method = "rest_patch"
            result.success = True
            return result

        # Try layout-values endpoint
        if self._rest_put_layout_values(matter_id, kv):
            result.method = "layout_values"
            result.success = True
            return result

        # Local layouts.db write (primary offline path)
        if use_sqlite_fallback:
            if self._sqlite_write_layouts(matter_id, fields):
                result.method = "layouts_db"
                result.success = True
                result.warning = (
                    "Wrote to local layouts.db. triConvey will reflect the change "
                    "immediately on next form refresh; the Windows Service syncs to cloud shortly after."
                )
                return result

        result.success = False
        result.error = (
            "All write paths failed (REST 403/404, SQLite unavailable). "
            "triConvey.exe must be open and the Windows Service must be running."
        )
        return result


# ── S32Fields data model ──────────────────────────────────────────────────────

# ── Direct question-id → layouts.db key path table ───────────────────────────
#
# Maps every canonical question_id directly to the Layouts.Matter XML key.
# Monetary fields are suffixed with ":amount" so _clean_amount() is applied.
# Boolean fields come through as True/False and are written as "True"/"False".
#
# NOTE: PlanningSchemeOvelay is a confirmed Smokeball typo (missing 'r').
#
_S32_KEY: dict[str, str] = {
    # ── Outgoings (row 1 = council, 2 = water, 3 = land tax, 4 = OC fees) ──
    "sec32_1.1_outgoing_1_authority": "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails/Authority",
    "sec32_1.1_outgoing_1_amount":    "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails/Amount:amount",
    "sec32_1.1_outgoing_2_authority": "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[1]/Authority",
    "sec32_1.1_outgoing_2_amount":    "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[1]/Amount:amount",
    "sec32_1.1_outgoing_3_authority": "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[2]/Authority",
    "sec32_1.1_outgoing_3_amount":    "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[2]/Amount:amount",
    "sec32_1.1_outgoing_4_authority": "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[3]/Authority",
    "sec32_1.1_outgoing_4_amount":    "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountDetails[3]/Amount:amount",
    # ── Outgoings policy checkboxes & total ─────────────────────────────────
    "policy_1_amounts_are_checked":          "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAmountsAre:bool",
    "policy_1_certs_attached":               "Matter/PropertyDetailsRealEstates/Section32/OutgoingsAreContainedInAttachedCertificates:bool",
    "policy_1_total_does_not_exceed":        "Matter/PropertyDetailsRealEstates/Section32/OutgoingsTotalAmountDoesNotExceed:bool",
    "policy_1_total_does_not_exceed_amount": "Matter/PropertyDetailsRealEstates/Section32/OutgoingsTotalAmountDoesNotExceedAmount:amount",
    "sec32_1.2_no_other_amounts":            "Matter/PropertyDetailsRealEstates/Section32/OutgoingsNoAmountsPurchaserLiableOtherThan:bool",
    # ── Contract type ────────────────────────────────────────────────────────
    "sec32_1.3_terms_contract":           "Matter/PropertyDetailsRealEstates/Section32/TermsContractApplies:bool",
    "sec32_1.4_sale_subject_to_mortgage": "Matter/PropertyDetailsRealEstates/Section32/SaleSubjectToMortgageApplies:bool",
    # ── Insurance (damage/destruction) ──────────────────────────────────────
    "sec32_2.1_insurance_company":       "Matter/PropertyDetailsRealEstates/Section32/DamageInsuranceCompanyName",
    "sec32_2.1_insurance_policy_no":     "Matter/PropertyDetailsRealEstates/Section32/DamageInsurancePolicyNo",
    "sec32_2.1_insurance_type":          "Matter/PropertyDetailsRealEstates/Section32/DamageInsuranceTypeOfPolicy",
    "sec32_2.1_insurance_amount_insured":"Matter/PropertyDetailsRealEstates/Section32/DamageInsuranceAmountInsured:amount",
    # ── Restrictions / title (Sec. 32 (2)) ──────────────────────────────────
    "policy_2_title_in_attached":  "Matter/PropertyDetailsRealEstates/Section32/RestrictionsCopiesOfTitleDocumentsAttached:bool",
    "policy_2_failure_checked":    "Matter/PropertyDetailsRealEstates/Section32/RestrictionsParticularsOfAnyExistingFailureToComply:bool",
    "policy_2_failure_text":       "Matter/PropertyDetailsRealEstates/Section32/RestrictionsParticularsOfAnyExistingFailureToComplyDetails",
    "sec32_3.1_easement_description":  "Matter/PropertyDetailsRealEstates/Section32/RestrictionsDetails",
    "sec32_3.1_easement_failure_text": "Matter/PropertyDetailsRealEstates/Section32/RestrictionsParticularsOfAnyExistingFailureToComplyDetails",
    # ── Road, Bushfire, Planning (Sec. 32 (2)) ───────────────────────────────
    "sec32_3.2_no_road_access":          "Matter/PropertyDetailsRealEstates/Section32/NoRoadAccess:bool",
    "sec32_3.3_bushfire_prone":          "Matter/PropertyDetailsRealEstates/Section32/LandIsInDesignatedBushfireProneArea:bool",
    "sec32_3.4_planning_scheme":         "Matter/PropertyDetailsRealEstates/Section32/PlanningSchemeName",
    "sec32_3.4_responsible_authority":   "Matter/PropertyDetailsRealEstates/Section32/PlanningSchemeResponsibleAuthorityName",
    "sec32_3.4_planning_zone":           "Matter/PropertyDetailsRealEstates/Section32/PlanningSchemeZoning",
    "sec32_3.4_planning_overlay_name":   "Matter/PropertyDetailsRealEstates/Section32/PlanningSchemeOvelay",   # Smokeball typo: Ovelay
    "policy_2_planning_cert_attached":   "Matter/PropertyDetailsRealEstates/Section32/PlanningSchemeCertificateAttached:bool",
    # ── Notices & Building Permits (Sec. 32 (3)) ────────────────────────────
    "sec32_4.1_notices_text":            "Matter/PropertyDetailsRealEstates/Section32/NoticesDetails",
    "sec32_5.1_building_permits_text":   "Matter/PropertyDetailsRealEstates/Section32/BuildingPermitsDetailsApply:bool",
    "sec32_5.1_building_permits_details":"Matter/PropertyDetailsRealEstates/Section32/BuildingPermitsDetails",
    # ── Services — tick means NOT connected (Sec. 32 (4)) ───────────────────
    "sec32_8_electricity_not_connected": "Matter/PropertyDetailsRealEstates/Section32/ServicesElectricitySupply:bool",
    "sec32_8_gas_not_connected":         "Matter/PropertyDetailsRealEstates/Section32/ServicesGasSupply:bool",
    "sec32_8_water_not_connected":       "Matter/PropertyDetailsRealEstates/Section32/ServicesWaterSupply:bool",
    "sec32_8_sewerage_not_connected":    "Matter/PropertyDetailsRealEstates/Section32/ServicesSewerage:bool",
    "sec32_8_telephone_not_connected":   "Matter/PropertyDetailsRealEstates/Section32/ServicesTelephone:bool",
    # ── GAIC & Owners Corporation (Sec. 32 (4)) ──────────────────────────────
    "sec32_7_gaic_applies":     "Matter/PropertyDetailsRealEstates/Section32/GAICApplies:bool",
    "sec32_oc_inactive":        "Matter/PropertyDetailsRealEstates/Section32/OwnersCorporationInactive:bool",
    "policy_4_oc_cert_attached":"Matter/PropertyDetailsRealEstates/Section32/OwnersCorporationCertificateAttached:bool",
    # ── Attachments (Sec. 32 (6)) ────────────────────────────────────────────
    "policy_6_attachments": "Matter/PropertyDetailsRealEstates/Section32/Attachments",
    # ── ConveyancingDetails (sale/settlement) ────────────────────────────────
    "vendor_sale_price":  "Matter/ConveyancingDetails/PurchasePrice:amount",
    "settlement_date":    "Matter/ConveyancingDetails/SettlementDateDetails/Date",
    "contract_date":      "Matter/ConveyancingDetails/ContractDate",
    "finance_amount":     "Matter/ConveyancingDetails/FinanceAmountRequired:amount",
    "finance_date":       "Matter/ConveyancingDetails/FinanceApprovalDueDate",
    "finance_lender":     "Matter/ConveyancingDetails/FinanceLenderName",
    "deposit_amount":     "Matter/ConveyancingDetails/DepositTotalAmount:amount",
    "deposit_initial":    "Matter/ConveyancingDetails/DepositInitialAmount:amount",
    "special_conditions": "Matter/ConveyancingDetails/SpecialConditions/Title",
}


class S32Fields:
    """Section 32 (Vendor's Statement) conveyancing fields.

    Thin wrapper around _S32_KEY — maps question_id values directly to
    layouts.db key paths with no intermediate attribute layer.
    """

    def __init__(self, kv: dict[str, str]):
        """*kv* is already the resolved {layouts_key: value} dict."""
        self._kv = kv

    def to_layout_key_values(self) -> dict[str, str]:
        return dict(self._kv)

    def to_rest_payload(self) -> dict:
        return {"layoutValues": [{"key": k, "value": v} for k, v in self._kv.items()]}

    @classmethod
    def from_run_answers(cls, answers: dict[str, Any]) -> "S32Fields":
        """Build S32Fields directly from a TriConvey Agent run answers dict.

        Iterates every question_id in the answers that appears in _S32_KEY,
        converts the value (bool → "True"/"False", monetary → cleaned number),
        and returns a ready-to-write {layouts.db_key: value} mapping.

        Also handles volume_folio → TitleParticulars split.
        """
        kv: dict[str, str] = {}

        def _to_str(qid: str, raw: Any, layout_path: str) -> str | None:
            if raw is None:
                return None
            # Boolean fields: normalise any truthy/falsy representation → "True"/"False"
            if layout_path.endswith(":bool"):
                if isinstance(raw, bool):
                    return "True" if raw else "False"
                s2 = str(raw).strip().lower()
                if s2 in ("1", "1.0", "1.00", "true", "yes"):
                    return "True"
                if s2 in ("0", "0.0", "0.00", "false", "no", ""):
                    return "False"
                return None
            if isinstance(raw, bool):
                return "True" if raw else "False"
            s = str(raw).strip()
            if not s:
                return None
            # Monetary: strip $ and commas
            if layout_path.endswith(":amount"):
                return _clean_amount(s)
            return s

        # Only push fields whose question_id is actually present in this run's
        # custom policy answers — never push fields the policy didn't ask about.
        for qid, raw in answers.items():
            layout_path = _S32_KEY.get(qid)
            if layout_path is None:
                continue   # question not mapped to a Smokeball field
            # Strip type tag to get the real XML key path
            if layout_path.endswith(":amount"):
                actual_path = layout_path[:-7]
            elif layout_path.endswith(":bool"):
                actual_path = layout_path[:-5]
            else:
                actual_path = layout_path
            val = _to_str(qid, raw, layout_path)
            if val is not None:
                kv[actual_path] = val

        # ── Title: volume/folio from matter metadata ──────────────────────
        vf = answers.get("volume_folio")
        if vf and "/" in str(vf):
            parts = str(vf).split("/", 1)
            vol, fol = parts[0].strip(), parts[1].strip()
            if vol:
                kv.setdefault("Matter/PropertyDetailsRealEstates/TitleParticulars/VolumeOrBook", vol)
            if fol:
                kv.setdefault("Matter/PropertyDetailsRealEstates/TitleParticulars/FolioOrNumber", fol)

        return cls(kv)


# ── PushResult ────────────────────────────────────────────────────────────────

class PushResult:
    def __init__(self, matter_id: str, fields_attempted: list[str]):
        self.matter_id = matter_id
        self.fields_attempted = fields_attempted
        self.success = False
        self.method: str | None = None
        self.warning: str | None = None
        self.error: str | None = None

    def to_dict(self) -> dict:
        return {
            "matter_id": self.matter_id,
            "success": self.success,
            "method": self.method,
            "fields_pushed": len(self.fields_attempted),
            "warning": self.warning,
            "error": self.error,
        }


# ── layouts.db XML patcher ───────────────────────────────────────────────────���

def _clean_amount(value: Any) -> str | None:
    """Strip currency formatting so triConvey can parse the number.

    Accepts "$3,822.78", "3,822.78", "3822", 3822.78 etc.
    Returns plain "3822.78" or None if nothing parseable.
    """
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        float(s)   # validate it parses
        return s
    except ValueError:
        return None


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _patch_layout_xml(xml_str: str, kv: dict[str, str]) -> str:
    """Update/insert key/value pairs inside a LayoutMatter XML string.

    Strategy:
      1. For keys that already exist: regex-replace the <Value> in place (on the
         full string, so updates are cumulative).
      2. For missing keys: re-split the updated string, find the non-removed
         LayoutMatterItem with the most values, and insert before its </Values>.

    XML structure per item:
      <LayoutMatterItem>
        <Values>
          <LayoutValue><Key>…</Key><Value>…</Value></LayoutValue>
          …
        </Values>
        <Events>…</Events>
        <IsRemoved>false</IsRemoved>
      </LayoutMatterItem>
    """
    item_split_re = re.compile(r'(<LayoutMatterItem>)', re.DOTALL)

    missing_lv_blocks: list[str] = []

    # Pass 1: update existing keys in-place on the monolithic string.
    #
    # Handles:
    #   <Value>text</Value>  — normal existing value (replace text)
    #   <Value />            — Smokeball self-closing empty default (replace whole tag)
    #   <Value></Value>      — explicit empty (covered by first form)
    #   Duplicate keys       — keep first occurrence updated, delete the rest
    #
    # Matching the COMPLETE LayoutValue block (not just the Value part) lets us
    # both update and deduplicate in a single regex pass, and avoids interpreting
    # backslashes in the replacement string as regex back-references.
    for key, value in kv.items():
        safe = _xml_escape(value)
        esc_key = re.escape(key)
        new_block = f'<LayoutValue><Key>{key}</Key><Value>{safe}</Value></LayoutValue>'
        # Pattern matches the COMPLETE LayoutValue block for this key.
        # Smokeball stores empty fields in three ways:
        #   <Value>text</Value>  — non-empty value
        #   <Value />            — self-closing empty
        #   (no Value element)   — completely absent, e.g. <LayoutValue><Key>…</Key></LayoutValue>
        lv_block_pattern = (
            r'<LayoutValue><Key>' + esc_key + r'</Key>'
            r'(?:\s*<Value>[^<]*</Value>|\s*<Value\s*/>|)</LayoutValue>'
        )
        first_seen: list[bool] = [False]

        def _replace(m: "re.Match[str]", _fseen: list[bool] = first_seen, _nb: str = new_block) -> str:
            if not _fseen[0]:
                _fseen[0] = True
                return _nb   # update first occurrence in-place
            return ''        # delete every subsequent duplicate

        new_xml = re.sub(lv_block_pattern, _replace, xml_str)
        if first_seen[0]:  # at least one occurrence was found → key exists
            xml_str = new_xml
        else:
            missing_lv_blocks.append(new_block)

    # Pass 2: insert new keys into the best active item (re-split the updated string)
    if missing_lv_blocks:
        parts = item_split_re.split(xml_str)
        best_idx: int | None = None
        best_count = -1
        for i in range(1, len(parts), 2):
            if i + 1 >= len(parts):
                break
            content = parts[i + 1]
            removed_m = re.search(r'<IsRemoved>([^<]+)</IsRemoved>', content)
            if removed_m and removed_m.group(1).strip().lower() == "true":
                continue
            n_vals = content.count('<LayoutValue>')
            if n_vals > best_count:
                best_count = n_vals
                best_idx = i + 1

        if best_idx is not None:
            insertion = "".join(missing_lv_blocks)
            # Use lambda to prevent re.sub from treating backslashes in
            # insertion as escape sequences / back-references.
            parts[best_idx] = re.sub(
                r'</Values>',
                lambda m, ins=insertion: ins + m.group(0),
                parts[best_idx],
                count=1,
            )
            xml_str = "".join(parts)

    return xml_str


# ── Convenience function ──────────────────────────────────────────────────────

def push_s32_to_matter(
    matter_number: str,
    answers: dict[str, Any],
    *,
    token: str | None = None,
    firm_id: str = FIRM_ID,
) -> dict:
    """High-level helper: look up a matter by number and push S32 fields from answers."""
    _dbg("push_start", matter_number=matter_number, answer_count=len(answers))
    LOG.info("[smokeball] push_s32_to_matter: matter_number=%s  answers_keys=%s",
             matter_number, list(answers.keys())[:10])
    try:
        client = SmokeballClient(token=token, firm_id=firm_id)
    except RuntimeError as exc:
        LOG.error("[smokeball] SmokeballClient init failed: %s", exc)
        _dbg("push_init_failed", error=exc)
        return {"success": False, "error": str(exc)}

    matter = client.find_matter_by_number(matter_number)
    if not matter:
        _dbg("push_matter_not_found", matter_number=matter_number)
        return {
            "success": False,
            "error": f"Matter '{matter_number}' not found in Smokeball browse list",
        }

    fields = S32Fields.from_run_answers(answers)
    kv = fields.to_layout_key_values()
    LOG.info("[smokeball] S32Fields resolved %d layout keys from answers", len(kv))

    # Log every question being pushed with its resolved value
    _dbg("push_fields_resolved", matter_id=matter["id"], field_count=len(kv))
    for layout_key, val in sorted(kv.items()):
        short = layout_key.split("/")[-1]
        _dbg("push_field", key=short, value=val, full_key=layout_key)

    result = client.push_s32_fields(matter["id"], fields)
    LOG.info("[smokeball] push result: success=%s  method=%s  error=%s",
             result.success, result.method, result.error)
    _dbg("push_complete", success=result.success, method=result.method, error=result.error)
    return result.to_dict()
