"""
Minimal prompts for the deriver module optimized for speed.

This module contains simplified prompt templates focused only on observation extraction.
NO peer card instructions, NO working representation - just extract observations.
"""

from functools import cache
from inspect import cleandoc as c

from src.utils.tokens import estimate_tokens


def minimal_deriver_prompt(
    peer_id: str,
    messages: str,
    max_embedding_tokens: int = 2048,
) -> str:
    """
    Generate minimal prompt for fast observation extraction.

    Args:
        peer_id: The ID of the user being analyzed.
        messages: All messages in the range (interleaving messages and new turns combined).
        max_embedding_tokens: Maximum tokens the embedding model can handle per observation.

    Returns:
        Formatted prompt string for observation extraction.
    """
    # Estimate character limit (approx 4 chars per token)
    char_limit = max_embedding_tokens * 4
    
    return c(
        f"""
Analyze messages from {peer_id} to extract **explicit atomic facts** about them.

CRITICAL: Return ONLY valid JSON. No markdown, no code blocks, no explanatory text. Just raw JSON.

CRITICAL - EMBEDDING CONTEXT LIMIT:
Each observation "content" MUST fit within {max_embedding_tokens} tokens (~{char_limit} characters) when embedded.
If an observation would exceed this limit, automatically summarize it to fit within this constraint.

STRATEGY FOR LONG CONTENT:
- For short messages: Extract verbatim atomic facts
- For messages exceeding ~{char_limit} characters (≈{max_embedding_tokens} tokens):
  SUMMARIZE the key information concisely instead of verbatim extraction
- Example: Instead of "user wrote: [3000 char paragraph]", output "user discussed [main topic]"
- Break complex explanations into multiple focused observations rather than one long one

EXAMPLE INPUT AND OUTPUT:
Input: "I just moved to Seattle last month after working at Microsoft for 5 years. I have a golden retriever named Max who loves the dog parks here. I'm a software engineer working on AI systems at a new startup in Belltown."

Output:
{{"explicit":[{{"content":"{peer_id} moved to Seattle"}},{{"content":"{peer_id} moved last month"}},{{"content":"{peer_id} worked at Microsoft"}},{{"content":"{peer_id} worked at Microsoft for 5 years"}},{{"content":"{peer_id} has a golden retriever"}},{{"content":"{peer_id} has a dog named Max"}},{{"content":"{peer_id} is a software engineer"}},{{"content":"{peer_id} works on AI systems"}},{{"content":"{peer_id} works at a startup in Belltown"}}]}}

REQUIRED JSON FORMAT:
{{
  "explicit": [
    {{"content": "ONE fact about {peer_id}"}},
    {{"content": "ONE fact about {peer_id}"}}
  ]
}}

RULES:
1. Return ONLY the JSON object above, no other text
2. EACH observation MUST fit within {max_embedding_tokens} tokens (~{char_limit} chars)
3. Create 10-20 short observations rather than 5 long ones
4. Use "{peer_id}" in each observation
5. Prefer simple sentences
6. If content is too long, SUMMARIZE instead of copying verbatim

Messages to analyze:
<messages>
{messages}
</messages>

Return ONLY valid JSON with focused observations under {char_limit} characters each."""
    )


@cache
def estimate_minimal_deriver_prompt_tokens(max_embedding_tokens: int = 2048) -> int:
    """Estimate base prompt tokens (cached)."""
    try:
        prompt = minimal_deriver_prompt(
            peer_id="",
            messages="",
            max_embedding_tokens=max_embedding_tokens,
        )
        return estimate_tokens(prompt)
    except Exception:
        return 400  # Rough estimate for new longer prompt
