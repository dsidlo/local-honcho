"""Scrub secrets out of text before Grok puts it in a model request.

The Honcho tailer has its own scrubber. This package is the one the Grok
hook runs, and it is a superset of those patterns: Gemini keys, ``sk-proj``
keys, JWTs, and assigned passwords are covered here.
"""

from scrubber.patterns import REDACTED, contains_secret, scrub, scrub_tree

__all__ = ["REDACTED", "contains_secret", "scrub", "scrub_tree"]
