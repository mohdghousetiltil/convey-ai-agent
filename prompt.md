we need to design a pipeline that always extracts council name and amount from the land certificate and water authority name and amount from water information certificate if you see land certificate document - it has a billing table we need to extract annual amount from this logic either the annual amount is directly mentioned or with this logic payed amount + owing amount payed amount will be in - in most cases, and owing amount will be highlighted from above example Bass Coast Shire Council annual amount is directly mentioned as Rates & Charges Sub Total for 2025/26 $1,009.89 from baw baw council annual amount is again directly mentioned like this Current Years Rates and Charges Sub Total 3,341.70 but for maroondah council the annual amount logic is different payed + outstanding + rebates etc 1,150.35 + 1,148.00 + 0(rebates) my council rates certificate pyhton file is struggling to make this improvements can we fix it





tackle it as  classification → authority → amount strategy, not one giant regex.

tackle it as a layout-agnostic extractor, not council-by-council hardcoding.



Strategy



For land/council certificates, extract in this priority order:



Council name

Prefer text like Bass Coast Shire Council, Baw Baw Shire Council, Maroondah City Council.

Fallback to email/domain: bawbawshire.vic.gov.au.

Last fallback: filename.

Annual council amount



First look for explicit annual subtotal rows:



Rates & Charges Sub Total for 2025/26 $1,009.89

Current Years Rates and Charges Sub Total 3,341.70

Sub Total $...

Current Total $...



If no direct subtotal exists, calculate:



annual = abs(payments / rebates / credits) + outstanding balance



For Maroondah-style tables, better logic is:



annual = sum(LEVIED column)



or, if OCR/table parsing is messy:



annual = abs(Less Payments) + Assessment Total + abs(Rebates)

Avoid using final balance as annual

Total Balance Owing, Assessment Total, Total Outstanding is usually settlement balance, not annual amount.



For water certificates:



Extract authority name from known authority list + logo/header text:



KNOWN_WATER_AUTHORITIES = [

    "Westernport Water",

    "Yarra Valley Water",

    "South East Water",

    "Greater Western Water",

    ...

]

Annual amount priority:



Direct annual total:



Total annual charges $...

Annual charge column: sum annual column.



Current period charges: annualise by period length:



if period_days <= 100: multiplier = 4

elif period_days <= 200: multiplier = 2

else: multiplier = 1

annual = current_period_total * multiplier



If mixed rows exist, like Westernport:



Waterways annual row 01/07/2025 to 30/06/2026 $68.60

Sewer quarterly row 19/04/2026 to 18/07/2026 $89.82

Water quarterly row 19/04/2026 to 18/07/2026 $56.60



calculate per row:



68.60 × 1 + 89.82 × 4 + 56.60 × 4 = 654.28

Core code pattern

def money_to_float(value: str) -> float:

    return float(

        value.replace("$", "")

             .replace(",", "")

             .replace("−", "-")

             .strip()

    )





def fmt_money(value: float) -> str:

    return f"${value:,.2f}"





def period_multiplier(days: int | None) -> int:

    if days is None:

        return 4

    if days <= 100:

        return 4

    if days <= 200:

        return 2

    return 1

Council annual extractor

SUBTOTAL_PATTERNS = [

    r"Rates\s*&?\s*Charges\s+Sub\s+Total(?:\s+for\s+\d{4}/\d{2,4})?\s+\$?\s*([\d,]+\.\d{2})",

    r"Current\s+Years?\s+Rates\s+and\s+Charges\s+Sub\s+Total\s+\$?\s*([\d,]+\.\d{2})",

    r"\bSub\s+Total\b\s*:?\s*\$?\s*([\d,]+\.\d{2})",

    r"\bCurrent\s+Total\b\s*:?\s*\$?\s*([\d,]+\.\d{2})",

]



PAYMENT_PATTERNS = [

    r"Less\s+Payments?\s*(?:Received)?\s*-?\$?\s*([\d,]+\.\d{2})",

    r"Payments?\s+Received\s*-?\$?\s*([\d,]+\.\d{2})",

    r"Receipts\s+and\s+Adjustments\s*-?\$?\s*([\d,]+\.\d{2})",

]



REBATE_PATTERNS = [

    r"Pension\s+Rebate\s*-?\$?\s*([\d,]+\.\d{2})",

    r"Rebates?\s*-?\$?\s*([\d,]+\.\d{2})",

]



BALANCE_PATTERNS = [

    r"ASSESSMENT\s+TOTAL\s+\$?\s*([\d,]+\.\d{2})",

    r"TOTAL\s+BALANCE\s+OUTSTANDING\s+\$?\s*([\d,]+\.\d{2})",

    r"Total\s+Balance\s+Owing\s+\$?\s*([\d,]+\.\d{2})",

    r"TOTAL\s+OUTSTANDING\s+\$?\s*([\d,]+\.\d{2})",

]





def first_money(text: str, patterns: list[str]) -> float | None:

    for pattern in patterns:

        m = re.search(pattern, text, re.I)

        if m:

            return money_to_float(m.group(1))

    return None





def extract_council_annual(text: str) -> str | None:

    # 1. Prefer explicit annual subtotal

    subtotal = first_money(text, SUBTOTAL_PATTERNS)

    if subtotal and subtotal > 0:

        return fmt_money(subtotal)



    # 2. Maroondah-style: sum LEVIED column if table exists

    levied_total = extract_levied_table_total(text)

    if levied_total and levied_total > 0:

        return fmt_money(levied_total)



    # 3. Fallback: paid + outstanding + rebates

    payment = first_money(text, PAYMENT_PATTERNS) or 0

    rebate = first_money(text, REBATE_PATTERNS) or 0

    balance = first_money(text, BALANCE_PATTERNS)



    if balance is not None:

        annual = abs(payment) + abs(rebate) + balance

        if annual > 0:

            return fmt_money(annual)



    return None

Maroondah-style levied table helper

def extract_levied_table_total(text: str) -> float | None:

    if not re.search(r"RATES\s*&\s*CHARGES\s+LEVIED\s+REBATES\s+BALANCE", text, re.I):

        return None



    total = 0.0



    row_pattern = re.compile(

        r"^(General Rate|Waste Service Charge|State Government.*?Levy|Municipal Charge)"

        r"\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",

        re.I | re.M,

    )



    for match in row_pattern.finditer(text):

        total += money_to_float(match.group(2))



    return total or None

Water annual extractor

CHARGE_LINE_RE = re.compile(

    r"(?P<label>[A-Za-z][A-Za-z &/.-]+?)\s+"

    r"(?P<from>\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+to\s+"

    r"(?P<to>\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+"

    r"\$?\s*(?P<amount>[\d,]+\.\d{2})",

    re.I,

)





def parse_date(value: str) -> date:

    day, month, year = re.split(r"[/-]", value)

    return date(int(year), int(month), int(day))





def extract_water_annual(text: str) -> str | None:

    direct = first_money(text, [

        r"Total\s+annual\s+charges?\s+\$?\s*([\d,]+\.\d{2})",

        r"Annual\s+amount\s+\$?\s*([\d,]+\.\d{2})",

    ])

    if direct:

        return fmt_money(direct)



    total = 0.0



    for m in CHARGE_LINE_RE.finditer(text):

        start = parse_date(m.group("from"))

        end = parse_date(m.group("to"))

        days = (end - start).days + 1

        amount = money_to_float(m.group("amount"))

        total += amount * period_multiplier(days)



    if total > 0:

        return fmt_money(total)



    return None

Big rule



Do not make this council-specific.



Make it evidence-specific:



Direct annual subtotal > levied table total > paid + outstanding + rebates > instalment annualisation



That will handle new councils much better than adding one-off council names.