"""Pay and distances the way people type them, in any unit, currency and number style.

One parser for every place pay is typed (profile floor and target, role pay), so the rules
and the error messages are the same everywhere. Amounts are kept as full-time yearly
numbers (2,080 hours, 260 days, 52 weeks, 12 months), along with what the user typed.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass

PER_YEAR = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}
MAX_YEARLY = 10_000_000
EXAMPLES = "like 18/hr, 45k a year or 3,000 a month"

# Longest first, so "c$" wins over "$". "$" alone stays unspecified: it could be US, Canadian, Australian...
SYMBOLS = [("ca$", "CAD"), ("au$", "AUD"), ("nz$", "NZD"), ("us$", "USD"), ("hk$", "HKD"), ("c$", "CAD"), ("a$", "AUD"), ("s$", "SGD"),
           ("r$", "BRL"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("₹", "INR"), ("₩", "KRW"), ("₱", "PHP"), ("₦", "NGN"), ("₪", "ILS"),
           ("zł", "PLN"), ("$", "")]
CODES = ("usd", "eur", "gbp", "cad", "aud", "nzd", "chf", "jpy", "cny", "inr", "sgd", "hkd", "sek", "nok", "dkk", "pln", "czk", "huf",
         "mxn", "brl", "zar", "krw", "php", "ngn", "kes", "aed", "ils", "try", "idr", "myr", "thb", "vnd", "cop", "clp", "ars", "pen",
         "egp", "pkr", "bdt", "ron", "bgn", "isk", "twd", "sar", "qar")
WORDS = {"dollars": "", "dollar": "", "euros": "EUR", "euro": "EUR", "pounds": "GBP", "rupees": "INR"}
DISPLAY = {"": "$", "USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$", "NZD": "NZ$", "JPY": "¥", "INR": "₹"}

_BASE = {"hour": ("h", "hr", "hrs", "hour", "hours"), "day": ("day", "days", "d"), "week": ("wk", "wks", "week", "weeks", "w"),
         "month": ("mo", "mos", "mth", "month", "months", "m"), "year": ("y", "yr", "yrs", "year", "years", "annum")}
_EXTRA = {"hour": ("hourly", "ph", "p.h.", "an hour", "a hour"), "day": ("daily", "pd", "p.d.", "a day"),
          "week": ("weekly", "pw", "p.w.", "a week"), "month": ("monthly", "pm", "p.m.", "pcm", "a month"),
          "year": ("yearly", "annual", "annually", "pa", "p.a.", "a year", "per annum", "salary")}
PERIOD_WORDS: dict[str, str] = {}
for _period, _words in _BASE.items():
    for _w in _words:
        if _w != "m":  # a bare "m" is millions
            PERIOD_WORDS[_w] = _period
        for _prefix in ("/", "per ", "p/", "a ", "an ", "each "):
            PERIOD_WORDS[_prefix + _w] = _period
    for _w in _EXTRA[_period]:
        PERIOD_WORDS[_w] = _period

_NUM = r"\d[\d.,]*"
_MULT = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "mil": 1_000_000, "million": 1_000_000}
_PART = re.compile(rf"({_NUM})\s*(?:(k|thousand|million|mil|mm|m)(?![a-z]))?\s*(.*)")
_SEP = re.compile(r"\s*(?:-|–|—|\bto\b)\s*")


@dataclass
class Pay:
    yearly: int
    amount: float  # per period
    period: str  # hour, day, week, month or year
    currency: str  # ISO code, or "" when not given (or only "$")
    as_typed: str
    assumed: bool = False  # a bare number whose unit was guessed (7-200 hourly, 201-999 thousands)

    def as_dict(self) -> dict:
        return asdict(self)


def _number(text: str) -> float:
    t = text.rstrip(".,")
    if "," in t and "." in t:  # the last mark is the decimal one
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        groups = t.split(",")
        if len(groups) == 2 and 1 <= len(groups[1]) <= 2:
            t = groups[0] + "." + groups[1]  # 18,50 means 18.50
        elif all(len(g) == 3 for g in groups[1:]) or (len(groups[-1]) == 3 and all(len(g) == 2 for g in groups[1:-1])):
            t = "".join(groups)  # 1,000,000 or the Indian 1,00,000
        else:
            raise ValueError(text)
    elif t.count(".") > 1:
        groups = t.split(".")
        if not all(len(g) == 3 for g in groups[1:]):
            raise ValueError(text)
        t = "".join(groups)  # 1.234.567
    value = float(t)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _currency(s: str) -> tuple[str, str, str | None]:
    """(text without the currency, ISO code or "", error)."""
    found = None
    for sym, code in SYMBOLS:
        if sym in s:
            found = code
            s = s.replace(sym, " ")
            break
    for code in CODES:
        if re.search(rf"(?<![a-z]){code}(?![a-z])", s):
            if found not in (None, "", code.upper()):
                return s, "", "Use one currency."
            found = code.upper()
            s = re.sub(rf"(?<![a-z]){code}(?![a-z])", " ", s)
    for word, code in WORDS.items():
        if re.search(rf"(?<![a-z]){word}(?![a-z])", s):
            found = found or code
            s = re.sub(rf"(?<![a-z]){word}(?![a-z])", " ", s)
    if any(sym in s for sym, _ in SYMBOLS):
        return s, "", "Use one currency."
    return s, found or "", None


def _period(text: str) -> str | None | bool:
    """The period a unit names, None for no unit, False for a unit we don't know."""
    t = re.sub(r"\s*/\s*", "/", text.strip().rstrip("."))
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^(base|gross|before tax)\s+", "", t)
    t = re.sub(r"\s+(base|gross|before tax)$", "", t)
    if not t:
        return None
    return PERIOD_WORDS.get(t, False)


def _normalize(text) -> str:
    s = unicodedata.normalize("NFKC", str(text if text is not None else "")).strip().lower()
    s = re.sub(r"(?<=\d)[\s'’](?=\d)", "", s)  # 45 000, 45 000 (no-break space), 1'000
    return re.sub(r"^(about|around|approx\.?|approximately|~|at least|min\.?|minimum)\s*", "", s)


def parse_pay_range(text) -> tuple[Pay | None, Pay | None, str | None]:
    """(low, high, error). A single amount gives the same Pay twice. Never raises."""
    raw = str(text if text is not None else "").strip()
    s = _normalize(text)
    if not s:
        return None, None, None
    try:
        return _parse(raw, s)
    except (ValueError, OverflowError, ArithmeticError):
        return None, None, f"Enter an amount {EXAMPLES}."


def _parse(raw: str, s: str) -> tuple[Pay | None, Pay | None, str | None]:
    if s.startswith(("-", "−", "minus")):
        return None, None, "Pay can't be negative."
    s, currency, err = _currency(s)
    if err:
        return None, None, err
    s = s.strip()
    parts = _SEP.split(s)
    if len(parts) > 2 or not all(parts):
        return None, None, f"Enter one amount or a range, {EXAMPLES}."
    pieces = []
    for part in parts:
        m = _PART.fullmatch(part.strip())
        if not m:
            return None, None, f"Enter an amount {EXAMPLES}."
        period = _period(m.group(3))
        if period is False and re.search(r"[\d.,]", m.group(3)):
            return None, None, f"Enter an amount {EXAMPLES}."
        if period is False:
            return None, None, f"I couldn't read “{m.group(3).strip()}” as a pay period. Write it {EXAMPLES}."
        pieces.append([_number(m.group(1)), m.group(2), period])
    if len(pieces) == 2:  # "60-70k" and "17-19/hr": the unit on the second amount applies to both
        (_, m1, p1), (_, m2, p2) = pieces
        if p1 and p2 and p1 != p2:
            return None, None, "Use the same unit for both ends of the range."
        pieces[0][2] = pieces[1][2] = p1 or p2
        if not m1 and m2 and pieces[0][0] < 1000:
            pieces[0][1] = m2
    assumed = False
    if not any(p[1] or p[2] for p in pieces):  # bare numbers: the size says what was meant
        top = max(p[0] for p in pieces)
        if top < 7:
            return None, None, f"{_fmt(top)} looks too low for pay. Add the unit, {EXAMPLES}."
        unit = ("hour", None) if top <= 200 else ("year", "k") if top < 1000 else ("year", None)
        for p in pieces:
            p[2], p[1] = unit[0], unit[1]
        assumed = top < 1000
    out = []
    for amount, mult, period in pieces:
        amount *= _MULT.get(mult or "", 1)
        period = period or "year"
        yearly = amount * PER_YEAR[period]
        if not math.isfinite(yearly) or amount <= 0:
            return None, None, "Enter an amount above zero."
        if yearly > MAX_YEARLY:
            return None, None, f"That's over {MAX_YEARLY:,} a year. Check the number, or add the unit, {EXAMPLES}."
        out.append(Pay(int(round(yearly)), round(amount, 2), period, currency, raw[:60], assumed))
    if len(out) == 2 and out[0].yearly > out[1].yearly:
        return None, None, "Put the lower amount first, like 60k-70k."
    return out[0], out[-1], None


def parse_pay(text, pick: str = "low") -> tuple[Pay | None, str | None]:
    """One amount: the low end of a range, or the high end with pick="high"."""
    low, high, err = parse_pay_range(text)
    return (high if pick == "high" else low), err


def parse_distance(text) -> tuple[int | None, str | None]:
    """Miles, from "15", "15 miles", "15mi" or "25 km". Never raises."""
    s = _normalize(text)
    if not s:
        return None, None
    message = "Enter a distance, like 15 or 25 km."
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(mi|mile|miles|km|kms|kilometers?|kilometres?)?\.?", s)
    if not m:
        return None, message
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None, message
    if (m.group(2) or "").startswith("k"):
        value *= 0.621371
    if not math.isfinite(value) or value > 1000:
        return None, message
    return int(round(value)), None


# ----------------------------------------------------------------- display

def _fmt(amount: float) -> str:
    return f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"


def money_text(amount: float, currency: str = "") -> str:
    """$37,440, €40,000, C$380,000, CHF 120,000, $17.50."""
    sym = DISPLAY.get(currency or "")
    return f"{sym}{_fmt(amount)}" if sym else f"{currency} {_fmt(amount)}"


PERIOD_LABEL = {"hour": "an hour", "day": "a day", "week": "a week", "month": "a month", "year": "a year"}


def describe(detail: dict | Pay) -> str:
    """'$18 an hour (about $37,440 a year)', or '$45,000 a year'."""
    d = detail.as_dict() if isinstance(detail, Pay) else detail
    cur = d.get("currency", "")
    own = f"{money_text(d['amount'], cur)} {PERIOD_LABEL[d['period']]}"
    return own if d["period"] == "year" else f"{own} (about {money_text(d['yearly'], cur)} a year)"


def yearly_text(yearly: int, detail: dict | None = None) -> str:
    """For the AI: 'CAD 380,000 a year (typed C$380k)'."""
    cur = (detail or {}).get("currency", "")
    text = f"{cur + ' ' if cur else ''}{int(yearly):,} a year"
    typed = (detail or {}).get("as_typed", "")
    if typed and (detail or {}).get("assumed"):
        text += f" (typed {typed}, read as {describe(detail).split(' (')[0]})"
    elif typed and re.sub(r"[\s,$]", "", typed) != str(int(yearly)):
        text += f" (typed {typed})"
    return text
