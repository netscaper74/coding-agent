# Repo Agent — repo-grounded coding assistant demo

A working demo of a coding agent that lives in the developer's IDE (Continue.dev), answers
from the organisation's **private repositories and coding standards**, respects **who is
allowed to see what**, and comes with a **monitoring console** for usage, quality, latency
and cost.

Stack: **MongoDB Atlas** (Vector Search + Atlas Search + time-series) · **Voyage AI**
(`voyage-code-3` embeddings, `rerank-2.5`) · **OpenAI** (chat model) · **Continue.dev** ·
Docker Compose.

```
 INDEX KNOWLEDGE
 GitLab / sample repos ─┐
 coding standards (MD) ─┴─► ingest ── tree-sitter chunks ── Voyage voyage-code-3 ──► Atlas: chunks
                            (incremental, per-file fingerprints)           (vector index + text index)

 ASSIST IN THE IDE
 Continue.dev ──► agent-api /v1 (OpenAI-compatible) ──► permission-filtered hybrid search on Atlas
   (VS Code,         │                                    ($vectorSearch + $search → RRF → Voyage rerank)
    JetBrains)       ├── /mcp  read-only tools (search_code, search_standards, get_file, list_repos)
                     ├── grounded prompt → OpenAI → streamed back with [S#] citations
                     └── usage event → Atlas time-series ──► rollups ──► web-ui console (:8080)
 developer reviews and applies every change (agent is read-only)
```

## What's in the box

| Service | Port | What it does |
|---|---|---|
| `ingest` | – | Reads repos (local folders or GitLab API) and standards, chunks code by function/class with tree-sitter, embeds with Voyage, writes to Atlas, creates the Atlas Search / Vector Search indexes. Re-checks every `INGEST_INTERVAL_SECONDS`, re-embedding only changed files. |
| `agent-api` | 8000 | `/v1/chat/completions` + `/v1/models` for Continue, `/mcp` (MCP Streamable HTTP), `/continue/events` (Continue dev-data), `/admin/*` (console API). |
| `web-ui` | 8080 | Monitoring console: Overview, Users & teams (drill-down), Quality (incl. evals), Performance, Cost, Knowledge & access, Audit log, Playground. Plain HTML/JS + SVG charts, no CDN — works offline / on-prem. |
| `eval` | – | `docker compose run --rm eval` — runs `eval/questions.yaml` through the real agent and scores retrieval, citations, standards and permissions. Also startable from the Quality page. |
| `seed` | – | `docker compose run --rm seed` — 14 days of synthetic activity for ~14 fictional developers so the console has something to show. `--purge` removes it. Everything is flagged `synthetic` and can be hidden in the UI. |

Sample content: two repos with different access scopes — `payments-svc` (Python, SQL,
Java; `group:finance`) and `web-portal` (TypeScript/React, Go; `group:engineering`) — and
a placeholder **ACME Secure Coding Standard** with clause IDs. The repos deliberately
contain a few standard violations (SQL interpolation, float money maths, card number in
logs, `dangerouslySetInnerHTML`) so the "review against our standard" demo has something
to find.

## Prerequisites

- Docker with Compose v2.
- A **MongoDB Atlas** cluster (M0 free tier works for the demo; it allows 3 search
  indexes and we use 2). In **Network Access**, allow the IP you run Docker from.
- A **Voyage AI** API key (from voyageai.com, or created in the Atlas UI — then set
  `VOYAGE_BASE_URL=https://ai.mongodb.com/v1`).
- An **OpenAI** API key.
- VS Code or a JetBrains IDE with the **Continue** extension.

## Quick start

```bash
cp .env.example .env          # fill in MONGODB_URI, VOYAGE_API_KEY, OPENAI_API_KEY
docker compose up -d --build
docker compose logs -f ingest # wait for "Search indexes ready" (first build: 1–3 min)
```

Then:

1. **Console:** open http://localhost:8080 and sign in with the admin token from
   `config/users.yaml` (`admin-demo-4x8p6`). Try the **Playground** first — it's the
   quickest proof that retrieval, permissions and the model are wired correctly.
2. **Demo data (optional):** `docker compose run --rm seed`
3. **Continue:** copy `continue/config.yaml` to `~/.continue/config.yaml` (or add it as a
   local config in Continue), replace `REPO_AGENT_TOKEN` with `dev-alice-7f3k2`, reload.
   Pick **Repo Agent** in the model dropdown. In Continue's settings, turn off local
   codebase indexing so all retrieval comes from Atlas.
4. **Evals:** Quality → *Run eval now*, or `docker compose run --rm eval`.

Change the tokens in `config/users.yaml` before you show this to anyone outside your team.

## Demo script (≈10 minutes)

1. **Explain across the repo** — as Alice: *"Where are journal entries validated before
   posting, and which rules are enforced?"* → answer cites `[S1 src/ledger/validation.py:…]`
   and clause `SCS-4.3`, with a Sources list.
2. **Review against the standard** — open `src/ledger/repository.py`, select
   `find_by_reference`, ask *"Does this comply with our coding standard? Fix it."* → flags
   SCS-5.1 and proposes a parameterised query as a diff; you apply it yourself.
3. **Generate tests** — select `convert()` in `src/fx/rates.py`: *"Write unit tests"* →
   follows the existing test style and SCS-6.x naming.
4. **Permissions** — switch Continue's token to Bob (`dev-bob-9q1m8`) and ask about ledger
   balances → no `payments-svc` sources; the console shows "relevant chunks hidden by
   permissions" for Bob (Knowledge & access page).
5. **Console** — Overview (adoption, acceptance, grounding, p95, spend), drill into a
   developer, Quality (eval run), Performance (time per stage), Cost (per accepted
   suggestion), Audit log.

## How it works

**Chunking.** One chunk per function / method / class / type (tree-sitter for Python,
TS/TSX/JS, Java, Go, C#; Python `ast` fallback; line windows for SQL/YAML/Markdown and
oversized blocks). Module-level code outside definitions is kept as its own chunk. Each
chunk stores repo, branch, commit, path, language, symbol, exact line range, access scope,
and split identifiers (`postLedgerEntry` → `post ledger entry`) for keyword search.
Standards are chunked per clause (`SCS-4.1`), with version and breadcrumb.

**Retrieval** (`codeagent/api/retrieval.py`). The developer's question (plus a bounded
slice of selected code) is embedded with `input_type=query`. In parallel:
`$vectorSearch` and Atlas `$search`, **both pre-filtered on `access_scope ∈ user.scopes`**,
fused with reciprocal-rank fusion, reranked by Voyage, then trimmed to top-k while keeping
1–3 standard clauses. A third small vector query counts strongly relevant chunks the user
is *not* allowed to see — only the count is logged, nothing is returned.

**Prompting** (`prompting.py`). Continue's own system message stays first; the agent adds
rules (cite with `[S# path:lines]`, never invent, read-only, follow named clauses) and the
labelled context. Chat/explain answers get a Sources footer in the IDE; edit/apply
responses don't (it would pollute diffs).

**Continue integration.** Continue treats `agent-api` as an OpenAI-compatible model
(`provider: openai`, `apiBase: http://localhost:8000/v1`). The developer's token identifies
them (ACL + monitoring); the OpenAI key never leaves the server. Requests are passed
through with tool calls intact, so Continue's agent mode works; the MCP server adds
read-only repo tools. `repo-agent-apply` is a no-retrieval pass-through for Continue's
apply role.

**Telemetry.** Every request writes one event to the `events` time-series collection
(user, team, action, repo, IDE, per-stage latency, tokens, cost, sources, citations,
no-context, ACL-hidden count). No prompt or code text is stored unless `LOG_PROMPTS=true`.
Continue's `data` block posts outcome events (accepted/rejected edits, thumbs) to
`/continue/events` with `level: noCode`. A background job rolls events into hourly
`events_rollup` documents with `$merge`; the console reads those. Events expire after
`EVENTS_TTL_DAYS` (TTL).

### Atlas collections

| Collection | Contents |
|---|---|
| `chunks` | code + standard chunks with `embedding`; indexes `chunks_vector` (vectorSearch, 1024-d cosine, filters on scope/kind/repo/language) and `chunks_text` (search) |
| `files` | per-file fingerprint, content (for `get_file`), scope |
| `ingest_runs` | one doc per ingest run |
| `events` | time-series, one doc per request (TTL) |
| `outcomes` | accept/reject/feedback (TTL) |
| `events_rollup` | hourly aggregates for the console |
| `eval_runs`, `audit` | eval results; admin actions (e.g. playground impersonation) |

## Configuration

- `.env` — keys, models, intervals, privacy flags (see `.env.example`).
- `config/sources.yaml` — repos and standards to index, each with an `access_scope`.
  For GitLab: `source: gitlab`, `project: group/name`, and `GITLAB_URL` / `GITLAB_TOKEN`
  (read_api + read_repository, dedicated read-only account).
- `config/users.yaml` — demo identities: token → user, team, groups, role. In production
  replace `codeagent/users.py:resolve_token` with a GitLab/SSO lookup so scopes come from
  real group membership.
- `config/pricing.yaml` — USD per 1M tokens for the Cost page.
- `eval/questions.yaml` — eval set.

## Things to verify before a customer demo

- **Continue config keys.** `mcpServers` with `type: streamable-http`, and the `data`
  block (`destination`, `schema`, `level`, `events`) follow Continue's current YAML
  reference, but Continue changes quickly — check against the version you install. If
  outcome events don't appear on the Quality page, check `docker compose logs agent-api`
  for `/continue/events` requests.
- **Prices** in `config/pricing.yaml`.
- **The standard.** `standards/acme-secure-coding.md` is a placeholder; swap in the
  customer's approved standard (exact ISO-aligned document and version) once cleared for
  indexing.
- **Data boundaries.** Code chunks go to Voyage (embeddings/rerank) and to OpenAI (prompt
  context); embeddings and telemetry live in Atlas. Confirm this is acceptable for the
  repos you index, given GitLab is on-prem.

## Development

```bash
pip install -r requirements.txt pytest
pytest -q            # chunking, retrieval filters, prompt, ingest, API + MCP against a fake OpenAI
uvicorn codeagent.api.app:app --reload   # needs .env values exported, CONFIG_DIR=./config
```

Layout: `codeagent/ingest` (sources, chunking, pipeline), `codeagent/api` (agent, retrieval,
prompting, mcp, telemetry, metrics, rollups, app), `codeagent/eval`, `codeagent/seed`,
`web-ui/` (console), `sample-repos/`, `standards/`, `continue/`, `tests/`.

## Troubleshooting

- **Console shows "Could not load"** → `docker compose logs agent-api`; usually the Atlas
  Network Access list or a wrong `MONGODB_URI`.
- **Answers say no sources were found** → ingest still running or indexes not queryable
  yet (`docker compose logs ingest`), or the user's groups don't match the repo's
  `access_scope`.
- **401 in Continue** → token in `continue/config.yaml` doesn't match `config/users.yaml`.
- **OpenAI 400 about parameters** → set `OPENAI_MODEL` to a chat-completions model your key
  can use; the agent strips `temperature`/`top_p` for reasoning models automatically.
