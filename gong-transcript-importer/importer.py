#!/usr/bin/env python3
"""Prepare local Gong exports for a separately authorized, interactive M365 import."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile
from xml.sax.saxutils import escape


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TRANSCRIPT_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_CALLS = 100
DEFAULT_STAGING = Path.home() / ".local" / "share" / "gong-transcript-importer" / "staging"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class ImportFailure(Exception):
    """An actionable validation or filesystem error."""


def local_path(value):
    """Reject symlink traversal and repository paths before accessing private data."""
    path = Path(os.path.abspath(Path(value).expanduser()))
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ImportFailure("Symlink paths are not allowed for manifests, exports or staging.")
        if (candidate / ".git").exists():
            raise ImportFailure("Manifests, exports and staging must be outside every Git checkout.")
    return path


def read_limited(path, limit):
    if not path.is_file():
        raise ImportFailure("Expected a regular local file.")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ImportFailure("Input exceeds the supported size limit.")
    return content


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ImportFailure(f"{field} must be a non-empty string.")
    # XML 1.0 cannot represent these characters; never silently drop transcript text.
    if any(
        not (c in "\t\n\r" or "\x20" <= c <= "\ud7ff"
             or "\ue000" <= c <= "\ufffd" or "\U00010000" <= c <= "\U0010ffff")
        for c in value
    ):
        raise ImportFailure(f"{field} contains unsupported XML characters.")
    return value


def timestamp(value, field):
    value = text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportFailure(f"{field} must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportFailure(f"{field} must include a timezone.")
    return value


def keys(value, expected, field):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ImportFailure(f"{field} must contain exactly: {', '.join(expected)}.")


def strings(value, field):
    if not isinstance(value, list) or not value:
        raise ImportFailure(f"{field} must be a non-empty list.")
    return [text(item, field) for item in value]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportFailure("Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def load_calls(manifest_path):
    raw = read_limited(manifest_path, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError) as exc:
        raise ImportFailure("Manifest must be valid UTF-8 JSON.") from exc
    keys(manifest, ("version", "calls"), "manifest")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ImportFailure("Only manifest version 1 is supported.")
    calls = manifest["calls"]
    if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_CALLS:
        raise ImportFailure(f"calls must contain 1 to {MAX_CALLS} entries.")
    seen = set()
    prepared = []
    total_bytes = 0
    for index, call in enumerate(calls, 1):
        label = f"calls[{index}]"
        keys(call, (
            "call_id", "title", "source_url", "recorded_at", "participants",
            "transcript_file", "source_permissions", "attestations",
        ), label)
        call_id = text(call["call_id"], f"{label}.call_id")
        if call_id in seen:
            raise ImportFailure("Duplicate call_id values are not allowed in a batch.")
        seen.add(call_id)
        text(call["title"], f"{label}.title")
        timestamp(call["recorded_at"], f"{label}.recorded_at")
        strings(call["participants"], f"{label}.participants")
        source_url = text(call["source_url"], f"{label}.source_url")
        try:
            url = urlsplit(source_url)
            valid_url = (
                url.scheme == "https"
                and url.hostname is not None
                and (url.hostname == "gong.io" or url.hostname.endswith(".gong.io"))
                and url.username is None and url.password is None
                and url.port in (None, 443)
                and not any(c.isspace() or ord(c) < 32 for c in source_url)
                and "\\" not in source_url
            )
        except ValueError as exc:
            raise ImportFailure(f"{label}.source_url is invalid.") from exc
        if not valid_url:
            raise ImportFailure(f"{label}.source_url must be an HTTPS Gong source link.")
        permissions = call["source_permissions"]
        keys(permissions, ("audience", "restrictions", "captured_at"),
             f"{label}.source_permissions")
        strings(permissions["audience"], f"{label}.source_permissions.audience")
        text(permissions["restrictions"], f"{label}.source_permissions.restrictions")
        timestamp(permissions["captured_at"], f"{label}.source_permissions.captured_at")
        attestations = call["attestations"]
        keys(attestations, ("export_authorized", "full_transcript"),
             f"{label}.attestations")
        if any(value is not True for value in attestations.values()):
            raise ImportFailure("Each call needs explicit export_authorized and full_transcript attestations.")
        source = Path(text(call["transcript_file"], f"{label}.transcript_file")).expanduser()
        if not source.is_absolute():
            source = manifest_path.parent / source
        source = local_path(source)
        if source.suffix.lower() not in (".txt", ".vtt"):
            raise ImportFailure("Only UTF-8 TXT or VTT full transcript exports are supported.")
        content = read_limited(source, MAX_TRANSCRIPT_BYTES)
        total_bytes += len(content)
        if total_bytes > MAX_BATCH_BYTES:
            raise ImportFailure("Batch exceeds the supported total export size limit.")
        try:
            transcript = content.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ImportFailure("Transcript must be UTF-8; explicitly convert the export first.") from exc
        text(transcript, f"{label}.transcript")
        if source.suffix.lower() == ".vtt" and not transcript.startswith("WEBVTT"):
            raise ImportFailure("VTT exports must start with WEBVTT.")
        prepared.append((call, content, transcript))
    return prepared


def paragraph(value):
    # Keep every line, tab and explicit line break, including blank lines and VTT cues.
    runs = []
    for index, line in enumerate(value.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if index:
            runs.append("<w:br/>")
        for part_index, part in enumerate(line.split("\t")):
            if part_index:
                runs.append("<w:tab/>")
            runs.append(f'<w:t xml:space="preserve">{escape(part)}</w:t>')
    return f"<w:p><w:r>{''.join(runs)}</w:r></w:p>"


def make_docx(call, transcript, source_hash, imported_at):
    permissions = call["source_permissions"]
    metadata = [
        call["title"],
        f"Gong call ID: {call['call_id']}",
        f"Recorded at: {call['recorded_at']}",
        "Participants: " + "; ".join(call["participants"]),
        "Source audience (recorded, NOT applied as a document ACL): "
        + "; ".join(permissions["audience"]),
        "Source restrictions: " + permissions["restrictions"],
        "Permissions captured at: " + permissions["captured_at"],
        "Prepared at: " + imported_at,
        "Original export SHA-256: " + source_hash,
        "Completeness: full transcript attested by operator; not verified against Gong.",
    ]
    body = "".join(paragraph(line) for line in metadata)
    body += (
        '<w:p><w:hyperlink r:id="source"><w:r><w:t>'
        + escape(call["source_url"]) + "</w:t></w:r></w:hyperlink></w:p>"
    )
    body += paragraph("Full transcript")
    # Separate paragraphs keep long calls practical in Word and for document indexing.
    normalized = transcript.replace("\r\n", "\n").replace("\r", "\n")
    body += "".join(paragraph(line) for line in normalized.split("\n"))
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}" xmlns:r="{R}">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{relationship_ns}"><Relationship Id="document" '
            f'Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": document,
        "word/_rels/document.xml.rels": (
            f'<Relationships xmlns="{relationship_ns}"><Relationship Id="source" '
            f'Type="{R}/hyperlink" Target="{escape(call["source_url"], {chr(34): "&quot;"})}" '
            'TargetMode="External"/></Relationships>'
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value.encode("utf-8"))
    return buffer.getvalue()


def private_directory(path):
    """Create missing ancestors privately; refuse an already shared staging parent."""
    if path.exists():
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ImportFailure("Staging parent must be a private directory (chmod 700).")
        if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
            raise ImportFailure("Staging parent must be owned by the current user.")
        return
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def write_private(path, content):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)


def prepare(manifest, output):
    if os.name != "posix":
        raise ImportFailure("This version requires POSIX file permissions (macOS/Linux).")
    manifest_path = local_path(manifest)
    output = local_path(output)
    if output.exists():
        raise ImportFailure("Output already exists; choose a new batch directory. No files were overwritten.")
    calls = load_calls(manifest_path)
    private_directory(output.parent)
    temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=output.parent))
    imported_at = datetime.now(timezone.utc).isoformat()
    records = []
    try:
        for call, raw, transcript in calls:
            source_hash = hashlib.sha256(raw).hexdigest()
            name = hashlib.sha256(call["call_id"].encode("utf-8")).hexdigest() + ".docx"
            document = make_docx(call, transcript, source_hash, imported_at)
            write_private(temporary / name, document)
            records.append({
                **call,
                "document": name,
                "source_sha256": source_hash,
                "document_sha256": hashlib.sha256(document).hexdigest(),
                "source_bytes": len(raw),
                "destination_permissions": "NOT_VERIFIED",
                "upload_status": "NOT_UPLOADED",
                "search_status": "NOT_VERIFIED",
            })
        review = {
            "version": 1,
            "prepared_at": imported_at,
            "status": "LOCAL_STAGING_ONLY",
            "warning": "Metadata is not an ACL. Review each call's effective destination access before upload.",
            "calls": records,
        }
        write_private(temporary / "review.json",
                      (json.dumps(review, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))
        # Hard-linking into an exclusive directory avoids overwriting a concurrent batch.
        output.mkdir(mode=0o700)
        try:
            for item in temporary.iterdir():
                os.link(item, output / item.name)
        except OSError:
            shutil.rmtree(output)
            raise
    finally:
        shutil.rmtree(temporary)
    return len(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare", help="Validate local exports and stage DOCX files; never upload.")
    command.add_argument("manifest", type=Path, help="External UTF-8 JSON manifest (see README).")
    command.add_argument("--output", type=Path, default=None,
                         help="New batch directory under a private, non-synced parent.")
    args = parser.parse_args(argv)
    output = args.output or DEFAULT_STAGING / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        count = prepare(args.manifest, output)
    except (ImportFailure, OSError) as exc:
        # Filesystem exception paths may contain customer names; keep stderr metadata-free.
        message = str(exc) if isinstance(exc, ImportFailure) else "Local filesystem operation failed; check paths, permissions and free space."
        print("ERROR: " + message, file=sys.stderr)
        return 1
    print(f"Prepared {count} local DOCX file(s) in {output}.")
    print("NOT UPLOADED. Destination permissions and Copilot search are NOT VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
