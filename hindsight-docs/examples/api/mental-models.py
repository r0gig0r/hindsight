"""Mental Models API examples for documentation."""

from hindsight_client import Hindsight

client = Hindsight()

# [docs:mm-set-mission]
client.set_mission(
    bank_id="my-agent",
    mission="Be a PM for the engineering team, tracking sprint progress and team capacity"
)
# [/docs:mm-set-mission]


# [docs:mm-set-mission-alt]
client.set_mission(
    bank_id="pm-agent",
    mission="Be a PM for the engineering team, tracking sprint progress, team capacity, and technical decisions"
)
# [/docs:mm-set-mission-alt]


# [docs:mm-list]
# List all mental models
models = client.list_mental_models(bank_id="my-agent")

# Filter by subtype
structural_models = client.list_mental_models(
    bank_id="my-agent",
    subtype="structural"
)

# Filter by tags
user_models = client.list_mental_models(
    bank_id="my-agent",
    tags=["user_alice"],
    tags_match="any"  # "any", "all", "any_strict", "all_strict"
)
# [/docs:mm-list]


# [docs:mm-get]
model = client.get_mental_model(bank_id="my-agent", model_id="alice")

print(f"Name: {model.name}")
print(f"Description: {model.description}")
print(f"Version: {model.version}")

for obs in model.observations:
    print(f"- {obs.title} ({obs.trend})")
    for evidence in obs.evidence:
        print(f"  Quote: {evidence.quote}")
# [/docs:mm-get]


# [docs:mm-create]
# Create a pinned model (observations generated on refresh)
model = client.create_mental_model(
    bank_id="my-agent",
    name="Product Roadmap",
    description="Track product priorities and feature decisions",
    tags=["project_alpha"]
)

# Create a directive (user-defined observations, never auto-modified)
directive = client.create_mental_model(
    bank_id="my-agent",
    name="Response Guidelines",
    subtype="directive",
    tags=["user_alice"],
    observations=[
        {
            "title": "Always respond in French",
            "content": "All responses must be in French regardless of input language"
        },
        {
            "title": "Never mention competitors",
            "content": "Do not reference or compare to competitor products"
        }
    ]
)
# [/docs:mm-create]


# [docs:mm-pinned]
client.create_mental_model(
    bank_id="my-agent",
    name="Product Roadmap",
    description="Track product priorities and feature decisions"
)
# [/docs:mm-pinned]


# [docs:mm-directive]
client.create_mental_model(
    bank_id="support-agent",
    name="Response Guidelines",
    subtype="directive",
    observations=[
        {"title": "Always respond in French", "content": "All responses must be in French"},
        {"title": "Never mention competitors", "content": "Do not reference competitor products"}
    ]
)
# [/docs:mm-directive]


# [docs:mm-delete]
client.delete_mental_model(bank_id="my-agent", model_id="old-model")
# [/docs:mm-delete]


# [docs:mm-refresh]
# Refresh all mental models
result = client.refresh_mental_models(bank_id="my-agent")
print(f"Operation ID: {result.operation_id}")

# Refresh only structural models (from mission)
client.refresh_mental_models(bank_id="my-agent", subtype="structural")

# Refresh only emergent models (from data patterns)
client.refresh_mental_models(bank_id="my-agent", subtype="emergent")

# Apply tags to newly created models
client.refresh_mental_models(bank_id="my-agent", tags=["user_alice"])
# [/docs:mm-refresh]


# [docs:mm-refresh-simple]
# Refresh all mental models
client.refresh_mental_models(bank_id="my-agent")

# Refresh only structural models
client.refresh_mental_models(bank_id="my-agent", subtype="structural")

# Refresh a single mental model
client.refresh_mental_model(bank_id="my-agent", model_id="alice")
# [/docs:mm-refresh-simple]


# [docs:mm-refresh-single]
result = client.refresh_mental_model(bank_id="my-agent", model_id="alice")
print(f"Operation ID: {result.operation_id}")
# [/docs:mm-refresh-single]


# [docs:mm-freshness]
model = client.get_mental_model(bank_id="my-agent", model_id="alice")

if not model.freshness.is_up_to_date:
    print(f"Needs refresh: {model.freshness.reasons}")
    print(f"Memories since last refresh: {model.freshness.memories_since_refresh}")

    # Trigger refresh
    client.refresh_mental_model(bank_id="my-agent", model_id="alice")
# [/docs:mm-freshness]


# [docs:mm-freshness-check]
model = client.get_mental_model(bank_id="my-agent", model_id="alice")
print(model.freshness)
# {
#   "is_up_to_date": false,
#   "last_refresh_at": "2025-12-01T10:30:00Z",
#   "memories_since_refresh": 47,
#   "reasons": ["new_memories", "mission_changed"]
# }
# [/docs:mm-freshness-check]


# [docs:mm-versions]
# List all versions
versions = client.list_mental_model_versions(
    bank_id="my-agent",
    model_id="alice"
)

for v in versions:
    print(f"Version {v.version}: {v.created_at}")

# Get a specific version
v2 = client.get_mental_model_version(
    bank_id="my-agent",
    model_id="alice",
    version=2
)

print(f"Observations at v2: {len(v2.observations)}")
# [/docs:mm-versions]


# [docs:mm-versions-simple]
# List all versions
versions = client.list_mental_model_versions(bank_id="my-agent", model_id="alice")

# Get specific historical version
v2 = client.get_mental_model_version(bank_id="my-agent", model_id="alice", version=2)
# [/docs:mm-versions-simple]


# [docs:mm-tags-refresh]
# Create structural/emergent models tagged for a specific user
client.refresh_mental_models(
    bank_id="my-agent",
    tags=["user_alice"]
)

# Refresh only structural models with tags
client.refresh_mental_models(
    bank_id="my-agent",
    subtype="structural",
    tags=["user_alice"]
)
# [/docs:mm-tags-refresh]


# [docs:mm-tags-filter]
# Get all models for a user
models = client.list_mental_models(
    bank_id="my-agent",
    tags=["user_alice"],
    tags_match="any"
)

# Get models matching all specified tags
models = client.list_mental_models(
    bank_id="my-agent",
    tags=["user_alice", "project_alpha"],
    tags_match="all"
)
# [/docs:mm-tags-filter]


# [docs:mm-reflect]
# Reflect with all mental models
response = client.reflect(
    bank_id="my-agent",
    query="Should we promote Alice to team lead?"
)

# Reflect scoped to a specific user's mental models
response = client.reflect(
    bank_id="my-agent",
    query="What should I focus on today?",
    tags=["user_alice"],
    tags_match="any"
)
# [/docs:mm-reflect]


# [docs:mm-tags-scoping]
# Create models for a specific user
client.refresh_mental_models(bank_id="my-agent", tags=["user_alice"])

# Create a directive scoped to a user
client.create_mental_model(
    bank_id="my-agent",
    name="Alice's Guidelines",
    subtype="directive",
    tags=["user_alice"],
    observations=[{"title": "Prefer detailed explanations", "content": "Alice prefers thorough explanations"}]
)

# Reflect using only Alice's context
response = client.reflect(
    bank_id="my-agent",
    query="What should I focus on?",
    tags=["user_alice"]
)
# [/docs:mm-tags-scoping]
