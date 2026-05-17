#!/usr/bin/env python3
"""
Skills Composer — local server.
Zero external dependencies (pure Python stdlib).

Start:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
        cp .env.example .env   # set ANTHROPIC_API_KEY (server only, never in browser)
        bash serve-app.sh
        # or: .venv/bin/python server.py --port 3000
"""

import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sysconfig
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
_HTTPS_CONTEXT: ssl.SSLContext | None = None


def _https_context() -> ssl.SSLContext:
    """
    SSL context for outbound HTTPS (Anthropic, GitHub proxy).
    macOS python.org builds often need Install Certificates.command or SSL_CERT_FILE.
    """
    global _HTTPS_CONTEXT
    if _HTTPS_CONTEXT is not None:
        return _HTTPS_CONTEXT

    if os.environ.get("SKILLS_COMPOSER_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _HTTPS_CONTEXT = ctx
        return ctx

    for env_key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        cafile = os.environ.get(env_key, "").strip()
        if cafile and os.path.isfile(cafile):
            _HTTPS_CONTEXT = ssl.create_default_context(cafile=cafile)
            return _HTTPS_CONTEXT

    try:
        import certifi

        _HTTPS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        return _HTTPS_CONTEXT
    except ImportError:
        pass

    for path in (
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/opt/homebrew/etc/openssl/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
    ):
        if os.path.isfile(path):
            try:
                _HTTPS_CONTEXT = ssl.create_default_context(cafile=path)
                return _HTTPS_CONTEXT
            except ssl.SSLError:
                continue

    try:
        cacert = Path(sysconfig.get_path("platlib")) / "certifi" / "cacert.pem"
        if cacert.is_file():
            _HTTPS_CONTEXT = ssl.create_default_context(cafile=str(cacert))
            return _HTTPS_CONTEXT
    except Exception:
        pass

    _HTTPS_CONTEXT = ssl.create_default_context()
    return _HTTPS_CONTEXT


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from path; does not override existing os.environ."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


_load_dotenv(APP_DIR / ".env")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_DEFAULT_MODEL = "claude-sonnet-4-20250514"


def _normalize_anthropic_model(model: str) -> str:
    """Fix common .env typos (e.g. claude-sonnet-4@20250514 → claude-sonnet-4-20250514)."""
    m = model.strip()
    m = re.sub(r"@", "-", m)
    m = re.sub(r"-{2,}", "-", m)
    return m


_RAW_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
ANTHROPIC_MODEL = _normalize_anthropic_model(_RAW_ANTHROPIC_MODEL)
GENERATE_MAX_TOKENS = int(os.environ.get("GENERATE_MAX_TOKENS", "8192"))
_NAME_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")

SKILL_KIND_GENERIC = "generic"
SKILL_KIND_ARCHITECTURE = "architecture"
SKILL_KIND_UPDATE = "update"


def normalize_skill_kind(kind: str, slug: str = "") -> str:
    """Map UI/API values to generic | architecture | update."""
    k = (kind or "").strip().lower()
    if k in ("architecture", "product", "repo", "product-architecture"):
        return SKILL_KIND_ARCHITECTURE
    if k in ("update", "existing", "load"):
        return SKILL_KIND_UPDATE
    if k == SKILL_KIND_GENERIC:
        return SKILL_KIND_GENERIC
    if slug:
        s = slug.lower()
        if "architecture" in s or s.endswith("-playbook"):
            return SKILL_KIND_ARCHITECTURE
    return SKILL_KIND_GENERIC


# ── Parsing helpers ────────────────────────────────────────────────────────────

def slugify_skill_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _split_frontmatter(raw: str) -> tuple[str | None, str]:
    text = raw.lstrip("\ufeff").strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end == -1:
        return None, text
    fm = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    return fm, body


def _yaml_scalar(lines: list[str], key: str) -> str | None:
    prefix = f"{key}:"
    for line in lines:
        s = line.strip()
        if not s.startswith(prefix):
            continue
        val = s[len(prefix) :].strip()
        if not val:
            return ""
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            return val[1:-1]
        return val
    return None


def _description_value(fm: str) -> str | None:
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("description:"):
            continue
        rest = s[len("description:") :].strip()
        if rest in (">", ">-", "|", "|-", ""):
            folded: list[str] = []
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if nxt.startswith("  "):
                    folded.append(nxt[2:].strip())
                elif nxt.strip() == "":
                    folded.append("")
                else:
                    break
            joined = " ".join(p for p in folded if p).strip()
            return joined or None
        return rest.strip() or None
    return None


def parse_skill_markdown(text: str) -> tuple[str, str, str, str]:
    """Return (name, description, body, frontmatter)."""
    fm, body = _split_frontmatter(text.strip())
    if fm is None:
        return "", "", text.strip(), ""
    fm_lines = fm.splitlines()
    name = (_yaml_scalar(fm_lines, "name") or "").strip()
    desc = _description_value(fm) or ""
    return name, desc, body, fm


def strip_outer_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()



IDE_CHAT_MAX_TOKENS = int(os.environ.get("IDE_CHAT_MAX_TOKENS", "4096"))


def ide_chat_system_prompt(skill_md: str, skill_slug: str) -> str:
    """System prompt for Review-panel AI assistant."""
    parsed_name, _, _, _ = parse_skill_markdown(skill_md) if skill_md.strip() else ("", "", "", "")
    slug = (skill_slug or "").strip() or slugify_skill_name(parsed_name) or "unknown"
    issues_block = ""
    if skill_md.strip():
        result = validate_skill_markdown(skill_md, expected_slug=slug)
        lines: list[str] = []
        for msg in result.errors:
            lines.append(f"- ERROR: {msg}")
        for msg in result.warnings:
            lines.append(f"- WARNING: {msg}")
        if lines:
            issues_block = "Current agentskills.io validation:\n" + "\n".join(lines) + "\n\n"
    return f"""You are the Skill Composer assistant helping edit a Dataverse Agent Skill (SKILL.md).

Specification: https://agentskills.io/specification

Skill slug (folder name): {slug}

{issues_block}Current SKILL.md:

```markdown
{skill_md}
```

Guidelines:
- Answer concisely in plain language.
- When the user asks about errors or validation, explain each issue and how to fix it in the YAML or body.
- Only output a full replacement SKILL.md when the user asks to fix, rewrite, or update the file.
- When you provide a full replacement, put the entire file in one fenced code block labeled markdown (opening ```markdown).
- Keep conversational explanation outside the code fence.
"""


_SKILL_MD_FENCE = re.compile(
    r"```(?:markdown|md)?\s*\n(---\s*\n[\s\S]*?---\s*\n[\s\S]*?)\s*```",
    re.IGNORECASE,
)


def parse_chat_response(text: str) -> tuple[str, list[dict]]:
    """Split assistant reply into chat text and optional SKILL.md file edit."""
    m = _SKILL_MD_FENCE.search(text)
    if not m:
        return text.strip(), []
    content = m.group(1).strip()
    if not content.startswith("---"):
        return text.strip(), []
    display = (text[: m.start()] + text[m.end() :]).strip()
    if not display:
        display = "Updated SKILL.md — review the editor and run Validate."
    return display, [{"file": "SKILL.md", "content": content}]


def anthropic_messages(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
) -> str:
    """Call Anthropic Messages API; return assistant text."""
    data = json.dumps(
        {
            "model": ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120, context=_https_context()) as r:
        result = json.loads(r.read())
    return "".join(b["text"] for b in result.get("content", []) if b["type"] == "text")


# ── Hub-aligned generation prompt ─────────────────────────────────────────────

def _author_notes_block(
    *,
    skill_name: str,
    description: str,
    when_to_use: str,
    examples: str,
    steps: str,
    gotchas: str,
    output_format: str,
    license_: str,
) -> str:
    extra: list[str] = []
    if examples.strip():
        extra.append(
            "Author-provided example prompts (incorporate into description and When to use):\n"
            + examples.strip()
        )
    if steps.strip():
        extra.append(
            "Author-provided procedure hints (turn into numbered operating loop):\n" + steps.strip()
        )
    if gotchas.strip():
        extra.append("Author-provided gotchas / pitfalls:\n" + gotchas.strip())
    if output_format.strip():
        extra.append("Author-provided output format requirements:\n" + output_format.strip())
    if license_.strip():
        extra.append(f"License (include in frontmatter if appropriate): {license_.strip()}")
    optional = "\n\n".join(extra) if extra else "(none)"
    return f"""**Skill display name (from form):** {skill_name}

**What / capability:** {description}

**When to use (triggers):** {when_to_use}

**Optional notes from author:**
{optional}
"""


def skill_generation_prompt(
    *,
    skill_name: str,
    slug: str,
    description: str,
    when_to_use: str,
    examples: str,
    steps: str,
    gotchas: str,
    output_format: str,
    compatibility: str,
    license_: str,
    skill_kind: str = SKILL_KIND_GENERIC,
    existing_skill_md: str = "",
) -> str:
    kind = normalize_skill_kind(skill_kind, slug)
    author = _author_notes_block(
        skill_name=skill_name,
        description=description,
        when_to_use=when_to_use,
        examples=examples,
        steps=steps,
        gotchas=gotchas,
        output_format=output_format,
        license_=license_,
    )
    if kind == SKILL_KIND_UPDATE and existing_skill_md.strip():
        return _skill_generation_prompt_update(
            slug=slug, author_block=author, existing_skill_md=existing_skill_md.strip()
        )
    if kind == SKILL_KIND_ARCHITECTURE:
        return _skill_generation_prompt_architecture(
            slug=slug,
            author_block=author,
            compatibility=compatibility.strip(),
        )
    return _skill_generation_prompt_generic(
        slug=slug, author_block=author, compatibility=compatibility.strip()
    )


def _skill_generation_prompt_generic(*, slug: str, author_block: str, compatibility: str) -> str:
    compat_line = compatibility or "Cursor IDE and Claude Code"
    return f"""You author Agent Skills for Claude Code, Cursor, and other agents. Follow:
- https://agentskills.io/specification
- The same structural discipline as skills-hub **skill-creator** (progressive disclosure, eval handoff, narrow triggers): see https://github.com/anthropics/skills/tree/main/skills/skill-creator

Produce **one** complete Markdown file — the skill root **SKILL.md** only (no surrounding code fence).

## Target slug

The skill directory and YAML `name:` **must** be exactly this kebab-case slug:

`{slug}`

## YAML frontmatter (required)

Start the file with YAML between `---` lines. Include at minimum:

- `name:` — **must equal the target slug** (`{slug}`).
- `description:` — prefer a **single line** combining what the skill does, **Use when …**, and **Do NOT use for …** (under 1024 chars). Use `description: >-` folded block only if the text would exceed ~200 characters on one line.
- `compatibility:` — one line (e.g. "{compat_line}").
- `metadata:` block with at least:
  - `agentskills-spec: "https://agentskills.io/specification"`
  - `version: "0.1.0"`

## Body structure (after frontmatter)

Use Markdown headings in this order (adapt titles slightly if needed but keep coverage):

1. Title line (`# …`) — human-readable skill title (can differ from slug).
2. `## When to use` — narrow bullets; mirror triggers below; mention example user phrasing.
3. `## Procedure` or `## Execution (Claude / Cursor)` — **numbered operating loop** the agent must follow (execute, not only advise). This is the **primary** workflow.
4. `## Defaults` — conventions, labels, env vars, or API defaults.
5. `## Anti-patterns` — what not to do (security, guessing, scope creep).
6. `## Evaluation suite` — point to `evals/README.md` and `evals/evals.json` (paths relative to the skill folder). Mention `bash cli/run-evals.sh {slug}` in skills-hub.
7. `## Additional detail` — point to `references/` (one or more `.md` files). Use `references/REFERENCE.md` only if a single catch-all doc fits; prefer topic files (e.g. `references/guide.md`) when appropriate.

## Rules

- Do **not** wrap the entire file in markdown code fences.
- Do **not** invent project-specific URLs, tokens, or ticket IDs.
- Prefer **one recommended path** over equal-weight menus.
- **Numbered Procedure is the main execution path.** Checklists are allowed only as secondary verification under Procedure or their own heading — not as a substitute for numbered steps.
- Do **not** use checkbox-style task lists (`- [ ]`) as the primary workflow.

## Author intent (required)

{author_block}
"""


def _skill_generation_prompt_architecture(*, slug: str, author_block: str, compatibility: str) -> str:
    compat_line = compatibility or "Name the real stack (frameworks, repos, env) — not only 'Cursor IDE'."
    return f"""You author **product / repository architecture** Agent Skills (dense, path-specific). Follow:
- https://agentskills.io/specification
- skills-hub discipline: progressive disclosure via **multiple** `references/<topic>.md` files — **do not** default to a lone `references/REFERENCE.md` unless the author only needs one doc.

Produce **one** complete **SKILL.md** (no code fence).

## Target slug

`name:` and directory **must** be exactly: `{slug}`

## YAML frontmatter

- `name:` = `{slug}`
- `description:` — rich triggers: what the product/repo is, **Use when …**, **Do NOT use for …** (under 1024 chars; `>-` only if needed)
- `compatibility:` — **stack-specific** (e.g. "{compat_line}")
- `metadata:` — include `agentskills-spec` and `version`, **and** when known `project:` / product fields from author intent

## Body (architecture / product skill)

1. `#` Title + **what this product/repo is** (2–4 sentences)
2. `## When to use` — narrow bullets + example user phrasing
3. `## Mental model` — ASCII diagram or bullet flow (UI → API → data → external systems)
4. `## Default workflow` — **numbered** operating loop (repo-specific steps, real folders)
5. `## Where to look` — **table** of paths/files (routes, services, config) agents should open first
6. `## Stack and conventions` — short factual table (libraries, patterns, auth, data ownership)
7. `## Anti-patterns` / gotchas — Pathfinder vs pooled JWT, caching layers, CR vs Git, etc. as relevant
8. `## Reference index` — **list actual** `references/<name>.md` files (only names the author implied; do not invent `REFERENCE.md` if multiple topics apply)
9. `## Evaluation suite` — `evals/README.md`, `evals/evals.json`, `bash cli/run-evals.sh {slug}`
10. `## When this skill is not enough` — point to deeper docs if author mentioned them

## Rules

- **Repo-specific facts only** from author intent — no generic filler that could apply to any app.
- Do **not** replace a mature skill with a thin checklist; match hub depth (~100+ lines when the topic warrants it).
- Numbered workflow is primary; checklists are secondary only.

## Author intent (required)

{author_block}
"""


def _skill_generation_prompt_update(*, slug: str, author_block: str, existing_skill_md: str) -> str:
    return f"""You **improve** an existing skills-hub **SKILL.md** — do **not** replace it with a generic template.

## Target slug

`name:` must remain: `{slug}`

## Task

1. Read the **existing SKILL.md** below.
2. **Preserve** repo-specific content: mental model, path tables, reference index, OpenShift/Snowflake sections, product metadata (`project:`, etc.).
3. **Improve** only where helpful:
   - Add or sharpen **Do NOT use for …** in `description:` (keep hub trigger richness)
   - Fix broken reference links; align reference index to **actual** `references/*.md` files (never force a fake `REFERENCE.md`)
   - Tighten triggers; fix clarity; keep line count under 500
4. Output the **full updated file** only (no code fence, no commentary).

## Do NOT

- Collapse sections into a short generic Procedure + checklist
- Remove path tables, mental model, or stack-specific compatibility
- Invent URLs, ticket IDs, or files not in the original or author notes

## Author intent (optional edits)

{author_block}

## Existing SKILL.md (canonical base — extend, do not discard)

{existing_skill_md}
"""


# ── Validation (hub + agentskills.io) ─────────────────────────────────────────

@dataclass
class SkillValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_skill_markdown(
    skill_markdown: str,
    *,
    expected_slug: str = "",
    skill_kind: str = SKILL_KIND_GENERIC,
) -> SkillValidationResult:
    kind = normalize_skill_kind(skill_kind, expected_slug)
    errors: list[str] = []
    warnings: list[str] = []

    text = skill_markdown.strip()
    if not text:
        errors.append("SKILL.md is empty.")
        return SkillValidationResult(ok=False, errors=errors, warnings=warnings)

    fm, body = _split_frontmatter(text)
    if fm is None:
        errors.append(
            "Missing YAML frontmatter: file must start with --- and include a closing ---."
        )
        return SkillValidationResult(ok=False, errors=errors, warnings=warnings)

    fm_lines = fm.splitlines()
    name = _yaml_scalar(fm_lines, "name")
    desc = _description_value(fm)

    if name is None or name.strip() == "":
        errors.append("Frontmatter must include non-empty name: (kebab-case, matches directory slug).")
    else:
        name = name.strip()
        if not _NAME_PATTERN.match(name):
            errors.append(
                f"name: '{name}' must match ^[a-z0-9-]{{1,64}}$ "
                "(lowercase letters, digits, hyphens only)."
            )
        if name.startswith("-") or name.endswith("-"):
            errors.append(f"name: must not start or end with a hyphen (got '{name}').")
        if "--" in name:
            errors.append(f"name: must not contain consecutive hyphens (got '{name}').")
        if expected_slug and name != expected_slug:
            errors.append(
                f"name: in YAML is '{name}' but expected directory slug '{expected_slug}' "
                "(align name: in SKILL.md with the skill folder slug)."
            )

    if desc is None or not str(desc).strip():
        errors.append("Frontmatter must include description: (1–1024 chars).")
    else:
        d = str(desc).strip()
        if len(d) > 1024:
            errors.append(f"description: length {len(d)} exceeds 1024 characters.")
        low = d.lower()
        if "use when" not in low and " when " not in low and not low.startswith("when "):
            warnings.append(
                "description: should state when to use the skill (e.g. 'Use when …')."
            )
        if "do not" not in low and "not use" not in low and "don't use" not in low:
            warnings.append(
                "Consider adding 'Do NOT use for …' in description to narrow triggers."
            )

    if _yaml_scalar(fm_lines, "compatibility") is None:
        warnings.append(
            "Optional: add compatibility: (e.g. Cursor IDE and Claude Code) under frontmatter."
        )

    if "metadata:" not in fm:
        warnings.append(
            "Optional: add metadata: block (e.g. agentskills-spec URL, version) under frontmatter."
        )
    elif kind == SKILL_KIND_ARCHITECTURE and "project:" not in fm.lower():
        warnings.append(
            "Architecture/product skills often include metadata.project (or internal-product-name) in frontmatter."
        )

    if not re.search(r"(?m)^\s{0,3}#+\s*when\s+to\s+use\b", body, re.IGNORECASE):
        warnings.append("Body should include a 'When to use' section (## When to use …).")

    if not re.search(r"(?mi)(anti[- ]?pattern|do\s+not\s+use|when\s+not)", body):
        warnings.append(
            "Consider an 'Anti-patterns' or 'When not to use' section to reduce false triggers."
        )

    if not re.search(r"(?mi)evaluat|evals/evals\.json|run-evals", body):
        warnings.append(
            "Body should mention an evaluation suite (evals/README.md, evals/evals.json, run-evals.sh)."
        )

    ref_files = re.findall(r"references/([a-zA-Z0-9][a-zA-Z0-9._-]*\.md)", body, re.IGNORECASE)
    has_refs = bool(ref_files) or bool(re.search(r"(?mi)references/", body))
    if not has_refs:
        warnings.append(
            "Body should mention progressive disclosure under references/ (one or more .md files)."
        )
    elif kind == SKILL_KIND_ARCHITECTURE:
        if len(ref_files) == 1 and ref_files[0].lower() == "reference.md":
            warnings.append(
                "Architecture skills usually use references/<topic>.md (multiple files), "
                "not only references/REFERENCE.md."
            )
        if not re.search(r"(?mi)mental\s+model|where\s+to\s+look", body):
            warnings.append(
                "Architecture/product skills should include Mental model and/or Where to look (path table)."
            )

    line_count = len(text.splitlines())
    if line_count > 500:
        warnings.append(
            f"SKILL.md is {line_count} lines; hub guidance recommends < 500 "
            "(move detail to references/)."
        )

    for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", body):
        path = m.group(1).strip()
        if path.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if ".." in path.split("/"):
            warnings.append(f"Relative link contains '..': {path} (check path safety).")

    ok = len(errors) == 0
    return SkillValidationResult(ok=ok, errors=errors, warnings=warnings)


def _validation_to_issues(result: SkillValidationResult) -> list[dict]:
    issues: list[dict] = []
    for msg in result.errors:
        field = "skill"
        if msg.lower().startswith("name"):
            field = "name"
        elif msg.lower().startswith("description"):
            field = "description"
        elif "frontmatter" in msg.lower():
            field = "frontmatter"
        issues.append({"field": field, "level": "error", "message": msg})
    for msg in result.warnings:
        field = "body"
        if msg.lower().startswith("description"):
            field = "description"
        elif "metadata" in msg.lower() or "compatibility" in msg.lower():
            field = "frontmatter"
        issues.append({"field": field, "level": "warning", "message": msg})
    return issues


def _legacy_field_issues(
    name: str,
    description: str,
    body: str,
    *,
    expected_slug: str = "",
    compatibility: str = "",
) -> list[dict]:
    """agentskills.io field checks when validating form fields without full markdown."""
    issues: list[dict] = []

    def err(field, msg):
        issues.append({"field": field, "level": "error", "message": msg})

    def warn(field, msg):
        issues.append({"field": field, "level": "warning", "message": msg})

    check_name = expected_slug or name
    if not check_name:
        err("name", "name is required")
    elif len(check_name) > 64:
        err("name", f"name must be ≤ 64 characters (currently {len(check_name)})")
    elif not _NAME_PATTERN.match(check_name):
        err(
            "name",
            "name must be lowercase letters, numbers, and hyphens only; "
            "cannot start or end with a hyphen",
        )
    elif expected_slug and name and name != expected_slug:
        err(
            "name",
            f"name '{name}' does not match expected slug '{expected_slug}'",
        )

    if not description:
        err("description", "description is required")
    elif len(description) > 1024:
        err("description", f"description must be ≤ 1024 characters (currently {len(description)})")
    elif len(description) < 20:
        warn(
            "description",
            "description is very short — should describe what the skill does and when to use it",
        )

    if compatibility and len(compatibility) > 500:
        err("compatibility", f"compatibility must be ≤ 500 characters (currently {len(compatibility)})")

    if body:
        if len(body.splitlines()) > 500:
            warn(
                "body",
                f"SKILL.md body is {len(body.splitlines())} lines — "
                "spec recommends under 500. Move detail to references/",
            )
        tokens_est = len(body.split())
        if tokens_est > 5000:
            warn(
                "body",
                f"SKILL.md body is ~{tokens_est} tokens — "
                "spec recommends under 5000. Move detail to references/",
            )

    return issues


def validate_skill(
    name: str = "",
    description: str = "",
    body: str = "",
    compatibility: str = "",
    license_: str = "",
    *,
    skill_markdown: str = "",
    expected_slug: str = "",
    frontmatter: str = "",
    skill_kind: str = SKILL_KIND_GENERIC,
) -> list[dict]:
    """
    Returns list of {field, level, message}.
    Prefer skill_markdown + expected_slug for full-file validation after edit.
    """
    issues: list[dict] = []

    kind = normalize_skill_kind(skill_kind, expected_slug or name)

    if skill_markdown.strip():
        result = validate_skill_markdown(
            skill_markdown, expected_slug=expected_slug, skill_kind=kind
        )
        issues.extend(_validation_to_issues(result))
        if not name and not description and not body:
            parsed_name, parsed_desc, parsed_body, _ = parse_skill_markdown(skill_markdown)
            name, description, body = parsed_name, parsed_desc, parsed_body
    elif frontmatter.strip() and (name or description):
        synthetic = f"---\n{frontmatter.strip()}\n---\n{body}"
        result = validate_skill_markdown(synthetic, expected_slug=expected_slug, skill_kind=kind)
        issues.extend(_validation_to_issues(result))

    issues.extend(
        _legacy_field_issues(
            name,
            description,
            body,
            expected_slug=expected_slug,
            compatibility=compatibility,
        )
    )

    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for item in issues:
        key = (item["field"], item["level"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


# ── HTTP handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "200" not in msg or self.command == "POST":
            print(f"  {self.command} {self.path}  {msg.split('\"')[-1].strip()}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._file(APP_DIR / "index.html", "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._config()
        elif self.path == "/api/skill-index":
            self._skill_index()
        else:
            self._json(404, {"error": "Not found"})

    def _skill_index(self):
        """Serve SKILL_INDEX.json from app/data/."""
        index_path = APP_DIR / "data" / "SKILL_INDEX.json"
        if not index_path.exists():
            self._json(404, {"error":
                "SKILL_INDEX.json not found. Upload it via Setup or drop it into app/data/."})
            return
        try:
            import json as _json
            data = _json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                payload = {"version": "1.0", "skills": data}
            else:
                payload = data
            body = _json.dumps(payload).encode()
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers(); self.wfile.write(body)
        except Exception as e:
            self._json(500, {"error": f"Could not read SKILL_INDEX.json: {e}"})

    def _upload_skill_index(self, body: dict):
        """Save uploaded SKILL_INDEX.json content to app/data/."""
        content = body.get("content", "")
        if not content:
            self._json(400, {"error": "No content provided"}); return
        try:
            import json as _json
            data   = _json.loads(content)
            skills = data if isinstance(data, list) else data.get("skills", [])
            if not skills:
                self._json(400, {"error": "No skills array found in JSON"}); return
            data_dir = APP_DIR / "data"
            data_dir.mkdir(exist_ok=True)
            (data_dir / "SKILL_INDEX.json").write_text(content, encoding="utf-8")
            self._json(200, {"ok": True, "count": len(skills)})
        except _json.JSONDecodeError as e:
            self._json(400, {"error": f"Invalid JSON: {e}"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _config(self):
        """Public config for the UI — never exposes secrets."""
        self._json(
            200,
            {
                "anthropicKeyConfigured": bool(ANTHROPIC_KEY),
                "model": ANTHROPIC_MODEL,
                "maxTokens": GENERATE_MAX_TOKENS,
            },
        )

    def do_POST(self):
        body = self._body()
        routes = {
            "/api/validate":           self._validate,
            "/api/generate":           self._generate,
            "/api/github":             self._github,
            "/api/upload-skill-index": self._upload_skill_index,
            "/api/run-evals":          self._run_evals,
        }
        fn = routes.get(self.path)
        if fn:
            fn(body)
        else:
            self._json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _validate(self, body: dict):
        slug = (body.get("slug") or "").strip() or slugify_skill_name(body.get("name", ""))
        skill_md = (body.get("skillMarkdown") or "").strip()
        skill_kind = body.get("skillKind", "")

        if skill_md:
            issues = validate_skill(
                skill_markdown=skill_md,
                expected_slug=slug,
                skill_kind=skill_kind,
            )
        else:
            issues = validate_skill(
                name=body.get("name", ""),
                description=body.get("description", ""),
                body=body.get("body", ""),
                compatibility=body.get("compatibility", ""),
                license_=body.get("license", ""),
                expected_slug=slug,
                skill_kind=skill_kind,
            )
        self._json(
            200,
            {"issues": issues, "valid": not any(i["level"] == "error" for i in issues)},
        )

    def _generate(self, body: dict):
        # ── Test mode: SKILL.md as system prompt, user prompt as message ──────
        if body.get("__test"):
            if not ANTHROPIC_KEY:
                self._json(400, {"error": "No Anthropic API key on the server. Set ANTHROPIC_API_KEY in .env."}); return
            skill_context = body.get("skillContext", "")
            user_prompt   = body.get("userPrompt", "")
            if not user_prompt.strip():
                self._json(400, {"error": "userPrompt is required"}); return
            try:
                data = json.dumps({
                    "model":      ANTHROPIC_MODEL,
                    "max_tokens": 1024,
                    "system":     skill_context,
                    "messages":   [{"role": "user", "content": user_prompt}],
                }).encode()
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages", data=data,
                    headers={"Content-Type": "application/json",
                             "x-api-key": ANTHROPIC_KEY,
                             "anthropic-version": "2023-06-01"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60, context=_https_context()) as r:
                    result = json.loads(r.read())
                text = "".join(b["text"] for b in result.get("content", []) if b["type"] == "text")
                self._json(200, {"response": text})
            except urllib.error.HTTPError as e:
                try: err = json.loads(e.read()).get("error", {}).get("message", str(e))
                except Exception: err = str(e)
                self._json(e.code, {"error": err})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        # ── End test mode ──────────────────────────────────────────────────────

        # ── Review IDE chat (multi-turn, SKILL.md in system) ───────────────────
        if body.get("__chat"):
            if not ANTHROPIC_KEY:
                self._json(
                    400,
                    {
                        "error": "No Anthropic API key on the server. "
                        "Set ANTHROPIC_API_KEY in .env and restart python3 server.py."
                    },
                )
                return
            raw_messages = body.get("messages")
            if not isinstance(raw_messages, list) or not raw_messages:
                self._json(400, {"error": "messages is required"}); return
            file_contexts = body.get("fileContexts") if isinstance(body.get("fileContexts"), dict) else {}
            skill_md = (file_contexts.get("SKILL.md") or "").strip()
            skill_slug = (body.get("skillSlug") or "").strip()
            api_messages: list[dict] = []
            for m in raw_messages[-20:]:
                if not isinstance(m, dict):
                    continue
                role = m.get("role") or "user"
                if role not in ("user", "assistant"):
                    role = "user"
                content = (m.get("content") or "").strip()
                if content:
                    api_messages.append({"role": role, "content": content})
            if not api_messages:
                self._json(400, {"error": "No valid messages"}); return
            try:
                text = anthropic_messages(
                    system=ide_chat_system_prompt(skill_md, skill_slug),
                    messages=api_messages,
                    max_tokens=min(IDE_CHAT_MAX_TOKENS, GENERATE_MAX_TOKENS),
                )
                reply, file_edits = parse_chat_response(text)
                self._json(200, {"response": reply, "fileEdits": file_edits})
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read()).get("error", {}).get("message", str(e))
                except Exception:
                    err = str(e)
                self._json(e.code, {"error": err})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        # ── Review IDE inline selection edit ───────────────────────────────────
        if body.get("__inline_edit"):
            if not ANTHROPIC_KEY:
                self._json(400, {"error": "No Anthropic API key on the server. Set ANTHROPIC_API_KEY in .env."}); return
            instruction = (body.get("instruction") or "").strip()
            selected = (body.get("selectedText") or "").strip()
            context = (body.get("surroundingContext") or "").strip()
            if not instruction or not selected:
                self._json(400, {"error": "instruction and selectedText are required"}); return
            prompt = (
                f"Rewrite ONLY the selected excerpt. Return replacement text only — no markdown fences, "
                f"no commentary.\n\nInstruction: {instruction}\n\n"
                f"SKILL.md context:\n{context}\n\nSelected text:\n{selected}"
            )
            try:
                replacement = anthropic_messages(
                    system="You edit fragments of Agent Skill SKILL.md files per agentskills.io.",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2048,
                )
                self._json(200, {"replacement": replacement.strip()})
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read()).get("error", {}).get("message", str(e))
                except Exception:
                    err = str(e)
                self._json(e.code, {"error": err})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return


        if body.get("apiKey"):
            self._json(
                400,
                {
                    "error": "API keys must not be sent from the browser. "
                    "Set ANTHROPIC_API_KEY in .env and restart the server."
                },
            )
            return
        if not ANTHROPIC_KEY:
            self._json(
                400,
                {
                    "error": "No Anthropic API key on the server. "
                    "Set ANTHROPIC_API_KEY in .env and restart python3 server.py."
                },
            )
            return

        name = body.get("name", "").strip()
        description = body.get("description", "").strip()
        when = body.get("when", "").strip()
        examples = body.get("examples", "").strip()
        steps = body.get("steps", "").strip()
        gotchas = body.get("gotchas", "").strip()
        output_fmt = body.get("outputFormat", "").strip()
        compat = body.get("compatibility", "").strip()
        license_ = body.get("license", "").strip()
        skill_kind = body.get("skillKind", "")
        existing_skill_md = (body.get("existingSkillMarkdown") or "").strip()

        slug = slugify_skill_name(name)
        if not slug:
            self._json(400, {"error": "Skill name is required to compute slug."})
            return

        prompt = skill_generation_prompt(
            skill_name=name,
            slug=slug,
            description=description,
            when_to_use=when,
            examples=examples,
            steps=steps,
            gotchas=gotchas,
            output_format=output_fmt,
            compatibility=compat,
            license_=license_,
            skill_kind=skill_kind,
            existing_skill_md=existing_skill_md,
        )

        try:
            data = json.dumps(
                {
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": GENERATE_MAX_TOKENS,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120, context=_https_context()) as r:
                result = json.loads(r.read())
            text = strip_outer_fence(
                "".join(b["text"] for b in result.get("content", []) if b["type"] == "text")
            )

            gen_name, gen_desc, gen_body, gen_fm = parse_skill_markdown(text)
            issues = validate_skill(
                name=gen_name,
                description=gen_desc,
                body=gen_body,
                skill_markdown=text,
                expected_slug=slug,
                frontmatter=gen_fm,
                compatibility=compat,
                skill_kind=skill_kind or normalize_skill_kind("", slug),
            )
            self._json(200, {"skill": text, "slug": slug, "issues": issues})

        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", {}).get("message", str(e))
            except Exception:
                err = str(e)
            self._json(e.code, {"error": err})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _run_evals(self, body: dict):
        """Run skills-hub cli/run-evals.sh against a local clone path."""
        hub_root = (body.get("hubRoot") or "").strip()
        skill_slug = (body.get("skillSlug") or "").strip()
        if not hub_root:
            self._json(400, {"error": "hubRoot is required (absolute path to skills-hub clone)"})
            return
        if not skill_slug:
            self._json(400, {"error": "skillSlug is required"})
            return
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", skill_slug):
            self._json(400, {"error": "Invalid skillSlug"})
            return

        try:
            root = Path(hub_root).expanduser().resolve()
        except Exception as e:
            self._json(400, {"error": f"Invalid hubRoot: {e}"})
            return

        home = Path.home().resolve()
        if root != home and home not in root.parents:
            self._json(400, {"error": "hubRoot must be under your home directory"})
            return

        eval_script = root / "cli" / "run-evals.sh"
        if not eval_script.is_file():
            self._json(
                400,
                {"error": f"Not found: {eval_script} — point hubRoot at your skills-hub clone"},
            )
            return

        try:
            proc = subprocess.run(
                ["bash", str(eval_script), skill_slug],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ},
            )
            self._json(
                200,
                {
                    "exitCode": proc.returncode,
                    "stdout": proc.stdout or "",
                    "stderr": proc.stderr or "",
                    "ok": proc.returncode == 0,
                },
            )
        except subprocess.TimeoutExpired:
            self._json(408, {"error": "Eval run timed out after 120s"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _github(self, body: dict):
        token = body.get("token", "")
        method = body.get("method", "GET").upper()
        url = body.get("url", "")
        payload = body.get("body")

        if not token:
            self._json(400, {"error": "GitHub token required"})
            return
        if not url.startswith("https://api.github.com/"):
            self._json(400, {"error": "Only github.com API calls are proxied"})
            return

        try:
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"token {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "skills-composer/1.0",
                },
                method=method,
            )
            with urllib.request.urlopen(req, timeout=20, context=_https_context()) as r:
                result = json.loads(r.read() or b"{}")
            self._json(200, result)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
            except Exception:
                err = {"message": str(e)}
            self._json(e.code, err)
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) if n else b"{}")
        except Exception:
            return {}

    def _file(self, path: Path, ctype: str):
        if not path.exists():
            self._json(404, {"error": "Not found"})
            return
        content = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def free_port(preferred: int) -> int:
    s = socket.socket()
    try:
        s.bind(("", preferred))
        return preferred
    except OSError:
        s.bind(("", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Skills Composer — local server")
    ap.add_argument("--port", type=int, default=3747)
    ap.add_argument("--no-browser", dest="no_browser", action="store_true")
    args = ap.parse_args()

    port = free_port(args.port)
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"

    print(f"\n  Skills Composer  →  {url}")
    print(f"  Anthropic key    →  {'env ✓' if ANTHROPIC_KEY else 'missing — set in .env'}")
    print(f"  Model            →  {ANTHROPIC_MODEL} (max_tokens={GENERATE_MAX_TOKENS})")
    if _RAW_ANTHROPIC_MODEL.strip() != ANTHROPIC_MODEL:
        print(f"  Model (raw .env) →  {_RAW_ANTHROPIC_MODEL.strip()}  ← corrected @/hyphens")
    if os.environ.get("SKILLS_COMPOSER_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
        print("  SSL              →  verification disabled (SKILLS_COMPOSER_INSECURE_SSL)")
    else:
        print("  SSL              →  system / certifi CA bundle (set SSL_CERT_FILE if verify fails)")
    print("  Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
