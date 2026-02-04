---
sidebar_position: 1
---

# Python Client

Official Python client for the Hindsight API.

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

## Installation

<Tabs>
<TabItem value="all-in-one" label="All-in-One (Recommended)">

The `hindsight-all` package includes embedded PostgreSQL, HTTP API server, and client:

```bash
pip install hindsight-all
```

</TabItem>
<TabItem value="client-only" label="Client Only">

If you already have a Hindsight server running:

```bash
pip install hindsight-client
```

</TabItem>
</Tabs>

## Quick Start

<Tabs>
<TabItem value="all-in-one" label="All-in-One">

```python
import os
from hindsight import HindsightServer, HindsightClient

with HindsightServer(
    llm_provider="openai",
    llm_model="gpt-4o-mini",
    llm_api_key=os.environ["OPENAI_API_KEY"]
) as server:
    client = HindsightClient(base_url=server.url)

    # Retain a memory
    client.retain(bank_id="my-bank", content="Alice works at Google")

    # Recall memories
    results = client.recall(bank_id="my-bank", query="What does Alice do?")
    for r in results:
        print(r.text)

    # Reflect - generate response with disposition
    answer = client.reflect(bank_id="my-bank", query="Tell me about Alice")
    print(answer.text)
```

</TabItem>
<TabItem value="client-only" label="Client Only">

```python
from hindsight_client import Hindsight

client = Hindsight(base_url="http://localhost:8888")

# Retain a memory
client.retain(bank_id="my-bank", content="Alice works at Google")

# Recall memories
results = client.recall(bank_id="my-bank", query="What does Alice do?")
for r in results:
    print(r.text)

# Reflect - generate response with disposition
answer = client.reflect(bank_id="my-bank", query="Tell me about Alice")
print(answer.text)
```

</TabItem>
</Tabs>

## Embedded Client (Easiest Option)

`HindsightEmbedded` provides the simplest way to use Hindsight in Python. It uses the same daemon management interface as the `hindsight-embed` CLI, ensuring full compatibility and profile sharing:

```python
from hindsight import HindsightEmbedded
import os

# Daemon starts automatically on first use
client = HindsightEmbedded(
    profile="myapp",                        # Profile for data isolation
    llm_provider="openai",
    llm_model="gpt-4o-mini",
    llm_api_key=os.environ["OPENAI_API_KEY"],
)

# Use immediately - no manual daemon management needed
client.retain(bank_id="my-bank", content="Alice works at Google")
results = client.recall(bank_id="my-bank", query="What does Alice do?")

# Daemon continues running (auto-stops after idle timeout)
# Or explicitly stop it:
client.close(stop_daemon=True)
```

### Features

- **Daemon Management**: Uses same interface as `hindsight-embed` CLI - both share the same daemon implementation
- **Lazy Start**: Daemon only starts when you make your first API call, not on client creation
- **Daemon Sharing**: Multiple SDK clients with same profile share one daemon process
- **Profile Isolation**: Each profile gets its own PostgreSQL database and dedicated daemon port
- **CLI Compatible**: Works seamlessly with profiles created by `hindsight-embed` CLI - same profile name = same data
- **Automatic Idle Timeout**: Daemon auto-stops after inactivity (default: 5 min, configurable)
- **Full API**: All `HindsightClient` methods available (retain, recall, reflect, banks, etc.)
- **Embedded PostgreSQL**: Uses pg0 by default for zero-config database storage

### How It Works

`HindsightEmbedded` uses the same daemon management functions as the `hindsight-embed` CLI:

1. **Profile Resolution**: Profile names are sanitized and mapped to `pg0://hindsight-embed-{profile}` databases
2. **Daemon Startup**: When you make your first API call, the daemon is started automatically if not running
3. **Port Allocation**: Each profile gets a dedicated port (stored in `~/.hindsight/daemon-{profile}.sock`)
4. **Data Storage**: Profile data is stored in `~/.pg0/instances/hindsight-embed-{profile}/`
5. **Daemon Reuse**: Multiple clients (SDK or CLI) with same profile share the same daemon
6. **Auto-Cleanup**: Daemon auto-stops after idle timeout (no requests for 5+ minutes)

This architecture ensures that:
- SDK and CLI are fully interoperable
- No resource duplication (one daemon per profile, not per client)
- Profile data is consistent across SDK and CLI usage

**Example - SDK and CLI Working Together**:
```python
# In your Python app
from hindsight import HindsightEmbedded
import os

client = HindsightEmbedded(
    profile="myapp",
    llm_provider="openai",
    llm_api_key=os.environ["OPENAI_API_KEY"],
)

# Store some data
client.retain(bank_id="users", content="Alice works at Google")
print(f"Daemon running at: {client.url}")
```

```bash
# From terminal - inspect the same data
$ export OPENAI_API_KEY="..."
$ uvx hindsight-embed --profile myapp daemon status
Daemon Status (profile: myapp)
Status: Running
URL: http://127.0.0.1:54321
Database: pg0://hindsight-embed-myapp

$ uvx hindsight-embed --profile myapp recall users "Where does Alice work?"
✓ Google
```

Both SDK and CLI see the same data, share the same daemon process.

### Architecture Comparison

**HindsightEmbedded (Daemon-Based)**:
```
┌─────────────┐  ┌─────────────┐
│   Python    │  │     CLI     │
│     App     │  │   (uvx)     │
└──────┬──────┘  └──────┬──────┘
       │                │
       └────────┬───────┘
                │ HTTP
        ┌───────▼────────┐
        │  Daemon Process│
        │  (auto-managed)│
        └───────┬────────┘
                │
        ┌───────▼────────┐
        │   PostgreSQL   │
        │  (pg0 embedded)│
        └────────────────┘
```
- ✅ Daemon shared across clients/CLI
- ✅ Survives app restarts
- ✅ Profile isolation
- ✅ Lazy start, auto-cleanup

**HindsightServer (In-Process)**:
```
┌─────────────────────┐
│    Python App       │
│  ┌───────────────┐  │
│  │ HindsightServer│ │
│  │   (in-process) │ │
│  └───────┬────────┘  │
│          │           │
│  ┌───────▼────────┐  │
│  │  PostgreSQL    │  │
│  │(pg0 or external)│ │
│  └────────────────┘  │
└─────────────────────┘
```
- ✅ Full lifecycle control
- ✅ Immediate startup
- ❌ No daemon sharing
- ❌ No CLI interop
- ❌ Exits when app exits

### Usage Patterns

<Tabs>
<TabItem value="simple" label="Simple (Recommended)">

```python
from hindsight import HindsightEmbedded
import os

client = HindsightEmbedded(
    profile="myapp",
    llm_provider="openai",
    llm_model="gpt-4o-mini",
    llm_api_key=os.environ["OPENAI_API_KEY"],
)

# Use anywhere - auto-cleanup on exit
client.retain(bank_id="test", content="Some content")
results = client.recall(bank_id="test", query="content")
```

</TabItem>
<TabItem value="context" label="Context Manager">

```python
from hindsight import HindsightEmbedded
import os

with HindsightEmbedded(
    profile="myapp",
    llm_provider="openai",
    llm_model="gpt-4o-mini",
    llm_api_key=os.environ["OPENAI_API_KEY"],
) as client:
    client.retain(bank_id="test", content="Some content")
    results = client.recall(bank_id="test", query="content")
# Server stops here
```

</TabItem>
<TabItem value="long-running" label="Long-Running App">

```python
from hindsight import HindsightEmbedded
import os

class MyApplication:
    def __init__(self):
        self.memory = HindsightEmbedded(
            profile="myapp",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_api_key=os.environ["OPENAI_API_KEY"],
        )

    def process_message(self, user_id: str, message: str):
        self.memory.retain(bank_id=user_id, content=message)
        context = self.memory.recall(bank_id=user_id, query=message)
        response = self.memory.reflect(
            bank_id=user_id,
            query=f"How should I respond to: {message}?"
        )
        return response.text

    def shutdown(self):
        self.memory.close()
```

</TabItem>
</Tabs>

### Profile Isolation

Each profile creates an isolated data directory:

```python
# Different profiles = different data stores
client1 = HindsightEmbedded(profile="app1", llm_provider="openai", llm_api_key="...")
client2 = HindsightEmbedded(profile="app2", llm_provider="openai", llm_api_key="...")

# These are completely isolated
client1.retain(bank_id="test", content="App 1 data")
client2.retain(bank_id="test", content="App 2 data")

# Each sees only its own data
results1 = client1.recall(bank_id="test", query="data")  # Only "App 1 data"
results2 = client2.recall(bank_id="test", query="data")  # Only "App 2 data"
```

Data is stored in pg0's data directory:
- **All platforms**: `~/.pg0/instances/hindsight-embed-{profile}/`

This matches the `hindsight-embed` CLI behavior for consistency.

### Daemon Management

The SDK and CLI share the same daemon infrastructure. You can manage daemons using either:

**Check daemon status (CLI)**:
```bash
# Check if daemon is running for a profile
uvx hindsight-embed daemon status --profile myapp

# Example output:
# Daemon Status (profile: myapp)
# Status: Running
# URL: http://127.0.0.1:54321
# PID: 12345
# Database: pg0://hindsight-embed-myapp
```

**Start daemon with SDK, check with CLI**:
```python
from hindsight import HindsightEmbedded

# Start daemon via SDK
client = HindsightEmbedded(profile="myapp", llm_provider="openai", llm_api_key="...")
client.retain(bank_id="test", content="Hello world")
```

```bash
# Now check from CLI - it sees the same daemon
uvx hindsight-embed daemon status --profile myapp
# Status: Running (started by SDK)
```

**Stop daemon (CLI)**:
```bash
# Stop the daemon manually
uvx hindsight-embed daemon stop --profile myapp

# Or let it auto-stop after idle timeout
```

**Stop daemon (SDK)**:
```python
# Explicitly stop daemon when closing
client.close(stop_daemon=True)

# Or let it auto-stop (default)
client.close()  # Daemon continues running, auto-stops after idle timeout
```

**Important Notes**:
- Stopping a daemon affects all clients using that profile (SDK and CLI)
- By default, SDK's `close()` doesn't stop the daemon (relies on idle timeout)
- Use `close(stop_daemon=True)` only when you're sure no other clients need it
- Daemon auto-stops after 5 minutes of inactivity by default

### Configuration Options

```python
client = HindsightEmbedded(
    # Profile & Database
    profile="default",                    # Profile name (alphanumeric, -, _)
    database_url=None,                    # Custom DB URL (default: profile-specific pg0)

    # LLM Configuration
    llm_provider="openai",                # Provider: openai, anthropic, groq, ollama, gemini, lmstudio
    llm_api_key="your-key",               # API key for the provider
    llm_model="gpt-4o-mini",              # Model name (provider-specific)
    llm_base_url=None,                    # Custom base URL (for ollama, lmstudio, etc.)

    # Daemon Settings
    idle_timeout=300,                     # Seconds before auto-stop (default: 5 min)
    log_level="info",                     # Daemon log level: debug, info, warning, error
)
```

**Parameter Details**:

- **profile**: Name for data isolation. Sanitized to alphanumeric + `-` and `_`. Each profile gets its own daemon and database.

- **database_url**: Override default database. Use `None` or `"pg0"` for embedded PostgreSQL. For external PostgreSQL: `"postgresql://user:pass@host/db"`

- **llm_provider**: Must match your API key. See [LLM Configuration](/developer/configuration#llm-configuration) for provider-specific setup.

- **llm_model**: Model identifier varies by provider:
  - OpenAI: `gpt-4o-mini`, `gpt-4o`, `o3-mini`
  - Anthropic: `claude-sonnet-4-20250514`, `claude-opus-4-5-20251101`
  - Groq: `openai/gpt-oss-120b`, `llama-3.3-70b-versatile`
  - Ollama: `llama3.2`, `qwen2.5` (requires Ollama running locally)

- **idle_timeout**: Daemon auto-stops after this many seconds of inactivity. Set higher for long-running apps, lower for development.

- **log_level**: Controls daemon verbosity. Use `"debug"` for troubleshooting, `"warning"` for production.

### When to Use What

| Feature | HindsightEmbedded | HindsightServer + Client | Hindsight CLI |
|---------|-------------------|-------------------------|---------------|
| **Setup** | ✅ Easiest (1 line) | ⚠️ Moderate (2-3 lines) | ⚠️ Requires uvx |
| **Daemon Mgmt** | ✅ Automatic (shared) | ❌ In-process only | ✅ Automatic (shared) |
| **Lazy Start** | ✅ Yes | ❌ No | ❌ Starts immediately |
| **Cleanup** | ✅ Automatic | ⚠️ Manual | ✅ Auto-timeout |
| **Profile Support** | ✅ Built-in | ❌ No | ✅ Built-in |
| **CLI Interop** | ✅ Full (shared daemon) | ❌ None | ✅ Full |
| **Use Case** | Python apps | Testing, custom control | CLI scripts, interactive |

**Use HindsightEmbedded when:**
- Building Python applications that need memory
- You want the simplest Python API
- You want daemon sharing and profile isolation
- You need CLI interoperability (e.g., inspect data with CLI)
- Building long-running applications

**Use HindsightServer + HindsightClient when:**
- You need explicit in-process server lifecycle control
- Testing or development (immediate startup/shutdown)
- Building microservices with custom initialization
- You don't need profile isolation or daemon sharing

**Use Hindsight CLI when:**
- Running commands from terminal or shell scripts
- Interactive exploration of profiles and data
- Managing daemons manually
- Quick testing without writing Python code

### Troubleshooting

**Check if daemon is running**:
```bash
uvx hindsight-embed daemon status --profile myapp
```

**Daemon won't start**:
- Check if port is already in use: `lsof -i :PORT` (port from daemon status)
- Check daemon logs: Look for errors in the daemon output
- Verify LLM credentials: `HINDSIGHT_API_LLM_API_KEY` is set correctly
- Try stopping and restarting: `uvx hindsight-embed daemon stop --profile myapp`

**"Cannot use HindsightEmbedded after it has been closed" error**:
```python
# Don't reuse a closed client
client = HindsightEmbedded(profile="myapp", ...)
client.close()
client.recall(...)  # ❌ Error!

# Create a new client instead
client = HindsightEmbedded(profile="myapp", ...)  # ✅ OK
client.recall(...)
```

**Multiple clients not sharing daemon**:
- Ensure profile names match exactly (case-sensitive)
- Check if one client has custom `database_url` set
- Verify both clients use same profile directory

**Daemon keeps running after app exits**:
- This is normal! Daemon has idle timeout (default: 5 min)
- To stop immediately: `client.close(stop_daemon=True)` or `uvx hindsight-embed daemon stop`
- To change timeout: Set `idle_timeout=60` (seconds) in `HindsightEmbedded()`

**View daemon logs**:
```python
# Set log level to see more details
client = HindsightEmbedded(
    profile="myapp",
    log_level="debug",  # or "info", "warning", "error"
    llm_provider="openai",
    llm_api_key="...",
)
```

### Thread Safety

`HindsightEmbedded` is thread-safe for daemon initialization. Multiple threads can safely call methods on the same client instance:

```python
from hindsight import HindsightEmbedded
from concurrent.futures import ThreadPoolExecutor
import os

client = HindsightEmbedded(
    profile="myapp",
    llm_provider="openai",
    llm_api_key=os.environ["OPENAI_API_KEY"],
)

def process_message(message: str):
    # Safe to call from multiple threads
    client.retain(bank_id="messages", content=message)
    results = client.recall(bank_id="messages", query=message)
    return results

with ThreadPoolExecutor(max_workers=10) as executor:
    messages = ["Message 1", "Message 2", "Message 3"]
    results = executor.map(process_message, messages)
```

**Notes**:
- Daemon startup is protected by a thread lock (only happens once)
- After startup, all calls go through the HTTP client (which is thread-safe)
- For async code, use the async methods: `aretain()`, `arecall()`, `areflect()`

## Client Initialization

```python
from hindsight_client import Hindsight

client = Hindsight(
    base_url="http://localhost:8888",  # Hindsight API URL
    timeout=30.0,                       # Request timeout in seconds
)
```

## Core Operations

### Retain (Store Memory)

```python
# Simple
client.retain(
    bank_id="my-bank",
    content="Alice works at Google as a software engineer",
)

# With options
from datetime import datetime

client.retain(
    bank_id="my-bank",
    content="Alice got promoted",
    context="career update",
    timestamp=datetime(2024, 1, 15),
    document_id="conversation_001",
    metadata={"source": "slack"},
)
```

### Retain Batch

```python
client.retain_batch(
    bank_id="my-bank",
    items=[
        {"content": "Alice works at Google", "context": "career"},
        {"content": "Bob is a data scientist", "context": "career"},
    ],
    document_id="conversation_001",
    retain_async=False,  # Set True for background processing
)
```

### Recall (Search)

```python
# Simple - returns list of RecallResult
results = client.recall(
    bank_id="my-bank",
    query="What does Alice do?",
)

for r in results.results:
    print(f"{r.text} (type: {r.type})")

# With options
results = client.recall(
    bank_id="my-bank",
    query="What does Alice do?",
    types=["world", "observation"],  # Filter by fact type
    max_tokens=4096,
    budget="high",  # low, mid, or high
)
```

### Recall with Chunks

```python
# Returns RecallResponse with source chunks
response = client.recall(
    bank_id="my-bank",
    query="What does Alice do?",
    types=["world", "experience"],
    budget="mid",
    max_tokens=4096,
    include_chunks=True,
    max_chunk_tokens=500
)

print(f"Found {len(response.results)} memories")
for r in response.results:
    print(f"  - {r.text}")
    if r.chunks:
        print(f"    Source: {r.chunks[0].text[:100]}...")
```

### Reflect (Generate Response)

```python
answer = client.reflect(
    bank_id="my-bank",
    query="What should I know about Alice?",
    budget="low",  # low, mid, or high
    context="preparing for a meeting",
)

print(answer.text)  # Generated response
```

## Bank Management

### Create Bank

```python
client.create_bank(
    bank_id="my-bank",
    name="Assistant",
    mission="You're a helpful AI assistant - keep track of user preferences and conversation history.",
    disposition={
        "skepticism": 3,    # 1-5: trusting to skeptical
        "literalism": 3,    # 1-5: flexible to literal
        "empathy": 3,       # 1-5: detached to empathetic
    },
)
```

### List Memories

```python
client.list_memories(
    bank_id="my-bank",
    type="world",  # Optional: filter by type
    search_query="Alice",  # Optional: text search
    limit=100,
    offset=0,
)
```

## Async Support

All methods have async versions prefixed with `a`:

```python
import asyncio
from hindsight_client import Hindsight

async def main():
    client = Hindsight(base_url="http://localhost:8888")

    # Async retain
    await client.aretain(bank_id="my-bank", content="Hello world")

    # Async recall
    results = await client.arecall(bank_id="my-bank", query="Hello")
    for r in results:
        print(r.text)

    # Async reflect
    answer = await client.areflect(bank_id="my-bank", query="What did I say?")
    print(answer.text)

    client.close()

asyncio.run(main())
```

## Context Manager

```python
from hindsight_client import Hindsight

with Hindsight(base_url="http://localhost:8888") as client:
    client.retain(bank_id="my-bank", content="Hello")
    results = client.recall(bank_id="my-bank", query="Hello")
# Client automatically closed
```
