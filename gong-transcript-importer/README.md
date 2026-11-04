# Gong transcript importer

Prepare full Gong call exports as searchable Word documents, then **interactively**
import them into an approved SharePoint document library or OneDrive for Business
location. This is a new, standalone project in this repository, not a new repository.

The Revenue MCP catalog exposes `get_account_gong_calls` and
`get_participant_gong_calls` for account-/participant-scoped call retrieval. Their
full-transcript payload, bulk export capability and live authorization have **not**
been tested; their availability is not evidence that any transcript can be fully
retrieved or exported. This project neither calls those tools nor assumes Gong API
credentials, Microsoft Graph access, or a tenant-wide service account. WorkIQ tool
path discovery has responded in the parent session, confirming service discovery
connectivity only—not file retrieval, transcript availability or indexing. No
scheduler, background sync, cloud provisioning, or upload code is included.

## What works locally

- Python 3.9+ on macOS/Linux; standard library only, no installation or credentials.
- UTF-8 `.txt` and `.vtt` exports, one full call per file. VTT headers, speaker tags,
  timestamps, cue lines, blank lines, Unicode and literal text are retained as text;
  no summarization or HTML interpretation. Only line endings are normalized.
- One `.docx` per call with title, call ID, recording timestamp, participants, clickable
  Gong source link, source audience/restrictions, preparation timestamp and original
  export SHA-256. `review.json` retains the metadata and generated document hashes.
- All entries validate before staging. Existing batch directories are never overwritten.
  Staged directories/files use POSIX modes `700`/`600`; symlink paths and paths inside
  Git checkouts are refused. Limit: 100 calls, 20 MiB per export, 100 MiB per batch,
  1 MiB manifest.

**Local preparation is not a successful import.** Permission metadata is not an ACL.
The tool cannot verify export entitlement, completeness, effective destination
permissions, tenant indexing or Copilot retrieval. Explicit operator attestations
are required and are labelled as attestations, not verification.

## Setup and prepare

Keep manifests, exports, review files and documents **outside all repositories**.
Never commit customer names, transcripts, access lists or real manifests, including
in tests. `.gitignore` is defense in depth, not a safe place to store customer data.
Use a private, non-synced local folder on an encrypted device that meets your
organization's data handling policy. Do not select a OneDrive/SharePoint sync folder
as staging: syncing would bypass the review gate. The tool cannot detect every sync
client, inherited ACL or device policy; the operator must verify the location.

From this project folder:

```sh
mkdir -p "$HOME/.local/share/gong-transcript-importer/input"
chmod 700 "$HOME/.local/share/gong-transcript-importer/input"
cp examples/manifest.example.json "$HOME/.local/share/gong-transcript-importer/input/manifest.json"
```

1. Start a user-initiated session. In Gong, select only calls you are authorized to
   export and whose copying is permitted by your organization's policy. Confirm
   the full transcript export is available; summaries/snippets are not substitutes.
2. Export each full TXT/VTT transcript through Gong's visible UI into the private
   input folder. If Gong only exports another format, stop: this version does not
   invent a schema, scrape hidden endpoints, or accept renamed PDF/JSON files.
3. Edit the external manifest using the schema below. Copy metadata and effective
   source access from Gong/admin-approved evidence, not from inference. The example
   is synthetic and intentionally has **false** attestations. Set them to `true`
   only after actually verifying export authorization and full transcript coverage.
4. Run:

```sh
python3 importer.py prepare "$HOME/.local/share/gong-transcript-importer/input/manifest.json"
```

The default batch location is
`~/.local/share/gong-transcript-importer/staging/<UTC timestamp>/`.
For a different private, non-synced parent:

```sh
mkdir -m 700 "$HOME/GongPrivateStaging"
python3 importer.py prepare "$HOME/.local/share/gong-transcript-importer/input/manifest.json" \
  --output "$HOME/GongPrivateStaging/batch-001"
```

Exit `0` means **local preparation only**. Exit `1` means validation/filesystem failure;
no upload was attempted. Argument errors use argparse's exit `2`. Open the generated
DOCX locally to review formatting and compare the beginning, end and speaker/cue
coverage with the full Gong export. Filenames are SHA-256 hashes of call IDs, not
customer titles. DOCX and `review.json` still contain sensitive metadata and text.

### Manifest version 1

Use the complete shape in [`examples/manifest.example.json`](examples/manifest.example.json).
Unknown/missing keys and duplicate JSON keys are errors, to catch misspelled attestations
and unsupported permission assumptions.

| Field | Required meaning |
|---|---|
| `version`, `calls` | Integer `1`; non-empty list of calls. |
| `call_id`, `title` | Stable, unique Gong call ID in this batch; original call title. |
| `source_url` | Original HTTPS Gong call link on `gong.io` or a subdomain. Custom domains are not supported. |
| `recorded_at` | ISO 8601 timestamp with timezone, obtained from the source. |
| `participants` | Non-empty list of participant labels from source metadata. |
| `transcript_file` | Absolute local path, or path relative to the manifest; UTF-8 TXT/VTT. |
| `source_permissions.audience` | Non-empty list describing verified effective source principals/groups. Participants are **not** an access list. |
| `source_permissions.restrictions` | Verified source restrictions, including export/retention/sharing constraints. Explicitly record when none apply; never infer that from missing evidence. |
| `source_permissions.captured_at` | ISO 8601 timestamp with timezone of permission verification. |
| `attestations.export_authorized` | Boolean `true` only when this export and local copy are authorized. |
| `attestations.full_transcript` | Boolean `true` only when the file is the full transcript, not a summary/excerpt. |

## Choose a retrieval path

These are separate products and data paths. A SharePoint/OneDrive upload or an
M365 Copilot search does **not** establish that GitHub Copilot can retrieve that
file. Likewise, an attached session Library file or WorkIQ result does not verify
SharePoint indexing. Verify only the path actually used and report its evidence
separately.

| Path | How the prepared DOCX becomes available | What to verify |
|---|---|---|
| SharePoint/OneDrive → Microsoft 365 Copilot | Manually upload to an approved M365 destination, then test in M365 Copilot. | Destination access and a cited M365 Copilot result. This says nothing about GitHub Copilot. |
| GitHub Copilot session Library | After explicit authorization for the exact document, attach that local DOCX to this session's Library using the Copilot app's Library action. This gives the file as context to this session; it does not sync OneDrive or change SharePoint ACLs. | Ask GitHub Copilot in this session about a distinctive phrase and check the response against the attached DOCX and Gong source link. Report Library/context access, not SharePoint indexing or general account-wide search. |
| Authorized WorkIQ retrieval | Use WorkIQ only when its M365 source is available to the signed-in user and the call/document is explicitly in scope. Search for the exact imported document or call. | Confirm WorkIQ returned the matching document/source with evidence or citation, then verify the answer against the file. An empty/no-hit result is not proof of no access, a failed export, or an indexing failure. WorkIQ availability does not prove GitHub Copilot's built-in search can find it. |

Use the [interactive assistant workflow](skill/SKILL.md) or perform the applicable
steps yourself. It is an on-demand runbook, not an installed connector or an
autonomous job. Neither this importer nor a successful local preparation proves
that WorkIQ is authorized or that any Copilot can retrieve a document.

## Permission-reviewed M365 import

Follow this section only if choosing the SharePoint/OneDrive → Microsoft 365
Copilot path. For a session Library or WorkIQ path, see the distinct instructions
below and do not infer that M365 upload is required.

1. Identify the exact organization-approved SharePoint library/folder or OneDrive
   for Business folder. Before transferring **each** call, review effective access:
   inherited site/library/folder membership, visitors, groups, owners/admins, direct
   grants and all sharing links, plus applicable sensitivity, retention and export
   restrictions. Evaluate group membership, not just matching group names.
   The destination audience must be the same as or narrower than the authorized
   source audience, and the operator must be included. If an administrator's access,
   membership, inheritance or policy equivalence cannot be established, **stop**.
   Prefer an existing restricted destination. Do not create a site, alter sharing,
   grant access or issue an "anyone"/organization-wide link to make import work.
2. Permissions do not follow from a local export. Source metadata in a DOCX records
   the original boundary but cannot enforce it. A folder with mixed source audiences
   is unsafe unless its effective access is a verified subset for **every** document.
   Do not rely on Copilot to fix overly broad SharePoint permissions later.
3. After the user explicitly authorizes transferring the specific documents to the
   named destination, upload only those `.docx` files via the visible UI. Do not
   upload the manifest, original exports or `review.json`. Decline overwrites and
   duplicate imports until the operator explicitly resolves which version to keep.
4. Open each uploaded file in M365 and check content/source link and effective file
   access again. Record its actual destination URL and observed status in a private
   import record. `review.json` deliberately starts with `NOT_UPLOADED`,
   `NOT_VERIFIED` destination permissions and `NOT_VERIFIED` search status; do not
   change these based only on clicking an upload button.
5. In Microsoft 365 Copilot with the intended authorized identity, search for the
   document title or a distinctive transcript phrase and request a cited answer.
   Open the cited document to confirm it is this file. Retrieval depends on tenant
   licensing, indexing configuration, policy and propagation delay. No fixed wait
   or indexing guarantee is claimed; an upload can succeed while search remains
   unverified. This test is specific to **Microsoft 365 Copilot**; do not report it
   as GitHub Copilot retrieval.

## GitHub Copilot session Library

To provide GitHub Copilot with a specific prepared file as context, the user can
explicitly authorize attaching that local DOCX to **this session's Library** using
the app's Library action. This is a separate transfer to session context; it is
not the SharePoint/OneDrive import, an M365 search index, or a general-purpose
repository-wide search feature. Confirm the exact selected file and that its
contents are appropriate for the session before attaching it. Never attach a
manifest, original export, review file, or other call without specific user
authorization. Do not infer consent from a request to implement or document this
workflow.

After attachment, ask GitHub Copilot in this same session about a distinctive
phrase or call detail, then compare the response with the attached DOCX and source
link. Report this as retrieval from session-provided Library context only, and
include whether the response could be verified against the file. A missing result
does not prove attachment, indexing, authorization or retrieval failure. Do not
claim that a session Library attachment is searchable in other sessions or across
the user's GitHub Copilot account.

### Limited live retrieval evidence

On 2026-11-04, a separate GitHub Copilot parent session used WorkIQ retrieval
scoped to `OneDriveAndSharePoint` and received three hits for pre-existing Gong
transcript DOCX files, with SharePoint URLs and snippets. The snippets were
truncated. This verifies GHCP → WorkIQ → M365 search connectivity for those
pre-existing files only. It does **not** verify an upload by this importer,
retrieval of a full transcript, current file permissions, or end-to-end import.
No transcript content, titles, people, or URLs are recorded here.

## WorkIQ retrieval

WorkIQ is an optional, separately authorized M365 retrieval route. Tool/path
discovery connectivity alone does not establish access to a file or transcript.
The limited live retrieval evidence above covers pre-existing files, not this
import workflow. Use WorkIQ only when the user requests it or its use is otherwise
explicitly in scope, and only through the available authenticated WorkIQ integration.
Query the specific imported document/call; inspect returned source metadata and
citations, and verify any answer against the prepared file. Report the exact source
and evidence returned.
No WorkIQ result means only that this retrieval attempt did not return a usable
match; it is not evidence that Gong export, SharePoint upload, M365 indexing or
GitHub Copilot retrieval succeeded or failed. A WorkIQ result is not proof that
GitHub Copilot's own retrieval/search has access to the same content.

Importing creates a **snapshot**, not a live permission mirror. Source revocations,
deletions, redactions and edits are not propagated. Before each import, recheck current
source permissions; use the destination only if your organization's approved
retention/revocation process covers these copies. Otherwise do not import.
Follow that process for subsequent permission changes or copy removal; the tool
does not schedule reconciliation or silently delete anything. Keep local copies
only as long as policy permits, and have the user authorize any UI deletion.

## Tests

From this folder:

```sh
python3 -m unittest discover -s tests -v
```

Tests create synthetic external temporary files, inspect DOCX XML/relationships and
hashes, exercise safety failures and invoke the CLI. They do not contact Gong,
SharePoint, OneDrive, WorkIQ or Copilot. Real export/import/retrieval validation is
deliberately left to an authorized interactive session and must identify which
retrieval path was actually tested.
