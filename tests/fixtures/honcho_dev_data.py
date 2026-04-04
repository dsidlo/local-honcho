"""
Test fixtures extracted from honcho_dev database.

This module contains real data extracted from the honcho_dev database
to be used as test fixtures for agent tools and search functionality tests.
"""

# Sample workspace
HONCHO_DEV_WORKSPACE = "-pi"

# Sample peers from honcho_dev
HONCHO_DEV_PEERS = [
    {"name": "agent-pi-mono", "workspace_name": "-pi"},
    {"name": "assistant", "workspace_name": "-pi"},
    {"name": "assistant-dsidlo", "workspace_name": "-pi"},
    {"name": "dsidlo", "workspace_name": "-pi"},
]

# Sample documents (observations) with embeddings from honcho_dev
# These have real embeddings for semantic search tests
HONCHO_DEV_DOCUMENTS = [
    {
        "id": "vqHSXXJX23jOE9WH85iPy",
        "content": "agent-pi-mono provided instructions for rotating Telegram bot tokens",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774736201459",
    },
    {
        "id": "y2i67aL9NGsSFyBH99Vn3",
        "content": "agent-pi-mono calculates cosine similarity on Query/Key descriptors",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774765494860",
    },
    {
        "id": "IcXu8r-KLZKexJD4R2Myr",
        "content": "agent-pi-mono stated pi-treesitter provides the tool read_code_structure",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774356817846",
    },
    {
        "id": "c472OBpEmvv_86lBDXcKN",
        "content": "dsidlo asked what needs to be changed for the dytopo agent harness",
        "observer": "dsidlo",
        "observed": "dsidlo",
        "level": "explicit",
        "session_name": "pi-1774765494860",
    },
    {
        "id": "LFwTpXvGF5BZdBUrfNZZ2",
        "content": "agent-pi-mono identified 27 .env files during the investigation",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774736201459",
    },
    {
        "id": "iMYVb9U0v_XXHksWQvqjq",
        "content": "agent-pi-mono fixed regex syntax in node G on line 11",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774568199754",
    },
    {
        "id": "vaw91HTAtXHxyZcN3XEA8",
        "content": "agent-pi-mono reported Redis-CLI Prompt Injections were removed",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774909596642",
    },
    {
        "id": "P_pvmHfjs-q6-LZ3k1PsG",
        "content": "agent-pi-mono used tool read with call id call_l9nxyaaa",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774909596642",
    },
    {
        "id": "F-w9wyexHS2LqpGuQVtL_",
        "content": "agent-pi-mono reads worker responses from Redis for semantic matching",
        "observer": "agent-pi-mono",
        "observed": "agent-pi-mono",
        "level": "explicit",
        "session_name": "pi-1774765494860",
    },
    {
        "id": "oQ7nLHIFo6-BIDIkx9-QA",
        "content": "dsidlo mentioned SKILL.md files in @agent/skills/dytopo-skills/",
        "observer": "dsidlo",
        "observed": "dsidlo",
        "level": "explicit",
        "session_name": "pi-1774909596642",
    },
]

# Sample messages from honcho_dev
HONCHO_DEV_MESSAGES = [
    {
        "public_id": "2WEVP4uNb7edkixHOXWbG",
        "content": "Starting turn 5",
        "session_name": "pi-1774908328974",
        "peer_name": "agent-pi-mono",
    },
    {
        "public_id": "w4KCuEH6NNGx6ftrL29Is",
        "content": "Observation (bash):\n",
        "session_name": "pi-1774773372879",
        "peer_name": "agent-pi-mono",
    },
    {
        "public_id": "dS91rbjiTIBUbZ7qpQDhP",
        "content": "",
        "session_name": "pi-1774258424500",
        "peer_name": "agent-pi-mono",
    },
    {
        "public_id": "N7n3zsXmhoiCv-WjAMGX0",
        "content": "Starting turn 14",
        "session_name": "pi-1774765494860",
        "peer_name": "agent-pi-mono",
    },
    {
        "public_id": "iFEKBdgerotGvzLzW2l0J",
        "content": "",
        "session_name": "pi-1774909596642",
        "peer_name": "agent-pi-mono",
    },
]

# Sample sessions from honcho_dev
HONCHO_DEV_SESSIONS = [
    {"name": "pi-1774736201459", "workspace_name": "-pi"},
    {"name": "pi-1774765494860", "workspace_name": "-pi"},
    {"name": "pi-1774356817846", "workspace_name": "-pi"},
    {"name": "pi-1774568199754", "workspace_name": "-pi"},
    {"name": "pi-1774909596642", "workspace_name": "-pi"},
]