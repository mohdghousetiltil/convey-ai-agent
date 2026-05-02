# Council Rates Certificate Extractor - Knox Format Fix

## Problem
The council rates certificate extractor was not capturing the annual amount from Knox City Council Land Information Certificates. The document showed:
- **Sub total**: $2,505.90 (annual levy before payments)
- **Less Payments received**: $-1,879.90
- **Total balance payable**: $626.00

The extractor was returning `None` with confidence 0.30 instead of the correct annual amount of $2,505.90.

## Root Cause
The extractor was looking for specific keywords to identify the outstanding balance:
- "Total Rates & Charges Due" (Brimbank format)
- "TOTAL OUTSTANDING" (Ballarat format)
- Generic patterns like "Balance Due/Outstanding/Owing"

Knox uses **"Total balance payable"** which wasn't in the pattern list.

Additionally, Knox documents have a **"Sub total"** line that represents the annual levy before payments, which wasn't being captured as a fallback pattern.

## Solution

### 1. Updated Outstanding Amount Pattern
Added "Total balance payable" to the `_OUTSTANDING_KW_RE` regex pattern:

```python
_OUTSTANDING_KW_RE = re.compile(
    r"Total\s+Rates?\s*(?:&|and)\s*Charges?\s+Due"       # Brimbank
    r"|TOTAL\s+OUTSTANDING"                               # Ballarat
    r"|TOTAL\s+(?:RATES?\s+)?DUE"                        # generic
    r"|Total\s+balance\s+payable"                         # Knox ← NEW
    r"|Balance\s+(?:Due|Outstanding|Owing|Payable)"       # generic
    r"|Outstanding\s+(?:Balance|Amount|Owing)",           # generic
    re.IGNORECASE,
)
```

### 2. Added Sub Total Pattern
Created a new regex pattern to capture the "Sub total" line:

```python
_SUB_TOTAL_RE = re.compile(
    r"Sub\s+total\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)
```

### 3. Added Sub Total Extraction Logic
Added extraction logic in `_extract_annual_amount()` to use the "Sub total" line as a high-confidence (0.96) fallback when paid+outstanding calculation isn't available:

```python
# ── 2b. "Sub total $2,505.90" — Knox direct annual levy line (before payments)
m = _SUB_TOTAL_RE.search(text)
if m:
    digits = m.group(1)
    try:
        val = _parse_dollar(digits)
        if val > 0:
            return [
                _make_fact(
                    doc, P.RATES_COUNCIL_ANNUAL, _fmt(val),
                    _compact(m.group(0)),
                    confidence=0.96,
                    notes="Annual council rates — 'Sub total' line (Knox LIC format, annual levy before payments)",
                )
            ]
    except ValueError:
        pass
```

## Extraction Priority
The updated extraction now follows this priority order:

1. **Paid + Outstanding** (conf 0.97) - Primary method
   - Brimbank: "Less Payments: -$X" + "Total Rates & Charges Due: $Y"
   - Ballarat: "Less Payments Received-X" + "TOTAL OUTSTANDING Y"
   - Knox: "Less Payments received $-X" + "Total balance payable $Y" ← **NOW WORKS**
   
2. **Direct Annual Levy Lines** (conf 0.96)
   - Knox: "Sub total $2,505.90" ← **NEW**
   - Ballarat: "Current Total:2,349.87"
   
3. **Explicit Annual Labels** (conf 0.88)
   - "Annual Council Rates and Charges YYYY/YYYY $X"
   
4. **Generic Annual Labels** (conf 0.84)
   - "Total annual rates/charges"
   
5. **Sum of Date Levied Lines** (conf 0.88)
   
6. **Instalment × Multiplier** (conf 0.80-0.85)

## Testing
Created comprehensive test suite in `tests/canonical/test_council_rates_certificate_extractor.py`:

- ✅ `test_knox_city_council_format` - Tests paid+outstanding calculation
- ✅ `test_knox_sub_total_fallback` - Tests Sub total line extraction
- ✅ `test_brimbank_format` - Ensures existing Brimbank format still works
- ✅ `test_ballarat_format` - Ensures existing Ballarat format still works
- ✅ `test_no_match_returns_empty` - Ensures non-council docs are ignored

All tests pass successfully.

## Verification
Tested with actual Knox City Council document:
- **Input**: Knox Land Information Certificate with Sub total $2,505.90
- **Output**: 
  - Council Name: "Knox City Council" (confidence: 0.97)
  - Annual Amount: "$2,505.90" (confidence: 0.97)
  - Calculation: $1,879.90 (paid) + $626.00 (outstanding) = $2,505.90 ✓

## Files Modified
1. `src/triconvey_agent/canonical/extractors/council_rates_certificate.py`
   - Updated `_OUTSTANDING_KW_RE` pattern
   - Added `_SUB_TOTAL_RE` pattern
   - Added Sub total extraction logic in `_extract_annual_amount()`
   - Updated documentation

2. `tests/canonical/test_council_rates_certificate_extractor.py` (NEW)
   - Comprehensive test suite for all council formats

## Impact
- ✅ Knox City Council documents now extract correctly
- ✅ All existing council formats (Brimbank, Ballarat, Indigo) continue to work
- ✅ No breaking changes to existing functionality
- ✅ Improved robustness with additional fallback pattern
