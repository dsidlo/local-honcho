#!/usr/bin/env python3
"""
Piper-specific text normalization layer.

Runs ONLY in the Piper branch of speak.sh, after the shared
tts_sanitize.py pass, where the engine is known to be Piper/espeak-ng.
Fixes the documented espeak-ng gibberish behaviors:

- currency   "$4.63" -> "4 dollars and 63 cents"
             (espeak-ng says "dollar four point six three")
- titles     "Dr. Mr. Mrs. Ms. Prof. No." -> spoken words
             (espeak-ng ends the sentence right after "Doctor.")
- decades    "1990s" -> "nineteen nineties"
             (espeak-ng says "nineteen hundred ninety Z")
- units      "km/h" -> "kilometers per hour", "mph" -> "miles per hour",
             "60C"/"60 degrees C" -> "60 degrees Celsius"
             (espeak-ng says "sixty K M slash H")
- terminal   short text without sentence punctuation ends clipped
             (measured on jenny-medium: last-50ms energy 57% of speech
             with no sentence mark vs 10% with one), so ensure a final
             period when the text ends on a word.

Deliberately not handled (ambiguous semantics, espeak-ng reads them
acceptably): dates like 4/5/2026 (day/month order unknown), "St."
(Saint vs Street), Roman numerals (I/V/X collide with words).

Usage:
    tts_piper_normalize.py "text"     # prints normalized text
    tts_piper_normalize.py --selftest # runs built-in checks

Import: `from tts_piper_normalize import piper_normalize`.
Never raises: falls back to the input text on internal errors.
"""

import re
import sys

DECADE_WORDS = {
    1: "tens", 2: "twenties", 3: "thirties", 4: "forties", 5: "fifties",
    6: "sixties", 7: "seventies", 8: "eighties", 9: "nineties",
}

CURRENCY = {"$": ("dollar", "dollars"), "€": ("euro", "euros"),
            "£": ("pound", "pounds"), "¥": ("yen", "yen")}

TITLES = [
    (r"\bDr\.(?=\s)", "Doctor"),
    (r"\bMr\.(?=\s)", "Mister"),
    (r"\bMrs\.(?=\s)", "Misses"),
    (r"\bMs\.(?=\s)", "Miss"),
    (r"\bProf\.(?=\s)", "Professor"),
    (r"\bNo\.(?=\s*\d)", "number"),
]

UNITS = [
    (r"\bkm/?h\b", " kilometers per hour "),
    (r"\bmph\b", " miles per hour "),
    (r"(?<=\d)\s*°\s*C\b", " degrees Celsius"),
    (r"(?<=\d)\s*°\s*F\b", " degrees Fahrenheit"),
    (r"(?<=\d)\s*s\b(?=[.,;:!?)]|\s|$)", " seconds"),   # "0.07s" -> seconds; the
                                # decade rule consumes "1990s" before this runs
    (r"(?<=\d)\s*°(?=\s|$)", " degrees"),
    (r"(?<=\d)\s*C\b(?=\.?\s|$)", " degrees Celsius"),
    (r"(?<=\d)\s*F\b(?=\.?\s|$)", " degrees Fahrenheit"),
]


def _decade(match: re.Match) -> str:
    year = int(match.group(1))
    century, rest = divmod(year, 100)
    tens = rest // 10
    if rest == 0:
        base = "nineteen" if century == 19 else "two"
        return f" {base} hundreds"
    return f" {'nineteen' if century == 19 else 'twenty'} {DECADE_WORDS[tens]}"


def _price(sign: str, amount: str) -> str:
    singular, plural = CURRENCY[sign]
    whole, _, frac = amount.replace(",", "").partition(".")
    try:
        whole_n = int(whole)
        cents_n = int(frac.ljust(2, "0")[:2]) if frac else 0
    except ValueError:
        return f"{sign}{amount}"
    base = f"{whole_n} {singular if whole_n == 1 else plural}"
    if cents_n:
        cents = f"{cents_n} cent" + ("" if cents_n == 1 else "s")
        return f"{base} and {cents}"
    return base


def piper_normalize(text: str) -> str:
    """Normalize text for Piper/espeak-ng. Input should already be shared-
    sanitized (tts_sanitize.sanitize). Falls back to input on any error."""
    try:
        return _normalize(text)
    except Exception:
        return text


def _normalize(text: str) -> str:
    if not text or not text.strip():
        return ""

    t = text

    for pat, word in TITLES:
        t = re.sub(pat, word, t)

    t = re.sub(r"\b((?:19|20)\d{2})('?s)\b", _decade, t)

    for sign in "$€£¥":
        t = t.replace(sign, f" {sign} ")
    t = re.sub(r"\s+([$€£¥])\s*", r"\1", t)
    t = re.sub(r"([$€£¥])(\d[\d,]*(?:\.\d+)?)",
               lambda m: " " + _price(m.group(1), m.group(2)) + " ", t)

    # Bare "60 C" -> "60 degrees Celsius"; the (?:\.?\s|$) guard skips
    # tokens like "60Co".
    for pat, rep in UNITS:
        t = re.sub(pat, rep, t)

    # Sentence-final mark so the utterance fades instead of clipping.
    t = t.rstrip()
    if t and (t[-1].isalnum() or t[-1] in "%)]"):
        t += "."

    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r"(?m)[ \t]+$", "", t)
    return t.strip()


_SELFTESTS = [
    ("usd cents", "it cost $4.63 total", "4 dollars and 63 cents", "$"),
    ("usd round", "pay $5 now", "5 dollars now", "$5"),
    ("usd exact", "$5.00 flat", "5 dollars", "0 cents"),
    ("euro", "€2.50 each", "2 euros and 50 cents", "€"),
    ("cents only tens digit", "$4.5 now", "4 dollars and 50 cents", "4.5"),
    ("decade nineties", "music of the 1990s", "of the nineteen nineties", "1990s"),
    ("decade twenties", "the 2020s era", "the twenty twenties era", "2020s"),
    ("decade hundreds", "life in the 1900s", "the nineteen hundreds", "1900s"),
    ("decade two-thousands", "the 2000s bugs", "the two hundreds bugs", "2000s"),
    ("decade possessive form", "the 1980's style", "the nineteen eighties style", "1980"),
    ("doctor title", "Dr. Smith called", "Doctor Smith called", "Dr."),
    ("mister title", "Mr. Jones replied", "Mister Jones replied", "Mr."),
    ("professor + number", "Prof. Lang said No. 5 wins", "Professor Lang said number 5 wins", "Prof."),
    ("km h", "driving 60 km/h", "driving 60 kilometers per hour", "km/h"),
    ("mph", "going 45 mph", "going 45 miles per hour", "mph"),
    ("degrees celsius", "it is 60°C today", "it is 60 degrees Celsius today", "°C"),
    ("bare terminal word", "say hello", "hello.", None),
    ("keeps existing period", "already done.", "already done.", "done.."),
    ("keeps terminal mark after unit", "it hit 60 kilometers per hour.", "per hour.", "hour.."),
    ("seconds shorthand", "took 0.07s total", "0.07 seconds total", "0.07s total"),
    ("seconds spaced", "about 3.5 s later", "3.5 seconds later", "3.5 s later"),
    ("empty safe", "   ", "", None),
]


def _selftest() -> int:
    failed = 0
    for desc, inp, expect, no in _SELFTESTS:
        out = piper_normalize(inp)
        ok = (expect in out) if expect is not None else out == ""
        if ok and no is not None:
            ok = no not in out
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {desc}")
        print(f"      in : {inp!r}")
        print(f"      out: {out!r}")
        if not ok:
            print(f"      expected to contain {expect!r} and not {no!r}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILURES'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if len(sys.argv) < 2:
        print('Usage: tts_piper_normalize.py "text" | --selftest', file=sys.stderr)
        sys.exit(1)
    print(piper_normalize(sys.argv[1]), end="")