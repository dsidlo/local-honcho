#!/usr/bin/env python3
"""
Gibberish guard for Piper/espeak-ng (and generally engine-hostile text).

Runs AFTER tts_sanitize.py (markdown/emoji/URL stripping) but BEFORE the
engine call, in the same chain as tts_piper_normalize.py. The existing
sanitizers leave symbol-dense technical fragments (file paths, KEY=VALUE
flags, hex hashes, arrows, <= / >=, snake_case tokens) that make
espeak-ng synthesize trailing nonsense ("trailed off into gibberish").
This layer scores the text and mitigates:

  1. rewrite  tokens it can convert to speech-safe words
              (arrows -> "then", <= / >= -> "at most"/"at least",
               == / != / === -> "equals"/"is not equal to"/"is identical
               to", digit-flanked < / > -> "less than"/"greater than",
               caret powers 2^8 -> evaluated or "2 to the power of 8",
               KEY=value -> "value flag", relative paths -> last two
               segments spoken plainly)
  2. score    each sentence 0-100 for engine-hostile content
  3. drop     sentences at or above TTS_GUARD_MAX_RISK (default 50)
  4. digest   if too much was dropped or the text is very long, speak a
              small digest of the safest sentences plus a canned closer
  5. log      one decision line per call to guard.log (tail-able)

Usage:
    tts_guard.py "text"          # prints speakable text (never raises)
    tts_guard.py --selftest      # built-in checks, exit 1 on failure
    tts_guard.py --report "text" # also print decision JSON to stderr

Env:
    TTS_GUARD_MAX_RISK  sentence drop threshold (default 50)
    TTS_GUARD_MAX_CHARS digest trigger for whole text (default 1200)
    TTS_GUARD_LOG       log path (default <script_dir>/guard.log)

Design rule: NEVER raise. On any internal error the input is returned.
"""

import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_RISK = int(os.environ.get("TTS_GUARD_MAX_RISK", "50"))
MAX_CHARS = int(os.environ.get("TTS_GUARD_MAX_CHARS", "1200"))
LOG_PATH = os.environ.get("TTS_GUARD_LOG",
                          os.path.join(SCRIPT_DIR, "guard.log"))
CLOSER = "The full details are in the chat transcript."
TOO_TECHNICAL = "That response was too technical to speak."

# ---------------------------------------------------------------- rewrites
ARROWS = re.compile(r"\s*(?:->|=>|->|⇒|⟶|→)\s*")
AT_MOST = re.compile(r"\s*(?:<=|≤)\s*")
AT_LEAST = re.compile(r"\s*(?:>=|≥)\s*")
# Full equality/inequality comparisons (sanitize leaves "=" untouched).
EQ_OPS = re.compile(r"===|!==|==|!=")
_EQ_WORDS = {
    "===": " is identical to ",
    "!==": " is not identical to ",
    "==": " equals ",
    "!=": " is not equal to ",
}
# Digit/word-flanked strict ordering, after <= and >= pairs are consumed.
LT_SYMBOL = re.compile(r"(?<=[\w\)])\s*<\s*(?=[\w\(])")
GT_SYMBOL = re.compile(r"(?<=[\w\)])\s*>\s*(?=[\w\(])")
# Digit-flanked caret exponentiation (the ** form is resolved earlier, in
# tts_sanitize.py, before leftover asterisks are stripped there).
POW_CARET = re.compile(r"(?<![\w.])(\d{1,9})\s*\^\s*(\d{1,2})(?![\w.])")
KEY_VALUE = re.compile(r"\b[a-zA-Z][\w.-]{1,24}=([A-Za-z0-9][\w.-]{0,24})\b")
PASS_FAIL = re.compile(r"\bPASS\s*/\s*FAIL\b", re.IGNORECASE)
MATH_MISC = re.compile(r"[∈∃≈≠±×÷]")
MEMBERSHIP = re.compile(r"\s*∈\s*")
CURLY = re.compile(r"[{}]")
BACKTICK = re.compile(r"`+")
# Home-dir shorthand: "~/.pi" is unpronounceable for espeak; say "home".
TILDE_HOME = re.compile(r"(?<![\w])~/")
# Slash-flanked punctuation tokens (backtick-stripped markdown lists such
# as "/! /? /;") — espeak garbles the bare glyphs, so voice them.
PUNCT_SLASH = re.compile(r"/[!?.;:](?:[/\s]*[/][!?.;:])*")
_PUNCT_NAMES = {
    "!": "bang", "?": "question mark", ";": "semicolon",
    ":": "colon", ".": "dot",
}
HASH_TOKEN = re.compile(r"\b(?=[0-9a-f]{7,40}\b)(?=.*\d)(?=.*[a-f])[0-9a-f]+\b")
PATH_TOKEN = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\b")
UNDERSCORE = re.compile(r"\b(\w+)_(\w+)\b")
MULTI_PUNCT = re.compile(r"([.!?]){2,}")


def _kv(match):
    word = match.group(1)
    return f" {word} flag " if word.isalpha() else " value "


def _punct_slash(match):
    """Voice a slash-punctuation run as "slash bang, slash question mark"."""
    tokens = re.findall(r"/[!?.;:]", match.group(0))
    parts = [f"slash {_PUNCT_NAMES[token[1]]}" for token in tokens]
    return " " + ", ".join(parts) + " "


def _rewrite(text: str) -> str:
    t = ARROWS.sub(" then ", text)
    t = AT_MOST.sub(" at most ", t)
    t = AT_LEAST.sub(" at least ", t)
    t = EQ_OPS.sub(lambda m: _EQ_WORDS[m.group(0)], t)
    t = LT_SYMBOL.sub(" less than ", t)
    t = GT_SYMBOL.sub(" greater than ", t)
    t = POW_CARET.sub(_pow_spoken, t)
    t = PASS_FAIL.sub(" pass or fail ", t)
    t = KEY_VALUE.sub(_kv, t)
    t = MEMBERSHIP.sub(" in ", t)
    t = MATH_MISC.sub(" ", t)
    t = CURLY.sub(" ", t)
    t = BACKTICK.sub(" ", t)
    t = TILDE_HOME.sub("home/", t)
    t = PUNCT_SLASH.sub(_punct_slash, t)
    t = PATH_TOKEN.sub(_path_word, t)
    t = HASH_TOKEN.sub(" ", t)
    t = UNDERSCORE.sub(r"\1 \2", t)
    t = MULTI_PUNCT.sub(r"\1", t)
    return re.sub(r"[ \t]{2,}", " ", t)


def _pow_spoken(match):
    """Digit-flanked a^b -> "256" when short, else spoken form."""
    base, expo = int(match.group(1)), int(match.group(2))
    if 0 <= expo <= 20:
        result = base ** expo
        if len(str(result)) <= 9:
            return f" {result} "
    return f" {base} to the power of {expo} "


def _path_word(match):
    """Relative paths/dirs -> last two segments, spoken plainly.

    "src/main.py" -> "src slash main dot py"; "24/7" -> "24 slash 7".
    Dots inside segments become "dot" so espeak does not swallow them.
    """
    token = match.group(0)
    parts = [p for p in token.split("/") if p]
    spoken = " slash ".join(p.replace(".", " dot ") for p in parts[-2:])
    return f" {spoken} "


# ---------------------------------------------------------------- scoring
_HEAVY_CHARS = set("<>|[]()~^&*%+=/\\#@")


def _risk(sentence: str) -> int:
    """0-100 heuristic score: 50+ means piper/espeak will likely garble it."""
    s = sentence.strip()
    if not s:
        return 0
    tokens = s.split()
    score = 0
    for tok in tokens:
        core = tok.strip("!?.,;:\"'()[]")
        if not core:
            continue
        if re.fullmatch(r"[0-9a-f]{10,}", core) and any(c.isdigit() for c in core):
            score += 25
        elif re.fullmatch(r"\d{7,}", core):
            score += 15
        elif len(core) > 16 and not core.isalpha():
            score += 15
        if "=" in core or "&" in core:
            score += 20
        heavy = sum(1 for c in core if c in _HEAVY_CHARS)
        if heavy >= 2:
            score += 15
    # Density term: divide by a floor of 40 chars so a lone period in a
    # tiny clean sentence ("Ok.") cannot dominate the ratio.
    density = sum(1 for c in s if not (c.isalnum() or c.isspace())) / max(len(s), 40)
    score += min(40, int(density * 150))
    if s.count("(") != s.count(")"):
        score += 10
    if len(s) > 350:
        score += 10
    return score


def _sentences(text: str):
    """Split into sentences, keeping the end punctuation with each."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _log(entry):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def guard(text: str):
    """Returns (final_text, report_dict). Never raises."""
    try:
        original = text.strip()
        if not original:
            return original, {"mode": "empty"}
        rewritten = _rewrite(original)
        scored = [(_s, _risk(_s), _n) for _n, _s in enumerate(_sentences(rewritten))]
        kept = [(_s, _r) for _s, _r, _n in scored if _r < MAX_RISK]
        dropped = [_s for _s, _r, _n in scored if _r >= MAX_RISK]
        orig_chars = len(rewritten)
        kept_chars = sum(len(s) for s, _r in kept)

        mode = "final"
        if kept and orig_chars <= MAX_CHARS and not (
                dropped and kept_chars < 0.5 * orig_chars):
            final = " ".join(s for s, _r in kept)
        else:
            mode = "digest"
            # Digests speak a one-sentence headline, not a sample of the
            # safest fragments: the FIRST plain sentence in narrative order
            # carries the orientation; out-of-context tail fragments do not.
            head = None
            for s, r in kept:
                if r < 40 and len(s) <= 300:
                    head = s
                    break
            if head is None:
                final = f"{TOO_TECHNICAL} {CLOSER}"
            else:
                if len(head) > 260:
                    cut = head.rfind(".", 0, 260)
                    if cut > 40:
                        head = head[:cut + 1]
                final = ((head if head.endswith((".", "!", "?")) else head + ".")
                         + " " + CLOSER)
        report = {"mode": mode, "orig_chars": orig_chars, "final_chars": len(final),
                  "dropped": [d[:80] for d in dropped][:5],
                  "risks": {f"s{_n}": _r for _n, (_s, _r, _) in enumerate(scored)}}
        _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **report,
              "orig_head": original[:60]})
        return final, report
    except Exception:
        return text, {"mode": "error"}


def _selftest() -> int:
    cases = [
        ("clean prose kept", "Monitoring is armed and you can keep prompting.",
         "Monitoring is armed and you can keep prompting.", None),
        ("path -> last two segments", "It writes results/monitor_flag.json to disk.",
         "results slash monitor flag dot json", "results/"),
        ("long path slab shortened", "sync /home/ds/proj/src/queue_manager.py next",
         "src slash queue manager dot py", "home"),
        ("slash ratio kept readable", "uptime 24/7 target", "24 slash 7", "24/7"),
        ("punct slash run voiced", "split at /!/?/; /: now",
         "slash bang, slash question mark, slash semicolon, slash colon", "/!"),
        ("punct slash spaced voiced", "boundaries. /! /? with whitespace",
         "slash bang", " /! "),
        ("tilde home expanded", "Sync complete, ~/.pi matches the repo.",
         "home slash dot pi", "~"),
        ("caret power evaluated", "The heap is 2^8 pages.", "is 256 pages.", "^"),
        ("caret power spelled", "a 7^20 grid", "7 to the power of 20", "^"),
        ("double equals", "run only when a == b", "a equals b", "=="),
        ("not equal", "skip when state != ok", "state is not equal to ok", "!="),
        ("strict identity", "x === y strict", "x is identical to y", "==="),
        ("not identical", "v !== w mismatch", "v is not identical to w", "!=="),
        ("less than", "loop while n < 5", "n less than 5", "<"),
        ("greater than", "alert if m > 3", "m greater than 3", ">"),
        ("hash dropped", "Committed 1f9472f to master.", "Committed", "1f9472f"),
        ("arrow rewritten", "D is 238.92 counts then 96.139 percent.",
         " then ", "→"),
        ("kv rewritten", "The monitor writes a done flag on completion.",
         "flag", "exit="),
        ("pass fail", "Report pass or fail per anchor.", "pass or fail", "PASS/"),
        ("at most", "Alert latency is at most 2 minutes.", "at most", "≤"),
        ("hostile sentence dropped",
         "Ok. Blah <<a|b>> then ~c&d*e/f%g^h is garbage.",
         "Ok.", "garbage"),
        ("digest fallback", "Monitoring works. <<<a|b>> c&d>e/f%g^h 1f9472f.",
         CLOSER, "1f9472f"),
        ("empty safe", "   ", "", None),
    ]
    failed = 0
    for desc, inp, expect, no in cases:
        out, rep = guard(inp)
        ok = (expect in out) if expect is not None else out == ""
        if ok and no is not None:
            ok = no not in out
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {desc} [{rep.get('mode')}]")
        print(f"      in : {inp!r}")
        print(f"      out: {out!r}")
    print("ALL PASS" if failed == 0 else f"{failed} FAILURES")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            sys.exit(_selftest())
        report_mode = "--report" in sys.argv
        args = [a for a in sys.argv[1:] if a not in ("--selftest", "--report")]
        text = args[0] if args else sys.stdin.read()
        final, rep = guard(text)
        if report_mode:
            print(json.dumps(rep), file=sys.stderr)
        print(final, end="")
    except Exception:
        # absolute last resort: never block speech
        args = sys.argv[1:]
        print(args[0] if args and args[0] != "--report" else "", end="")