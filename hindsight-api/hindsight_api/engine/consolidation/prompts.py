"""Prompts for the consolidation engine."""

CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation system. Your job is to identify when a new fact should be merged with existing knowledge (mental models).

You must output ONLY valid JSON with no markdown formatting, no code blocks, and no additional text.

## MERGE RULES (same subject + same topic):
1. REDUNDANT: Same information worded differently → update existing
2. CONTRADICTION: Opposite information about same topic → update with history (e.g., "used to X, now Y")
3. UPDATE: New state replacing old state → update with history

## TAG ROUTING RULES:
Tags define visibility scopes. The fact and each mental model have tags (can be empty = global).

| Fact Tags | Model Tags | Action |
|-----------|------------|--------|
| [alice] | [alice] | UPDATE the model (same scope) |
| [alice] | [] | UPDATE the model (global absorbs all scopes) |
| [alice] | [bob] | CREATE new untagged model (cross-scope insight) |
| [] | [alice] | UPDATE the model (untagged facts can update any scope) |
| [] | [] | UPDATE the model (global to global) |

When NO existing model matches the fact's topic: CREATE new model with fact's tags.

## MULTIPLE ACTIONS:
One fact can trigger MULTIPLE actions. For example:
- Update a scoped model [alice] about pizza preferences
- AND update a global model [] about pizza in general

Output an ARRAY of actions (can be empty, one, or many).

## CRITICAL RULES:
- NEVER merge facts about DIFFERENT people
- NEVER merge unrelated topics (food preferences vs work vs hobbies)
- When merging contradictions, capture the CHANGE (before → after)
- Keep mental models focused on ONE specific topic per person
- Cross-scope insights (alice's fact about bob's topic) become UNTAGGED (global)"""

CONSOLIDATION_USER_PROMPT = """Analyze this new fact against existing mental models.
{mission_section}
NEW FACT: {fact_text}
FACT TAGS: {fact_tags}

EXISTING MENTAL MODELS:
{mental_models_text}

For each relevant mental model, decide: UPDATE existing or CREATE new?
- Same scope (tags match or model is global): UPDATE
- Different scope (both have non-overlapping tags): CREATE untagged cross-scope insight
- No match found: CREATE with fact's tags

Output JSON array of actions:
[
  {{"action": "update", "learning_id": "uuid", "text": "updated text", "reason": "..."}},
  {{"action": "create", "tags": ["tag"], "text": "new learning", "reason": "..."}}
]

If NO consolidation is needed (fact is unrelated to all models), output empty array:
[]

If no models exist but fact should become a learning, output create action:
[{{"action": "create", "tags": {fact_tags}, "text": "learning text", "reason": "new topic"}}]"""

NEW_LEARNING_SYSTEM_PROMPT = """You are a memory consolidation system. Your job is to convert facts into clear, memorable knowledge statements.

You must output ONLY valid JSON with no markdown formatting, no code blocks, and no additional text.

## EXTRACT DURABLE KNOWLEDGE, NOT EPHEMERAL STATE
Facts often describe events or actions. Extract the DURABLE KNOWLEDGE implied by the fact, not the transient state.

Examples of extracting durable knowledge:
- "User moved to Room 203" -> "Room 203 exists" (location exists, not where user is now)
- "User visited Patterson Law at Room 203" -> "Patterson Law is located in Room 203"
- "User took the elevator to floor 3" -> "Floor 3 is accessible by elevator"
- "User met John at the coffee shop" -> "John frequents the coffee shop" or just the fact itself

DO NOT track current user position/state as knowledge - that changes constantly.
DO track permanent facts learned from the user's actions.

## PRESERVE SPECIFIC DETAILS
Keep names, locations, numbers, and other specifics. Do NOT:
- Abstract into general principles
- Generate business insights
- Make knowledge generic

GOOD examples:
- Fact: "John likes pizza" -> "John likes pizza"
- Fact: "Alice works at Google" -> "Alice works at Google"

BAD examples:
- "John likes pizza" -> "Understanding dietary preferences helps..." (TOO ABSTRACT)
- "User is at Room 203" -> "User is currently at Room 203" (EPHEMERAL STATE)"""

NEW_LEARNING_USER_PROMPT = """Convert this fact into a clear, durable knowledge statement.
{mission_section}
FACT: {fact_text}

IMPORTANT: Extract PERMANENT knowledge from this fact, not transient user state.
If the fact describes user movement/location, extract what it tells us about the environment, not where the user currently is.

Output JSON:
{{
  "learning_text": "..."  // Durable knowledge (not "user is currently at X")
}}"""
