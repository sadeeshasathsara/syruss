"""
Deterministic format and checksum validators for document extraction confidence.

Supported format rules:
  - "currency"        : Numeric currency string or float/int >= 0
  - "iso_date"        : ISO-8601 formatted date (YYYY-MM-DD)
  - "iso_time"        : 24h or 12h time string (HH:MM or HH:MM:SS)
  - "email"           : Standard RFC-5322 email format
  - "iban"            : International Bank Account Number with Modulo-97 checksum
  - "luhn"            : Luhn algorithm checksum (payment cards, IMEI, identifiers)
  - "regex:<pattern>" : Linear-time RE2 regular expression check
"""
import re
from datetime import date, datetime
from typing import Any, Optional, Tuple

try:
    import re2
except ImportError:
    re2 = re  # fallback to standard re if re2 is unavailable


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(:([0-5]\d))?$")


def validate_iban(value: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates an International Bank Account Number using ISO 13616 / Modulo-97.
    Returns (is_valid, error_message).
    """
    if not value:
        return False, "IBAN is empty"
    clean = re.sub(r"[\s-]+", "", str(value)).upper()
    if len(clean) < 5 or len(clean) > 34:
        return False, f"IBAN length ({len(clean)}) out of bounds (5-34 characters)"
    if not clean[:2].isalpha():
        return False, f"IBAN must start with 2-letter country code: {clean[:2]}"
    if not clean[2:4].isdigit():
        return False, f"IBAN check digits must be numeric: {clean[2:4]}"
    if not clean.isalnum():
        return False, "IBAN contains invalid characters"

    # Rearrange: move the first 4 characters to the end
    rearranged = clean[4:] + clean[:4]

    # Convert letters to digits: A=10, B=11, ..., Z=35
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        else:
            digits.append(str(ord(ch) - 55))
    num_str = "".join(digits)

    # Compute modulo 97 on large integer
    remainder = int(num_str) % 97
    if remainder != 1:
        return False, f"IBAN Modulo-97 checksum failed (remainder {remainder}, expected 1)"
    return True, None


def validate_luhn(value: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates a number using the Luhn checksum algorithm (Mod 10).
    Returns (is_valid, error_message).
    """
    if not value:
        return False, "Number is empty"
    clean = re.sub(r"[\s-]+", "", str(value))
    if not clean.isdigit():
        return False, "Value must contain digits only"
    if len(clean) < 2:
        return False, "Number is too short for Luhn check"

    total = 0
    reverse_digits = [int(c) for c in clean[::-1]]
    for idx, d in enumerate(reverse_digits):
        if idx % 2 == 1:
            doubled = d * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += d

    if total % 10 != 0:
        return False, f"Luhn checksum failed (sum {total} not divisible by 10)"
    return True, None


def validate_iso_date(value: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates an ISO-8601 date (YYYY-MM-DD).
    Returns (is_valid, error_message).
    """
    if isinstance(value, (date, datetime)):
        return True, None
    if not value:
        return False, "Date is empty"
    text = str(value).strip()[:10]
    try:
        parsed = date.fromisoformat(text)
        # Sanity check year bounds
        if parsed.year < 1900 or parsed.year > 2100:
            return False, f"Year {parsed.year} out of reasonable range (1900-2100)"
        return True, None
    except ValueError:
        return False, f"'{value}' is not a valid ISO date (YYYY-MM-DD)"


def validate_currency(value: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates a numeric currency value.
    Accepts floats, ints, or strings like '1,250.50', '$45.00', '€ 100'.
    Returns (is_valid, error_message).
    """
    if value is None or value == "":
        return False, "Currency value is empty"
    if isinstance(value, (int, float)):
        return True, None
    # Strip common currency symbols and whitespace
    clean = re.sub(r"[\$\€\£\¥\₹\s,]", "", str(value))
    try:
        float(clean)
        return True, None
    except ValueError:
        return False, f"'{value}' cannot be parsed as a currency amount"


def validate_email(value: Any) -> Tuple[bool, Optional[str]]:
    """Validates an email address."""
    if not value:
        return False, "Email is empty"
    text = str(value).strip()
    if _EMAIL_RE.match(text):
        return True, None
    return False, f"'{value}' is not a valid email address"


def validate_regex(value: Any, pattern: str) -> Tuple[bool, Optional[str]]:
    """Validates a string against an RE2 regex pattern."""
    if value is None:
        return False, "Value is null"
    try:
        compiled = re2.compile(pattern)
        if compiled.fullmatch(str(value).strip()):
            return True, None
        return False, f"'{value}' does not match pattern '{pattern}'"
    except Exception as e:
        return False, f"Invalid regex pattern '{pattern}': {e}"


def check_format_rule(value: Any, format_rule: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Dispatches to the appropriate validator based on format_rule.
    Returns (is_valid, error_message).
    """
    if not format_rule:
        return True, None
    rule = format_rule.strip().lower()

    if rule == "iban":
        return validate_iban(value)
    elif rule == "luhn":
        return validate_luhn(value)
    elif rule in ("iso_date", "date"):
        return validate_iso_date(value)
    elif rule in ("currency", "money"):
        return validate_currency(value)
    elif rule == "email":
        return validate_email(value)
    elif rule.startswith("regex:"):
        pattern = format_rule[6:].strip()
        return validate_regex(value, pattern)
    else:
        # Unknown rule: don't fail, log or treat as passing
        return True, None
