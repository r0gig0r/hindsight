#!/usr/bin/env python3
"""
Documentation Example Tester

Uses an LLM to extract code examples from documentation and test them.
This ensures documentation stays in sync with the actual codebase.

Usage:
    python scripts/test-doc-examples.py

Environment variables:
    OPENAI_API_KEY: Required for LLM calls
    HINDSIGHT_API_URL: URL of running Hindsight server (default: http://localhost:8888)
"""

import os
import re
import sys
import json
import glob
import subprocess
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import threading

from openai import OpenAI

# Thread-safe print lock
print_lock = threading.Lock()


def discover_and_install_dependencies(repo_root: str) -> dict:
    """Scan documentation for dependencies and install them."""
    print("\n=== Discovering and installing dependencies ===")

    results = {
        "python_packages": set(),
        "npm_packages": set(),
        "local_packages": [],
        "cli_available": False
    }

    # Find all markdown files
    md_files = []
    for pattern in ["**/*.md"]:
        md_files.extend(glob.glob(os.path.join(repo_root, pattern), recursive=True))

    # Patterns to find installation commands
    pip_pattern = re.compile(r'pip install\s+([^\s`\n]+)')
    npm_pattern = re.compile(r'npm install\s+([^\s`\n]+)')

    for md_file in md_files:
        # Skip symlinks to avoid circular references
        if os.path.islink(md_file):
            continue
        try:
            with open(md_file, 'r') as f:
                content = f.read()

            # Find pip install commands
            for match in pip_pattern.finditer(content):
                pkg = match.group(1).strip()
                if pkg and not pkg.startswith('-') and not pkg.startswith('.'):
                    results["python_packages"].add(pkg)

            # Find npm install commands
            for match in npm_pattern.finditer(content):
                pkg = match.group(1).strip()
                if pkg and not pkg.startswith('-') and not pkg.startswith('.'):
                    results["npm_packages"].add(pkg)
        except Exception:
            pass

    # Always include core local packages
    local_python_packages = [
        ("hindsight-clients/python", "hindsight_client"),
        ("hindsight-integrations/litellm", "hindsight_litellm"),
        ("hindsight-integrations/openai", "hindsight_openai"),
    ]

    # Install local Python packages
    print("\nInstalling local Python packages...")
    for pkg_path, pkg_name in local_python_packages:
        full_path = os.path.join(repo_root, pkg_path)
        if os.path.exists(full_path):
            try:
                subprocess.run(
                    ["uv", "pip", "install", "-e", full_path],
                    capture_output=True,
                    timeout=120
                )
                print(f"  Installed {pkg_name}")
                results["local_packages"].append(pkg_name)
            except Exception as e:
                print(f"  Failed to install {pkg_name}: {e}")

    # Install discovered Python packages (filter out local ones and known problematic ones)
    skip_packages = {"hindsight-client", "hindsight_client", "hindsight-litellm", "hindsight-openai",
                     "hindsight", ".", "..", "-e", "-r", "--upgrade"}
    external_packages = results["python_packages"] - skip_packages

    if external_packages:
        print(f"\nInstalling external Python packages: {external_packages}")
        for pkg in external_packages:
            try:
                subprocess.run(
                    ["uv", "pip", "install", pkg],
                    capture_output=True,
                    timeout=60
                )
            except Exception:
                pass

    # Build TypeScript client and set up symlink
    ts_client_path = os.path.join(repo_root, "hindsight-clients/typescript")
    if os.path.exists(ts_client_path):
        print("\nBuilding TypeScript client...")
        try:
            # Install npm dependencies
            subprocess.run(["npm", "ci"], cwd=ts_client_path, capture_output=True, timeout=120)
            # Build
            subprocess.run(["npm", "run", "build"], cwd=ts_client_path, capture_output=True, timeout=60)
            # Create symlink in /tmp for ESM module resolution
            os.makedirs("/tmp/node_modules/@vectorize-io", exist_ok=True)
            symlink_path = "/tmp/node_modules/@vectorize-io/hindsight-client"
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            os.symlink(ts_client_path, symlink_path)
            print("  TypeScript client built and symlinked")
        except Exception as e:
            print(f"  Failed to build TypeScript client: {e}")

    # Try to build hindsight CLI if Rust is available
    cli_path = os.path.join(repo_root, "hindsight-cli")
    if os.path.exists(cli_path):
        print("\nChecking for Rust toolchain...")
        try:
            cargo_result = subprocess.run(["cargo", "--version"], capture_output=True, timeout=5)
            if cargo_result.returncode == 0:
                print("  Rust found, building hindsight CLI...")
                build_result = subprocess.run(
                    ["cargo", "build", "--release"],
                    cwd=cli_path,
                    capture_output=True,
                    timeout=300
                )
                if build_result.returncode == 0:
                    # Try to install to PATH
                    cli_binary = os.path.join(cli_path, "target/release/hindsight")
                    if os.path.exists(cli_binary):
                        # Copy to /usr/local/bin or ~/.local/bin
                        local_bin = os.path.expanduser("~/.local/bin")
                        os.makedirs(local_bin, exist_ok=True)
                        import shutil
                        shutil.copy2(cli_binary, os.path.join(local_bin, "hindsight"))
                        os.environ["PATH"] = f"{local_bin}:{os.environ.get('PATH', '')}"
                        results["cli_available"] = True
                        print("  CLI built and installed")
                else:
                    print(f"  CLI build failed")
            else:
                print("  Rust not available, skipping CLI build")
        except FileNotFoundError:
            print("  Rust not installed, skipping CLI build")
        except Exception as e:
            print(f"  CLI build error: {e}")

    # Check if CLI is available (might be pre-installed)
    if not results["cli_available"]:
        try:
            result = subprocess.run(["hindsight", "--version"], capture_output=True, timeout=5)
            results["cli_available"] = result.returncode == 0
            if results["cli_available"]:
                print("  CLI already available in PATH")
        except Exception:
            pass

    print("\n=== Dependency installation complete ===\n")
    return results


@dataclass
class CodeExample:
    """Represents a code example extracted from documentation."""
    file_path: str
    language: str
    code: str
    context: str  # Surrounding text for context
    line_number: int


@dataclass
class TestResult:
    """Result of testing a code example."""
    example: CodeExample
    success: bool
    output: str
    error: Optional[str] = None


@dataclass
class TestReport:
    """Final test report."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[TestResult] = field(default_factory=list)
    created_banks: list[str] = field(default_factory=list)  # Track banks for cleanup

    def add_result(self, result: TestResult):
        self.total += 1
        self.results.append(result)
        if result.success:
            self.passed += 1
        elif result.error and "SKIPPED" in result.error:
            self.skipped += 1
        else:
            self.failed += 1

    def add_bank(self, bank_id: str):
        """Track a bank that was created during testing."""
        if bank_id not in self.created_banks:
            self.created_banks.append(bank_id)


def find_markdown_files(repo_root: str) -> list[str]:
    """Find all markdown files in the repository, excluding auto-generated docs."""
    md_files = []
    # Directories to skip (auto-generated docs, dependencies, etc.)
    skip_patterns = [
        "node_modules",
        ".git",
        "venv",
        "__pycache__",
        "hindsight_client_api/docs",  # Auto-generated OpenAPI docs
        "hindsight-clients/typescript/docs",  # Auto-generated TS docs
        "target/",  # Rust build artifacts
        "dist/",  # Build outputs
    ]
    for pattern in ["*.md", "**/*.md"]:
        for f in glob.glob(os.path.join(repo_root, pattern), recursive=True):
            # Skip symlinks to avoid circular references
            if os.path.islink(f):
                continue
            # Check if any parent directory is a symlink (circular reference protection)
            try:
                real_path = os.path.realpath(f)
                if not real_path.startswith(os.path.realpath(repo_root)):
                    continue
            except Exception:
                continue
            if any(skip in f for skip in skip_patterns):
                continue
            md_files.append(f)
    return sorted(set(md_files))


def extract_code_blocks(file_path: str) -> list[CodeExample]:
    """Extract code blocks from a markdown file."""
    with open(file_path, "r") as f:
        content = f.read()

    examples = []
    # Match fenced code blocks with language identifier
    pattern = r"```(\w+)\n(.*?)```"

    for match in re.finditer(pattern, content, re.DOTALL):
        language = match.group(1).lower()
        code = match.group(2).strip()

        # Get surrounding context (100 chars before and after)
        start = max(0, match.start() - 200)
        end = min(len(content), match.end() + 200)
        context = content[start:end]

        # Calculate line number
        line_number = content[:match.start()].count('\n') + 1

        # Only include testable languages
        if language in ["python", "typescript", "javascript", "bash", "sh"]:
            examples.append(CodeExample(
                file_path=file_path,
                language=language,
                code=code,
                context=context,
                line_number=line_number
            ))

    return examples


def analyze_example_with_llm(client: OpenAI, example: CodeExample, hindsight_url: str, repo_root: str, cli_available: bool = True) -> dict:
    """Use LLM to analyze a code example and determine how to test it."""

    cli_status = "AVAILABLE" if cli_available else "NOT INSTALLED - mark CLI examples as NOT testable"

    prompt = f"""Analyze this code example from documentation and determine how to test it.

File: {example.file_path}
Language: {example.language}
Line: {example.line_number}
Repository root: {repo_root}
Hindsight CLI status: {cli_status}

Context around the code:
{example.context}

Code:
```{example.language}
{example.code}
```

Your task:
1. Determine if this code example is testable (some are just fragments or pseudo-code)
2. If testable, generate a complete, runnable test script
3. The test should verify the example works correctly

IMPORTANT RULES:
- Hindsight API is ALREADY running at: {hindsight_url} - do NOT start Docker containers or servers
- Mark Docker/server setup examples as NOT testable (reason: "Server setup example - server already running")
- Mark pip/npm install commands as NOT testable (reason: "Package installation command")
- Mark code fragments that define classes/functions without calling them as NOT testable (reason: "Code fragment - defines but doesn't execute")
- Mark helm commands as NOT testable (reason: "Helm chart testing not supported")
- Use unique bank_id names like "doc-test-<random-uuid>" to avoid conflicts
- For cleanup, use requests.delete("{hindsight_url}/v1/default/banks/<bank_id>") - there is NO delete_bank() method
- For Python, wrap in try/finally to ensure cleanup runs, print "TEST PASSED" on success

WORKING DIRECTORY RULES:
- The test script will run from a temp directory, NOT from the repository
- Repository root is: {repo_root}
- If an example uses 'cd <dir>' or requires a specific working directory, convert to absolute paths
- For example: 'cd hindsight-api && uv run pytest' becomes 'cd {repo_root}/hindsight-api && uv run pytest'
- For cargo commands: run them from the correct absolute path (e.g., 'cd {repo_root}/hindsight-clients/rust && cargo test')
- Always use absolute paths based on the repository root when the example implies a specific directory

EXACT HINDSIGHT PYTHON CLIENT API (use EXACTLY these signatures):
```python
from hindsight_client import Hindsight

# Initialize client
client = Hindsight(base_url="{hindsight_url}")

# Store a single memory - use 'content' parameter, NOT 'items' or 'text'
response = client.retain(
    bank_id="my-bank",      # Required: string
    content="Memory text",   # Required: string - the memory content
    timestamp=None,          # Optional: datetime
    context=None,            # Optional: string
    document_id=None,        # Optional: string
    metadata=None,           # Optional: dict
)
# Returns RetainResponse with: success (bool), bank_id (str), items_count (int)

# Store multiple memories
response = client.retain_batch(
    bank_id="my-bank",
    items=[{{"content": "Memory 1"}}, {{"content": "Memory 2"}}],  # List of dicts with 'content' key
    document_id=None,
    retain_async=False,
)

# Recall memories
response = client.recall(
    bank_id="my-bank",
    query="search query",    # Required: string
    types=None,              # Optional: list of strings
    max_tokens=4096,
    budget="mid",            # "low", "mid", or "high"
)
# Returns RecallResponse with: results (list of RecallResult, each has .text attribute)

# Generate answer using memories
response = client.reflect(
    bank_id="my-bank",
    query="question",        # Required: string
    budget="low",            # "low", "mid", or "high"
    context=None,            # Optional: string
)
# Returns ReflectResponse with: text (str)

# Create a bank with profile
response = client.create_bank(
    bank_id="my-bank",
    name=None,               # Optional: string
    background=None,         # Optional: string
    disposition=None,        # Optional: dict
)

# Close client (important for cleanup)
client.close()
```

IMPORTANT: There is NO delete_bank() method. To delete a bank, use raw HTTP:
```python
import requests
requests.delete(f"{hindsight_url}/v1/default/banks/{{bank_id}}")
```

TYPESCRIPT/JAVASCRIPT RULES:
- ALWAYS use ES module syntax (import), NEVER use require()
- The package is '@vectorize-io/hindsight-client'
- NEVER import external packages like 'node-fetch', 'uuid', 'axios', etc. - use native APIs only
- Use native fetch() for HTTP requests (available in Node 18+)
- Use crypto.randomUUID() for generating UUIDs
- HindsightClient has NO close() method - do NOT call client.close()
- For cleanup, use native fetch() to DELETE banks
```typescript
import {{ HindsightClient }} from '@vectorize-io/hindsight-client';

const client = new HindsightClient({{ baseUrl: '{hindsight_url}' }});

// Retain
await client.retain('bank-id', 'content text');

// Recall
const response = await client.recall('bank-id', 'query');
for (const r of response.results) {{
    console.log(r.text);
}}

// Reflect
const answer = await client.reflect('bank-id', 'question');
console.log(answer.text);

// Create bank
await client.createBank('bank-id', {{ name: 'Name', background: 'Background' }});

// Cleanup - use native fetch, NOT client.close()
await fetch(`{hindsight_url}/v1/default/banks/${{bankId}}`, {{ method: 'DELETE' }});
```

BASH/CLI RULES:
- The CLI command is 'hindsight'
- Check the "Hindsight CLI status" above - if it says "NOT INSTALLED", mark ALL examples that use the 'hindsight' CLI command as NOT testable (reason: "CLI not installed")
- Mark 'cargo build' and 'cargo test' as NOT testable (reason: "Build command - too slow for CI")

Respond with JSON:
{{
    "testable": true/false,
    "reason": "Why it is or isn't testable",
    "language": "python|typescript|bash",
    "test_script": "Complete runnable test script that will exit 0 on success, non-zero on failure",
    "cleanup_script": "Optional cleanup script to run after test"
}}

If not testable, set test_script to null."""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0
    )

    return json.loads(response.choices[0].message.content)


def run_python_test(script: str, timeout: int = 60) -> tuple[bool, str, Optional[str]]:
    """Run a Python test script."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script)
        f.flush()

        try:
            result = subprocess.run(
                [sys.executable, f.name],
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            )
            output = result.stdout + result.stderr
            # Check for "TEST PASSED" in output as primary success indicator
            # This handles cases where exit code might be non-zero due to warnings
            if "TEST PASSED" in output:
                return True, output, None
            success = result.returncode == 0
            error = None if success else f"Exit code: {result.returncode}\n{result.stderr}"
            return success, output, error
        except subprocess.TimeoutExpired:
            return False, "", f"Test timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)
        finally:
            os.unlink(f.name)


def run_typescript_test(script: str, timeout: int = 60) -> tuple[bool, str, Optional[str]]:
    """Run a TypeScript/JavaScript test script."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mjs', delete=False) as f:
        f.write(script)
        f.flush()

        try:
            result = subprocess.run(
                ["node", f.name],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            success = result.returncode == 0
            output = result.stdout + result.stderr
            error = None if success else f"Exit code: {result.returncode}\n{result.stderr}"
            return success, output, error
        except subprocess.TimeoutExpired:
            return False, "", f"Test timed out after {timeout}s"
        except FileNotFoundError:
            return False, "", "Node.js not found"
        except Exception as e:
            return False, "", str(e)
        finally:
            os.unlink(f.name)


def run_bash_test(script: str, timeout: int = 60) -> tuple[bool, str, Optional[str]]:
    """Run a bash test script."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write("#!/bin/bash\nset -e\n" + script)
        f.flush()
        os.chmod(f.name, 0o755)

        try:
            result = subprocess.run(
                ["bash", f.name],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            success = result.returncode == 0
            output = result.stdout + result.stderr
            error = None if success else f"Exit code: {result.returncode}\n{result.stderr}"
            return success, output, error
        except subprocess.TimeoutExpired:
            return False, "", f"Test timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)
        finally:
            os.unlink(f.name)


def safe_print(*args, **kwargs):
    """Thread-safe print."""
    with print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()


def test_example(openai_client: OpenAI, example: CodeExample, hindsight_url: str, repo_root: str, cli_available: bool = True, debug: bool = False) -> TestResult:
    """Test a single code example."""
    safe_print(f"  Testing {example.file_path}:{example.line_number} ({example.language})")

    try:
        # Analyze with LLM
        analysis = analyze_example_with_llm(openai_client, example, hindsight_url, repo_root, cli_available)

        if debug and analysis.get("test_script"):
            safe_print(f"    [DEBUG] Generated script:\n{analysis.get('test_script')}")

        if not analysis.get("testable", False):
            safe_print(f"    SKIPPED: {analysis.get('reason', 'Not testable')}")
            return TestResult(
                example=example,
                success=True,
                output="",
                error=f"SKIPPED: {analysis.get('reason', 'Not testable')}"
            )

        test_script = analysis.get("test_script")
        if not test_script:
            safe_print(f"    SKIPPED: No test script generated")
            return TestResult(
                example=example,
                success=True,
                output="",
                error="SKIPPED: No test script generated"
            )

        # Run the test based on language
        lang = analysis.get("language", example.language)
        if lang == "python":
            success, output, error = run_python_test(test_script)
        elif lang in ["typescript", "javascript"]:
            success, output, error = run_typescript_test(test_script)
        elif lang in ["bash", "sh"]:
            success, output, error = run_bash_test(test_script)
        else:
            safe_print(f"    SKIPPED: Unsupported language {lang}")
            return TestResult(
                example=example,
                success=True,
                output="",
                error=f"SKIPPED: Unsupported language {lang}"
            )

        # Run cleanup if provided
        cleanup = analysis.get("cleanup_script")
        if cleanup and lang == "python":
            run_python_test(cleanup, timeout=30)

        if success:
            safe_print(f"    PASSED")
        else:
            safe_print(f"    FAILED: {error[:200] if error else 'Unknown error'}")

        return TestResult(
            example=example,
            success=success,
            output=output,
            error=error
        )

    except Exception as e:
        error_msg = f"Exception: {str(e)}\n{traceback.format_exc()}"
        safe_print(f"    ERROR: {error_msg[:200]}")
        return TestResult(
            example=example,
            success=False,
            output="",
            error=error_msg
        )


def cleanup_test_banks(hindsight_url: str, report: TestReport):
    """Clean up any banks created during testing."""
    import urllib.request
    import urllib.error

    # Also search for any doc-test-* banks that might have been left behind
    try:
        req = urllib.request.Request(f"{hindsight_url}/v1/default/banks")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            for bank in data.get("banks", []):
                bank_id = bank.get("bank_id", "")
                if bank_id.startswith("doc-test-"):
                    report.add_bank(bank_id)
    except Exception:
        pass  # Ignore errors listing banks

    if not report.created_banks:
        return

    print(f"\nCleaning up {len(report.created_banks)} test banks...")
    for bank_id in report.created_banks:
        try:
            req = urllib.request.Request(
                f"{hindsight_url}/v1/default/banks/{bank_id}",
                method="DELETE"
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"  Deleted: {bank_id}")
        except Exception as e:
            print(f"  Failed to delete {bank_id}: {e}")


def print_report(report: TestReport):
    """Print the final test report."""
    print("\n" + "=" * 70)
    print("DOCUMENTATION EXAMPLE TEST REPORT")
    print("=" * 70)
    print(f"\nTotal examples: {report.total}")
    print(f"  Passed:  {report.passed}")
    print(f"  Failed:  {report.failed}")
    print(f"  Skipped: {report.skipped}")

    if report.failed > 0:
        print("\n" + "-" * 70)
        print("FAILURES:")
        print("-" * 70)

        for result in report.results:
            if not result.success and result.error and "SKIPPED" not in result.error:
                print(f"\n{result.example.file_path}:{result.example.line_number}")
                print(f"Language: {result.example.language}")
                print(f"Code snippet:")
                print("  " + result.example.code[:200].replace("\n", "\n  ") + "...")
                print(f"Error: {result.error}")

    print("\n" + "=" * 70)

    if report.failed > 0:
        print("RESULT: FAILED")
    else:
        print("RESULT: PASSED")
    print("=" * 70)


def write_github_summary(report: TestReport, output_path: str, openai_client: OpenAI = None):
    """Write a GitHub Actions compatible markdown summary."""
    lines = []

    # Header
    status_emoji = "❌" if report.failed > 0 else "✅"
    lines.append(f"# {status_emoji} Documentation Examples Test Report")
    lines.append("")

    # Summary stats
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total | {report.total} |")
    lines.append(f"| ✅ Passed | {report.passed} |")
    lines.append(f"| ❌ Failed | {report.failed} |")
    lines.append(f"| ⏭️ Skipped | {report.skipped} |")
    lines.append("")

    # If there are failures, use LLM to summarize the raw logs
    if report.failed > 0 and openai_client:
        # Collect raw failure logs
        failure_logs = []
        for result in report.results:
            if not result.success and result.error and "SKIPPED" not in result.error:
                file_path = result.example.file_path
                if "/hindsight/" in file_path:
                    file_path = file_path.split("/hindsight/", 1)[-1]
                failure_logs.append(f"""
--- FAILURE ---
File: {file_path}
Line: {result.example.line_number}
Language: {result.example.language}
Code:
{result.example.code[:500]}
Error:
{result.error[:1000] if result.error else 'Unknown'}
""")

        # Truncate if too long (keep under ~100k tokens)
        all_logs = "\n".join(failure_logs)
        if len(all_logs) > 80000:
            all_logs = all_logs[:80000] + "\n\n... (truncated)"

        prompt = f"""Analyze these {report.failed} documentation test failures and create a markdown summary.

{all_logs}

Create a markdown summary with:
1. **Failed Tests** - List EVERY failure grouped by file, showing:
   - File path and line number
   - Brief description of what went wrong (e.g., "AttributeError: no .success attribute", "command not found: hindsight", "module not found")

2. **Categories** - At the end, group the failures into categories like:
   - CLI commands don't exist (count)
   - Wrong attribute names in Python (count)
   - TypeScript package not found (count)
   - etc.

Format each failure like:
### `filename.md`
- **Line X** (language): brief error description

Output raw markdown directly. Do NOT wrap in code blocks."""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=8000
            )
            llm_summary = response.choices[0].message.content
            lines.append(llm_summary)
        except Exception as e:
            # Fallback to simple list
            lines.append("## Failed Tests")
            lines.append("")
            lines.append(f"*LLM summary failed: {e}*")
            lines.append("")
            for result in report.results:
                if not result.success and result.error and "SKIPPED" not in result.error:
                    file_path = result.example.file_path
                    if "/hindsight/" in file_path:
                        file_path = file_path.split("/hindsight/", 1)[-1]
                    lines.append(f"- `{file_path}:{result.example.line_number}` ({result.example.language})")

    # Write to file
    with open(output_path, "w") as f:
        f.write("\n".join(lines))


def main():
    # Ensure unbuffered output
    sys.stdout.reconfigure(line_buffering=True)

    # Check for required environment variables
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        print("ERROR: OPENAI_API_KEY environment variable is required")
        sys.exit(1)

    hindsight_url = os.environ.get("HINDSIGHT_API_URL", "http://localhost:8888")

    # Find repo root (handle both script execution and exec())
    if '__file__' in globals():
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        # Fallback: use current directory or REPO_ROOT env var
        repo_root = os.environ.get("REPO_ROOT", os.getcwd())
    print(f"Repository root: {repo_root}")

    # Discover and install dependencies
    dep_results = discover_and_install_dependencies(repo_root)
    cli_available = dep_results["cli_available"]

    # Check if Hindsight is running
    try:
        import urllib.request
        urllib.request.urlopen(f"{hindsight_url}/health", timeout=5)
        print(f"Hindsight API is running at {hindsight_url}")
    except Exception as e:
        print(f"WARNING: Could not connect to Hindsight at {hindsight_url}: {e}")
        print("Some tests may fail if they require a running server")

    if not cli_available:
        print("WARNING: hindsight CLI not available - CLI examples will be skipped")

    # Initialize OpenAI client
    client = OpenAI(api_key=openai_api_key)

    # Find all markdown files
    md_files = find_markdown_files(repo_root)
    print(f"\nFound {len(md_files)} markdown files")

    # Extract all code examples
    all_examples = []
    for md_file in md_files:
        examples = extract_code_blocks(md_file)
        if examples:
            print(f"  {md_file}: {len(examples)} code blocks")
            all_examples.extend(examples)

    print(f"\nTotal code examples to test: {len(all_examples)}")

    # Test examples in parallel
    report = TestReport()
    debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))  # Default 8 parallel workers

    print(f"Running tests with {max_workers} parallel workers...")

    def run_test(args):
        idx, example = args
        safe_print(f"\n[{idx}/{len(all_examples)}] Testing example...")
        return test_example(client, example, hindsight_url, repo_root, cli_available=cli_available, debug=debug)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {executor.submit(run_test, (i, ex)): i for i, ex in enumerate(all_examples, 1)}

        # Collect results as they complete
        for future in as_completed(futures):
            result = future.result()
            report.add_result(result)

    # Clean up any test banks (runs even if tests failed)
    cleanup_test_banks(hindsight_url, report)

    # Print report
    print_report(report)

    # Write summary to file for CI (pass OpenAI client for LLM-powered summary)
    summary_path = "/tmp/doc-test-summary.md"
    write_github_summary(report, summary_path, openai_client=client)
    print(f"Summary written to {summary_path}")

    # Exit with appropriate code
    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
