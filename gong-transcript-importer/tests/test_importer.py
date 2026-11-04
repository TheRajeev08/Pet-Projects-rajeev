"""Synthetic-only tests. Never add real account names, exports or access lists."""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importer


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gong-synthetic-test-")
        self.addCleanup(self.temporary.cleanup)
        # macOS temp roots can contain /var -> /private/var; production rejects symlinks.
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "synthetic.txt"
        self.source.write_text("00:00 Synthetic A: Opening.\n00:03 Synthetic B: Closing.\n", encoding="utf-8")
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "prepared"
        self.call = {
            "call_id": "synthetic-001",
            "title": "Synthetic <call> & review",
            "source_url": "https://app.gong.io/call?id=synthetic-001&view=transcript",
            "recorded_at": "2026-11-01T10:00:00Z",
            "participants": ["Synthetic A", "Synthetic B"],
            "transcript_file": "synthetic.txt",
            "source_permissions": {
                "audience": ["Synthetic operator only"],
                "restrictions": "Synthetic no-resharing restriction.",
                "captured_at": "2026-11-01T11:00:00+00:00",
            },
            "attestations": {"export_authorized": True, "full_transcript": True},
        }

    def manifest_with(self, calls=None, **changes):
        manifest = {"version": 1, "calls": calls if calls is not None else [self.call]}
        manifest.update(changes)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return self.manifest

    def run_prepare(self):
        return importer.prepare(self.manifest_with(), self.output)

    def assert_rejected(self, message=None):
        self.manifest_with()
        with self.assertRaises(importer.ImportFailure) as error:
            importer.prepare(self.manifest, self.output)
        if message:
            self.assertIn(message, str(error.exception))
        self.assertFalse(self.output.exists())

    def document(self):
        return next(self.output.glob("*.docx"))

    def document_paragraphs(self):
        with zipfile.ZipFile(self.document()) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": importer.W}
        paragraphs = root.findall("w:body/w:p", ns)
        return ["".join(node.itertext()) for node in paragraphs]

    def test_prepare_docx_metadata_links_hashes_and_states(self):
        self.assertEqual(self.run_prepare(), 1)
        review = json.loads((self.output / "review.json").read_text())
        self.assertEqual(review["status"], "LOCAL_STAGING_ONLY")
        record = review["calls"][0]
        for key in self.call:
            self.assertEqual(record[key], self.call[key])
        self.assertEqual(record["upload_status"], "NOT_UPLOADED")
        self.assertEqual(record["destination_permissions"], "NOT_VERIFIED")
        self.assertEqual(record["search_status"], "NOT_VERIFIED")
        self.assertEqual(record["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(record["document_sha256"], hashlib.sha256(self.document().read_bytes()).hexdigest())
        expected_name = hashlib.sha256(b"synthetic-001").hexdigest() + ".docx"
        self.assertEqual(self.document().name, expected_name)
        paragraphs = self.document_paragraphs()
        self.assertIn(self.call["title"], paragraphs)
        self.assertIn(self.call["source_url"], paragraphs)
        self.assertIn("Participants: Synthetic A; Synthetic B", paragraphs)
        self.assertIn("Source restrictions: Synthetic no-resharing restriction.", paragraphs)
        self.assertIn("Recorded at: 2026-11-01T10:00:00Z", paragraphs)
        self.assertIn("00:03 Synthetic B: Closing.", paragraphs)
        with zipfile.ZipFile(self.document()) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            self.assertEqual(relationships[0].attrib["Target"], self.call["source_url"])
            self.assertEqual(relationships[0].attrib["TargetMode"], "External")
            package = ET.fromstring(archive.read("_rels/.rels"))
            self.assertEqual(package[0].attrib["Target"], "word/document.xml")

    def test_unicode_xml_and_trailing_blank_lines_survive(self):
        transcript = "\ufeff00:00 Speaker: café <tag> & \"quote\"\r\n\r\n00:04: 終わり\u0085literal\r\n\r\n"
        self.source.write_bytes(transcript.encode("utf-8"))
        self.run_prepare()
        paragraphs = self.document_paragraphs()
        self.assertEqual(paragraphs[-5:], [
            "00:00 Speaker: café <tag> & \"quote\"", "", "00:04: 終わり\u0085literal", "", "",
        ])

    def test_tabs_have_explicit_word_tab_nodes(self):
        self.source.write_text("Speaker:\tSynthetic text\n", encoding="utf-8")
        self.run_prepare()
        with zipfile.ZipFile(self.document()) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        self.assertEqual(len(root.findall(".//{" + importer.W + "}tab")), 1)

    def test_vtt_is_preserved_including_cues_and_speaker_tags(self):
        self.source = self.root / "synthetic.vtt"
        transcript = "WEBVTT\n\ncue-1\n00:00:01.000 --> 00:00:03.000\n<v Synthetic A>Hello.</v>\n"
        self.source.write_text(transcript, encoding="utf-8")
        self.call["transcript_file"] = self.source.name
        self.run_prepare()
        expected = transcript.split("\n")
        self.assertEqual(self.document_paragraphs()[-len(expected):], expected)

    def test_absolute_export_paths_are_supported(self):
        self.call["transcript_file"] = str(self.source)
        self.assertEqual(self.run_prepare(), 1)

    def test_separate_documents_preserve_separate_audiences(self):
        second = copy.deepcopy(self.call)
        second["call_id"] = "synthetic-002"
        second["source_permissions"]["audience"] = ["Different synthetic audience"]
        importer.prepare(self.manifest_with([self.call, second]), self.output)
        self.assertEqual(len(list(self.output.glob("*.docx"))), 2)
        records = json.loads((self.output / "review.json").read_text())["calls"]
        self.assertNotEqual(records[0]["source_permissions"], records[1]["source_permissions"])

    def test_output_is_private(self):
        self.run_prepare()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_staging_ancestors_are_created_privately(self):
        self.output = self.root / "private-parent" / "nested" / "batch"
        self.run_prepare()
        self.assertEqual(stat.S_IMODE(self.output.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.output.parent.parent.stat().st_mode), 0o700)

    def test_shared_staging_parent_is_rejected(self):
        self.root.chmod(0o755)
        self.assert_rejected("private directory")

    def test_no_overwrites_or_implicit_updates(self):
        self.output.mkdir()
        sentinel = self.output / "existing.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(importer.ImportFailure, "already exists"):
            self.run_prepare()
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_a_later_invalid_call_writes_no_batch(self):
        second = copy.deepcopy(self.call)
        second["call_id"] = "synthetic-002"
        second["attestations"]["full_transcript"] = False
        self.manifest_with([self.call, second])
        with self.assertRaises(importer.ImportFailure):
            importer.prepare(self.manifest, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".preparing-*")))

    def test_write_failure_removes_partial_output_and_temporary_files(self):
        with mock.patch.object(importer.os, "link", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                self.run_prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".preparing-*")))

    def test_existing_batch_created_concurrently_is_preserved(self):
        original_mkdir = Path.mkdir

        def concurrent_mkdir(path, *args, **kwargs):
            if path == self.output:
                original_mkdir(path, mode=0o700)
                (path / "other-session.txt").write_text("keep")
            return original_mkdir(path, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", concurrent_mkdir):
            with self.assertRaises(FileExistsError):
                self.run_prepare()
        self.assertEqual((self.output / "other-session.txt").read_text(), "keep")
        self.assertFalse(list(self.root.glob(".preparing-*")))

    def test_attestations_must_be_boolean_true_not_truthy(self):
        for key in ("export_authorized", "full_transcript"):
            for value in (False, None, "true", 1):
                with self.subTest(key=key, value=value):
                    self.call["attestations"] = {"export_authorized": True, "full_transcript": True}
                    self.call["attestations"][key] = value
                    self.assert_rejected("explicit")

    def test_metadata_fields_must_be_nonempty_strings(self):
        original = copy.deepcopy(self.call)
        for key in ("call_id", "title", "source_url", "recorded_at", "transcript_file"):
            for value in ("", " ", None, 123):
                with self.subTest(key=key, value=value):
                    self.call = copy.deepcopy(original)
                    self.call[key] = value
                    self.assert_rejected()

    def test_missing_unknown_and_malformed_permission_fields_are_rejected(self):
        original = copy.deepcopy(self.call)
        for mutate in (
            lambda c: c.pop("source_permissions"),
            lambda c: c.update({"unexpected": "field"}),
            lambda c: c.update({"source_permissions": None}),
            lambda c: c["source_permissions"].update({"audience": []}),
            lambda c: c["source_permissions"].update({"audience": [""]}),
            lambda c: c["source_permissions"].update({"restrictions": ""}),
            lambda c: c["source_permissions"].update({"captured_at": "yesterday"}),
            lambda c: c.update({"participants": "Synthetic A"}),
            lambda c: c.update({"participants": []}),
            lambda c: c.update({"participants": [1]}),
        ):
            self.call = copy.deepcopy(original)
            mutate(self.call)
            self.assert_rejected()

    def test_timestamps_must_include_timezones(self):
        for value in ("2026-11-01", "2026-11-01T10:00:00", "not-a-date"):
            with self.subTest(value=value):
                self.call["recorded_at"] = value
                self.assert_rejected()

    def test_non_gong_and_unsafe_source_links_are_rejected(self):
        for value in (
            "http://app.gong.io/call?id=x",
            "https://gong.io.example.org/call?id=x",
            "https://evilgong.io/call?id=x",
            "https://app.gong.io@invalid.example/call",
            "https://user:pass@app.gong.io/call",
            "https://app.gong.io:8080/call",
            "https://app.gong.io:invalid/call",
            "https://app.gong.io/call\n?id=x",
            "https://app.gong.io\\evil.example/call",
            "file:///synthetic.txt",
            "javascript:alert(1)",
        ):
            with self.subTest(value=value):
                self.call["source_url"] = value
                self.assert_rejected()

    def test_duplicate_call_ids_are_rejected(self):
        self.manifest_with([self.call, copy.deepcopy(self.call)])
        with self.assertRaisesRegex(importer.ImportFailure, "Duplicate call_id"):
            importer.prepare(self.manifest, self.output)
        self.assertFalse(self.output.exists())

    def test_manifest_shape_and_versions_are_strict(self):
        for value in (None, [], {}, {"version": 1, "calls": []},
                      {"version": True, "calls": [self.call]},
                      {"version": 2, "calls": [self.call]},
                      {"version": 1, "calls": [self.call], "upload": True}):
            with self.subTest(value=value):
                self.manifest.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(importer.ImportFailure):
                    importer.prepare(self.manifest, self.output)
                self.assertFalse(self.output.exists())

    def test_duplicate_json_keys_and_invalid_json_are_rejected(self):
        for content in ('{"version":1,"version":1,"calls":[]}', "{invalid", "\ufffd"):
            self.manifest.write_text(content, encoding="utf-8")
            with self.assertRaises(importer.ImportFailure):
                importer.prepare(self.manifest, self.output)
        self.manifest.write_bytes(b"\xff")
        with self.assertRaises(importer.ImportFailure):
            importer.prepare(self.manifest, self.output)

    def test_unsupported_export_formats_are_rejected(self):
        for suffix in (".pdf", ".json", ".docx"):
            self.call["transcript_file"] = "synthetic" + suffix
            self.assert_rejected("Only UTF-8 TXT or VTT")

    def test_empty_binary_and_invalid_xml_text_are_rejected(self):
        for content in (b"", b"  \n", b"\xff", b"text\x00bad", b"text\x0bbad"):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                self.assert_rejected()
        self.source.write_bytes(b"synthetic")
        self.call["title"] = "Synthetic\x00title"
        self.assert_rejected()

    def test_invalid_vtt_header_is_rejected(self):
        self.call["transcript_file"] = "synthetic.vtt"
        (self.root / "synthetic.vtt").write_text("00:01 incomplete export")
        self.assert_rejected("WEBVTT")

    def test_missing_files_and_directories_are_rejected(self):
        self.source.unlink()
        self.assert_rejected("regular local file")
        self.source.mkdir()
        self.assert_rejected("regular local file")

    def test_input_and_batch_limits_are_enforced(self):
        for constant, limit in (
            ("MAX_MANIFEST_BYTES", 1),
            ("MAX_TRANSCRIPT_BYTES", 1),
            ("MAX_BATCH_BYTES", 1),
            ("MAX_CALLS", 0),
        ):
            with self.subTest(constant=constant):
                with mock.patch.object(importer, constant, limit):
                    self.assert_rejected()

    def test_all_git_checkout_paths_are_rejected(self):
        repository = self.root / "checkout"
        repository.mkdir()
        for is_worktree in (False, True):
            marker = repository / ".git"
            if is_worktree:
                marker.write_text("gitdir: synthetic")
            else:
                marker.mkdir()
            for target in (repository / "manifest.json", repository / "nested" / "batch"):
                with self.assertRaisesRegex(importer.ImportFailure, "outside every Git"):
                    importer.local_path(target)
            if marker.is_dir():
                marker.rmdir()
            else:
                marker.unlink()

    def test_manifest_and_export_repository_paths_are_rejected(self):
        self.manifest_with()
        (self.root / ".git").write_text("gitdir: synthetic")
        with self.assertRaisesRegex(importer.ImportFailure, "outside every Git"):
            importer.prepare(self.manifest, self.output)

    def test_symlink_manifest_export_and_output_parents_are_rejected(self):
        self.manifest_with()
        manifest_link = self.root / "manifest-link.json"
        manifest_link.symlink_to(self.manifest)
        with self.assertRaisesRegex(importer.ImportFailure, "Symlink"):
            importer.prepare(manifest_link, self.output)
        source_link = self.root / "link.txt"
        source_link.symlink_to(self.source)
        self.call["transcript_file"] = source_link.name
        self.assert_rejected("Symlink")
        self.call["transcript_file"] = self.source.name
        parent_link = self.root / "parent-link"
        parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(importer.ImportFailure, "Symlink"):
            importer.prepare(self.manifest_with(), parent_link / "batch")

    def test_non_posix_platform_fails_closed(self):
        with mock.patch.object(importer.os, "name", "nt"):
            with self.assertRaisesRegex(importer.ImportFailure, "POSIX"):
                importer.prepare(self.manifest, self.output)

    def test_cli_smoke_reports_local_only_and_no_content(self):
        script = Path(importer.__file__).resolve()
        result = subprocess.run(
            [sys.executable, str(script), "prepare", str(self.manifest_with()),
             "--output", str(self.output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Prepared 1 local DOCX", result.stdout)
        self.assertIn("NOT UPLOADED", result.stdout)
        self.assertIn("NOT VERIFIED", result.stdout)
        self.assertNotIn(self.call["title"], result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.document().is_file())

    def test_cli_errors_do_not_report_success_or_leak_os_error_paths(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            with mock.patch.object(importer, "prepare", side_effect=OSError("sensitive path")):
                status = importer.main(["prepare", str(self.manifest), "--output", str(self.output)])
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ERROR:", stderr.getvalue())
        self.assertNotIn("sensitive path", stderr.getvalue())

    def test_docs_distinguish_github_copilot_m365_and_workiq(self):
        project = Path(importer.__file__).resolve().parent
        readme = (project / "README.md").read_text(encoding="utf-8")
        skill = (project / "skill" / "SKILL.md").read_text(encoding="utf-8")
        for document in (readme, skill):
            with self.subTest(document=document[:80]):
                self.assertIn("Microsoft 365 Copilot", document)
                self.assertIn("GitHub Copilot", document)
                self.assertIn("WorkIQ", document)
                self.assertIn("session Library", document)
                self.assertIn("does not", document)
        self.assertIn("does **not** establish that GitHub Copilot can retrieve", readme)
        self.assertIn("An empty/no-hit result is not proof", readme)
        for tool in ("get_account_gong_calls", "get_participant_gong_calls"):
            self.assertIn(tool, readme)
            self.assertIn(tool, skill)
        self.assertIn("full-transcript payload, bulk export capability and live authorization have", readme.lower())
        self.assertIn("service discovery\nconnectivity only", readme.lower())
        self.assertIn("three hits for pre-existing gong\ntranscript docx files", readme.lower())
        self.assertIn("snippets were", readme.lower())
        self.assertIn("truncated", readme.lower())
        self.assertIn("specific authorization for the exact prepared DOCX", skill)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("textutil"),
                         "Requires macOS's built-in DOCX reader")
    def test_native_docx_reader_extracts_transcript_and_metadata(self):
        self.run_prepare()
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(self.document())],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            self.call["title"], self.call["source_url"],
            "00:00 Synthetic A: Opening.", "00:03 Synthetic B: Closing.",
            self.call["source_permissions"]["restrictions"],
        ):
            self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
