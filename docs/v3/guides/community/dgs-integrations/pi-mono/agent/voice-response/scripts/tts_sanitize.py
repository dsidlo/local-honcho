#!/usr/bin/env python3
"""
Shared TTS text sanitizer for the voice-response skill.

Turns agent-typical markdown/symbol output into text that reads cleanly
when spoken: no asterisks, no URLs spelled out, no emoji names, no table
pipes, no raw code.

Usage:
    python3 tts_sanitize.py "text to clean"        # prints cleaned text
    python3 tts_sanitize.py --selftest             # runs built-in checks

Import as a module: `from tts_sanitize import sanitize`.
Designed to never fail: if anything goes wrong it returns a crudely
de-markdowned version of the input rather than raising.
"""

import re
import sys

# ---------------------------------------------------------------------------
# Spoken-word substitutions applied before symbol stripping.
# (pattern, replacement) pairs of things that are better as words than symbols.
# ---------------------------------------------------------------------------
WORD_SUBS = [
    (r"→|->|=>|⇒", " to "),
    (r"←|<-", " from "),
    (r"↔", " between "),
    (r"[✓✔☑✅]", " "),           # checkmarks are redundant when spoken
    (r"[✗✘✖☒❌]", " "),           # the surrounding words carry the meaning
    (r"⚠", " "),                     # surrounding words usually say it already
    (r"[©®™]", " "),
    (r"\bC#(?=\W|$)", " C sharp "),
    (r"(?<=[\s(])&(?=[\s)])", " and "),
    (r"\bfor e\.g\.", "for example,"),
    (r"\be\.g\.", " for example,"),
    (r"\bi\.e\.", " that is,"),
    (r"\betc\.", " et cetera"),
    (r"\bvs\.?\b", " versus "),
    (r"\bw/(?=\s)", "with "),
    (r"≈", " approximately "),
    (r"±", " plus or minus "),
    (r"[—‒―]|(?<=\s)--(?=\s)", ", "),              # em/prose dashes pause
    (r"(?<=\d)–(?=\d)", " to "),                     # numeric range en dash
    (r"\bsk-lf-\S+", " secret key "),                 # never spell key strings
    (r"\bpk-lf-\S+", " public key "),
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", " unique identifier "),
    (r"\b[0-9a-f]{16,}\b", " hash value "),            # hex blobs invent phonemes
    (r"\b(?=\S*\d)\S{16,}\b", " identifier "),       # 16+ nonspace chars w/ digit
    (r"(?<=\w):(?=\d)", " "),                           # localhost:3000, 23:46 -> pause
                                                       # (prose 'Fast: 4.6' keeps colon - space before it)
    (r"\b(\d{1,4})ms\b", r"\1 milliseconds"),
    (r"×", " times "),
    (r"÷", " divided by "),
    (r"(?<=[\s])<(?=[\s])", " less than "),
    (r"(?<=[\s])>(?=[\s])", " greater than "),
]

# Emoji, pictographs, dingbats, box drawing, misc symbols, flags, and
# joiners/variation selectors: TTS engines read their names aloud ("face
# with tears of joy") or stall on them. Anything mapped above is gone
# before this runs.
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # pictographs, transport, supplemental symbols
    "\u2600-\u27BF"           # misc symbols + dingbats
    "\u2500-\u25FF"           # box drawing, geometric shapes
    "\u2B00-\u2BFF"           # misc symbols and arrows
    "\U0001F1E6-\U0001F1FF"   # regional indicator flags
    "\uFE0F\u200D\u20E3"      # variation selector, ZWJ, keycap joiner
    "]"
)

# Non-Latin scripts: English voices read them as garbage phonemes. Strip
# rather than butcher. Covers CJK, kana, Hangul, Cyrillic, Arabic, Hebrew,
# Devanagari, Thai, and common fullwidth forms.
SCRIPT_RE = re.compile(
    "["
    "\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u0900-\u097F"
    "\u0E00-\u0E7F\u3040-\u30FF\u3130-\u318F\u3400-\u4DBF\u4E00-\u9FFF"
    "\uAC00-\uD7AF\uFF00-\uFF60"
    "]+"
)


def sanitize(text: str) -> str:
    """Return cleaned text. Falls back to a minimal strip on any error."""
    try:
        return _sanitize(text)
    except Exception:
        try:
            return re.sub(r"[*#`|_]+|https?://\S+", " ", text).strip()
        except Exception:
            return text


def _sanitize(text: str) -> str:
    if not text or not text.strip():
        return ""

    t = text

    # 1. Fenced code blocks: code is painful spoken; announce it was omitted.
    t = re.sub(r"```[\s\S]*?```", " code omitted. ", t)
    t = re.sub(r"```[\s\S]*\Z", " code omitted. ", t)     # unterminated fence
    t = re.sub(r"~~~[\s\S]*?~~~", " code omitted. ", t)

    # 2. HTML tags and entities, e.g. <br>, &nbsp;. Content must look
    # tag-like (optional "/", then a letter) so prose with bare angle
    # pairs ("n < 5 and m > 3") survives for the guard's comparison
    # rewrites instead of being swallowed as markup.
    t = re.sub(r"</?[A-Za-z][^>\n]{0,199}>", " ", t)
    t = re.sub(r"&[a-zA-Z]+;", " ", t)

    # 3. Links: keep the label, drop the target.
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                       # images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)",
               lambda m: f" {m.group(1).strip() or 'link'} ", t)      # md links
    t = re.sub(r"(?:https?://|www\.)\S+", " link ", t, flags=re.I)    # bare urls

    # 3b. Powers: "2**8" -> "256" when the result stays short (espeak
    # reads long digit slabs worse than words); otherwise spell it out.
    # Must run BEFORE the bold/italic formatter below: two separate '**'
    # pairs ("x**y and n**3") make its lazy middle span across them and
    # swallow the operators entirely ("x y and n 3"), and leftover-`*`
    # cleanup would lose the exponent too ("2**8" -> "2 8").
    def _pow_sub(m):
        base, expo = int(m.group(1)), int(m.group(2))
        if 0 <= expo <= 20:
            result = base ** expo
            if len(str(result)) <= 9:
                return f" {result} "
        return f" {base} to the power of {expo} "

    t = re.sub(r"(?<![\w.])(\d{1,9})\s*\*\*\s*(\d{1,2})(?![\w.])", _pow_sub, t)
    # NOTE: a generic word**word "to the power of" residue rule is NOT
    # safe here: "**bold**" (markdown) matches it. Step 4's bold/italic
    # formatter and step 10's leftover-`*` strip reduce real word**word
    # powers to "x y", which degrades gracefully instead of mangling
    # every bold phrase.

    # 4. Asterisk/underscore formatting: bold, italic, strike, inline code.
    t = re.sub(r"\*{1,3}([^*]+?)\*{1,3}", r"\1", t)                   # **x**, *x*
    t = re.sub(r"(?<![\w_])_(?=\S)([^_\n]*\S)_(?![\w_])", r"\1", t)   # _x_ only
    t = re.sub(r"~~(.+?)~~", r"\1", t)                                # ~~x~~
    t = re.sub(r"`{1,3}([^`]*)`{1,3}", r" \1 ", t)                    # `x`

    # 5. Spoken-word substitutions (symbols -> words).
    for pat, rep in WORD_SUBS:
        t = re.sub(pat, rep, t)

    # 6. Emoji, decorative symbols, and non-Latin scripts.
    t = re.sub(r"\b[A-Z]{5,}\b", lambda m: m.group(0).lower(), t)  # caps words
    t = EMOJI_RE.sub(" ", t)
    t = SCRIPT_RE.sub(" ", t)

    # 6b. Phonetic transcription garbage: IPA letters and stress/length
    # marks (U+0250-02AF, U+02C0-02FF). English voices read them by name
    # ("stress turned alpha small cap eye") - letter soup. They never occur
    # in normal prose, so drop any token containing one. Greek letters like
    # theta are kept: espeak reads their names acceptably.
    t = re.sub(r"\S*[\u0250-\u02AF\u02C0-\u02FF]\S*", " ", t)

    # 6c. Path slabs to the final segment: "slash t m p slash" is noisy;
    # extension-gated so "24/7" and prose with slashes are untouched.
    t = re.sub(r"(?:\S*/)+([A-Za-z0-9_.][\w.-]*\.(?:py|ts|tsx|js|sh|json|md|txt"
               r"|wav|onnx|log|html|css|csv|ya?ml|toml|xml|cfg|conf|so|bin|lock|svg|png)\b)",
               r"\1", t)

    # 7. Structural markdown: headers, quotes, rules, bullets, tables.
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", t)                       # headers
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*$", "", t)
    t = re.sub(r"(?m)^\s{0,3}>\s?", "", t)                            # quotes
    t = re.sub(r"(?m)^\s{0,3}([-*_][ \t]{0,2}){3,}$", "", t)          # hr rules
    t = re.sub(r"(?m)^[ \t]{0,3}\|[\s:|-]*\|\s*$", "", t)             # table seps
    t = re.sub(r"(?m)^[ \t]{0,3}[-*+][ \t]+", "", t)                  # bullets
    t = re.sub(r"(?m)^\s{0,3}\[\d+\]:.*$", "", t)                     # footnotes
    t = re.sub(r"\[\d+\]", "", t)                                     # cite refs
    t = t.replace("|", ", ")                                          # table cols

    # 8. Brackets become pauses; leftover brackets collapse.
    t = re.sub(r"\[([^\]\n]*)\]", r" \1 ", t)
    t = re.sub(r"[()]", ", ", t)
    t = re.sub(r"[{}]", " ", t)

    # 9. Thousands separators in numbers ("1,234" pauses mid-number).
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)

    # 10. Leftover formatting characters that engines read aloud.
    t = re.sub(r"[*_#]{1,}", " ", t)
    t = re.sub(r"\\(?=[^\s])", " ", t)                                # escapes
    t = re.sub(r"([^\w\s.,!?;:])\1{2,}", r"\1", t)                    # ~~~~ -> ~

    # 11. Punctuation normalisation.
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)                            # space before punct
    t = re.sub(r"…|\.{3,}", ", ", t)                                  # ellipsis
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"\?{2,}", "?", t)
    t = re.sub(r"([,.;:!?])[ \t]*\1+", r"\1", t)                      # repeats
    t = re.sub(r"[,;:]([.!?])(?=\s|$)", r"\1", t)                     # ',.' -> '.'
    t = re.sub(r"\b(\w+)(\s+\1\b){2,}", r"\1", t, flags=re.I)         # stuttered words

    # 12. Whitespace: keep paragraph breaks (the engine pauses on \n+).
    t = re.sub(r"[ \t]+", " ", t, flags=re.M)
    t = re.sub(r" ?\n ?\n ?", "\n\n", t)
    t = re.sub(r"(?m)[ \t]+$", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ ]{2,}", " ", t)

    return t.strip()


_SELFTESTS = [
    # (description, input, expected-substring, should-not-contain)
    ("bold removed", "This is **bold** text", "This is bold text", "**"),
    ("italic removed", "an *important* point", "an important point", "*"),
    ("underscore italic removed, snake_case spoken as words",
     "use _this_ and file_name_ok", "use this and file name ok", "_"),
    ("inline code", "run `pnpm seed --list` now", "run pnpm seed --list now", "`"),
    ("link keeps label", "see [Langfuse](http://localhost:3000) traces",
     "see Langfuse traces", "localhost"),
    ("bare url", "docs at https://example.com/a?b=1 here", "docs at link here", "http"),
    ("code fence omitted", "```py\nx = '**'\n```\nAfter", "code omitted.", "x ="),
    ("checkmark + parens", "langfuse ✓ (trace sent)", "langfuse, trace sent,", "✓"),
    ("arrow", "pnpm run seed → verify", "pnpm run seed to verify", "→"),
    ("header + bullet", "## Results\n- Fast: 4.6s", "Results\nFast: 4.6s", "#"),
    ("table pipes", "calls | 25 | total", "calls, 25, total", "|"),
    ("phonetic soup stripped", "reads as 'ɪt kˈɒst dˈɒlɐ fˈɔː' here", "reads as", "kˈɒst"),
    ("stress marks stripped", "clauses nˈaɪntiːn and kˌeɪˈɛm", "clauses and", "aɪntiːn"),
    ("path slab basename", "inspect /tmp/gib_before.wav now", "before.wav", "tmp/"),
    ("emoji dropped", "done 🎉 nice", "done nice", "🎉"),
    ("e.g.", "use e.g. the seed CLI", "use for example, the seed CLI", "e.g."),
    ("for e.g. no doubling", "read it for e.g. testing", "read it for example, testing", "for for"),
    ("thousands separator", "cost $1,234.56 total", "cost $1234.56 total", ","),
    ("ellipsis", "thinking... done", "thinking, done", "..."),
    ("em dash", "sent — see UI", "sent, see UI", "—"),
    ("digit range", "items 4–6 exist", "items 4 to 6 exist", "–"),
    ("prose dash, flags kept", "a -- b and `seed --list` ok",
     "a, b and seed --list ok", "seed to list"),
    ("ampersand", "web & worker", "web and worker", "&"),
    ("comma-period collapse", "trace sent,. Next", "trace sent. Next", ",."),
    ("secret key never spelled", "using sk-lf-1a2b3c4d5e6f7a8b9c0d now",
     "using secret key now", "1a2b"),
    ("power evaluated", "the buffer is 2**8 bytes wide", "256 bytes wide", "**"),
    ("power spelled out when huge", "a 7**20 search space",
     "7 to the power of 20", "**"),
    ("power beside bold kept separate", "2**8 and **bold** text",
     "256 and bold text", "power of bold"),
    ("spaced angle pair spoken (WORD_SUBS)", "value < n and value > 0",
     "value less than n and value greater than 0", "<"),
    ("real html tag still stripped", "click <b>bold</b> link",
     "click bold link", "<b>"),
    ("uuid suppressed", "trace 123e4567-e89b-12d3-a456-426614174000 done",
     "trace unique identifier done", "4567"),
    ("hex blob suppressed", "verify 9f2c8e0417ab53de9f2c8e0417ab53de ok",
     "verify hash value ok", "9f2c"),
    ("long token w/ digit", "id abc123xyz890qwerty12 here", "id identifier here", "abc123xyz"),
    ("all caps word", "the GENERATION is slow", "the generation is slow", "GENERATION"),
    ("foreign script stripped", "结果 ok and Привет now", "ok and now", "结果"),
    ("colon port", "served at localhost:3000 now", "served at localhost 3000 now", ":3000"),
    ("symbol run", "wow~~~~ really", "wow~ really", "~~~~"),
    ("stutter collapse", "and and and done", "and done", ", and and"),
]


def _selftest() -> int:
    failed = 0
    for desc, inp, expect, no in _SELFTESTS:
        out = sanitize(inp)
        ok = expect in out and no not in out
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
        print('Usage: tts_sanitize.py "text" | --selftest', file=sys.stderr)
        sys.exit(1)
    print(sanitize(sys.argv[1]), end="")