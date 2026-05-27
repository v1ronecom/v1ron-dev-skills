# Skill Development Guide

Welcome to your v1ron skills workspace. Each skill in this folder is a self-contained
AI capability that runs inside your MCP container and is invokable by the v1ron AI.

---

## Folder Structure

```
skills/
├── skill-guide.md          ← this file
├── docx/                   ← demo skill (Word document generator)
│   ├── SKILL.md            ← skill definition (frontmatter + docs)
│   └── scripts/
│       └── create.py       ← entry point
└── your-skill/
    ├── SKILL.md
    └── scripts/
        └── create.py
```

---

## Anatomy of a Skill

### `SKILL.md` — The Definition File

Every skill needs a `SKILL.md` with YAML frontmatter followed by documentation:

```markdown
---
name: my-skill
description: >
  One paragraph describing WHEN the AI should use this skill.
  Be specific about trigger phrases and use cases.
category: productivity
version: "1.0.0"
inputs:
  topic:
    type: string
    required: true
    description: "What the skill should act on"
  format:
    type: string
    required: false
    description: "Output format (default: standard)"
run: python scripts/create.py
output: file        # or: text | json | image
---

# Human-readable documentation

Explain what the skill does, how it works, and any important notes.
```

**Key frontmatter fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique slug (lowercase, hyphens) |
| `description` | yes | When the AI invokes this skill — be explicit about triggers |
| `category` | yes | `productivity`, `data`, `media`, `communication`, `code` |
| `version` | yes | Semver string |
| `inputs` | yes | Dict of named inputs with type/required/description |
| `run` | yes | Command to execute (always `python scripts/create.py`) |
| `output` | yes | `file`, `text`, `json`, or `image` |
| `license` | no | License identifier, e.g. `Proprietary. LICENSE.txt has complete terms` |

> **Important:** All required fields must be present. A missing field causes the MCP to return HTTP 400 when the skill is installed. See [Troubleshooting](#troubleshooting) below.

---

### `scripts/create.py` — The Entry Point

The script receives inputs as JSON on **stdin** and must write NDJSON to **stdout**:

```python
#!/usr/bin/env python3
import json, sys, os

# Read inputs from stdin
inputs = json.loads(sys.stdin.read())
topic  = inputs.get("topic", "")

# Emit thinking lines (optional — shown to user as progress)
print(json.dumps({"thinking": "Processing..."}), flush=True)

# Do your work here
result = f"Processed: {topic}"

# Emit the final result as the last line
if output_type == "text":
    print(json.dumps({"text": result}))

elif output_type == "file":
    import base64
    print(json.dumps({
        "filename":       "output.txt",
        "content_base64": base64.b64encode(result.encode()).decode(),
        "mime_type":      "text/plain",
    }))
```

---

## Environment Variables

Inside `create.py` these environment variables are always available:

| Variable | Value | Use |
|----------|-------|-----|
| `APP_INTERNAL_URL` | `http://app:8000` | Call your v1ron app API |
| `MCP_API_KEY` | auto-generated | Auth header for internal API calls |
| `DATABASE_URL` | postgres connection string | Direct DB access (asyncpg) |
| `REDIS_URL` | redis connection string | Cache / pub-sub |

---

## Calling the Internal LLM

Your skill can call the v1ron app's internal LLM endpoint for AI-generated content:

```python
import json, os, urllib.request

APP_URL = os.environ.get("APP_INTERNAL_URL", "http://app:8000")
MCP_KEY = os.environ.get("MCP_API_KEY", "")

def call_llm(prompt: str) -> str:
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": prompt},
    ]
    req = urllib.request.Request(
        f"{APP_URL}/api/v1/internal/llm/stream",
        data=json.dumps({"messages": messages}).encode(),
        headers={"Content-Type": "application/json", "X-MCP-Key": MCP_KEY},
        method="POST",
    )
    result = ""
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    if "delta" in obj:
                        result += obj["delta"]
                except Exception:
                    pass
    return result
```

---

## Output Types

### `output: text`
```python
print(json.dumps({"text": "Your plain text result"}))
```

### `output: file`
```python
import base64
print(json.dumps({
    "filename":       "report.docx",
    "content_base64": base64.b64encode(file_bytes).decode(),
    "mime_type":      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # Optional: also return an HTML preview
    "html_filename":  "report.html",
    "html_base64":    base64.b64encode(html.encode()).decode(),
}))
```

### `output: image`
```python
import base64
print(json.dumps({
    "filename":       "chart.png",
    "content_base64": base64.b64encode(image_bytes).decode(),
    "mime_type":      "image/png",
}))
```

### `output: json`
```python
print(json.dumps({"data": {"key": "value", "count": 42}}))
```

---

## Thinking Lines

Emit progress updates before the final result — they appear as a live feed to the user:

```python
print(json.dumps({"thinking": "Fetching data..."}),   flush=True)
print(json.dumps({"thinking": "Building document..."}), flush=True)
print(json.dumps({"thinking": "Finalising output..."}), flush=True)
# Last line = the result
print(json.dumps({"filename": "...", "content_base64": "..."}))
```

---

## Installing Python Packages

SSH into your MCP container and install packages directly:

```bash
ssh dev@dev.v1ron.com -p 2201 -i your_key.pem
pip install requests pandas matplotlib
```

Packages persist in the container until it is rebuilt.

---

## Demo Skill: `docx`

The `docx/` skill in this folder is a fully working example. It:
- Reads `topic`, `purpose`, `audience`, `tone`, `section_count` from inputs
- Calls the internal LLM to generate structured document content
- Builds a `.docx` file using `python-docx`
- Returns both `.docx` and `.html` companion files as base64

Study `docx/scripts/create.py` as a reference implementation.

---

## Registering a Skill

Once your skill is ready, submit it to the skill store via the v1ron admin panel:
**Settings → Skills → Submit New Skill**

Your skill will appear in the marketplace after approval.

---

## Troubleshooting

### MCP returns HTTP 400 on skill install

**Cause:** A required frontmatter field is missing from `SKILL.md`.

Check that your frontmatter includes every required field:

```yaml
---
name: my-skill          # ✓
description: >          # ✓
  ...
category: productivity  # ✓  ← commonly forgotten
version: "1.0.0"        # ✓  ← commonly forgotten
inputs:                 # ✓  ← commonly forgotten
  topic:
    type: string
    required: true
    description: "..."
run: python scripts/create.py  # ✓  ← commonly forgotten
output: file                   # ✓  ← commonly forgotten
---
```

**Fix:** Add the missing fields, then re-install the skill. The MCP validates the manifest on registration and rejects it immediately if any required key is absent.

### Skill runs but returns no output

- Ensure the **last line** of stdout is the result JSON (`filename`/`content_base64`, `text`, etc.).
- `thinking` lines emitted before the final line are fine — they are shown as progress.
- Any uncaught exception that writes to stderr is swallowed; wrap your main logic in a try/except and emit `{"error": "..."}` so the failure is visible.

### Python package not found inside the container

SSH into the MCP container and install the package directly:

```bash
ssh dev@dev.v1ron.com -p 2201 -i your_key.pem
pip install <package-name>
```

Packages persist until the container is rebuilt.
