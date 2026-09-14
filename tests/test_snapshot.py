"""Publication-boundary behavior, using only disposable repositories and fake data."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools/snapshot.py"
SPEC = importlib.util.spec_from_file_location("publication_snapshot", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Publication module is unavailable")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)
GIT = shutil.which("git")


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-publication-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.destination = self.base / "public"
        for root in (self.source, self.destination):
            root.mkdir()
            self.git(root, "init", "-q")
            self.git(root, "config", "user.name", "Publication Fixture")
            self.git(root, "config", "user.email", "fixture@example.invalid")
            self.git(root, "config", "core.hooksPath", str(root / ".git/hooks"))
        (self.source / "scripts").mkdir()
        (self.source / "scripts/demo.py").write_text("print('committed fixture')\n")
        self.git(self.source, "add", "scripts/demo.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic fixture")
        for name in snapshot.ROOT_FILES - {".gitignore", "snapshot.lock.json"}:
            path = self.destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# publication fixture\n")
        shutil.copyfile(SCRIPT, self.destination / "tools/snapshot.py")
        self.write_manifest(["scripts/demo.py"])

    def git(self, root: Path, *args: str, expected: int = 0) -> bytes:
        self.assertIsNotNone(GIT)
        result = subprocess.run(  # noqa: S603 -- fixed Git in test-owned temporary repositories.
            [str(GIT), "-C", str(root), *args], capture_output=True, check=False
        )
        self.assertEqual(result.returncode, expected, result.stderr.decode(errors="replace"))
        return result.stdout

    def write_manifest(
        self, files: list[str], replacements: list[dict[str, str]] | None = None
    ) -> None:
        data = {"version": 1, "files": files, "replacements": replacements or []}
        (self.destination / "export-manifest.json").write_text(json.dumps(data))

    def test_sync_uses_committed_allowlist_and_is_idempotent(self) -> None:
        (self.source / "scripts/demo.py").write_text("uncommitted local content\n")
        (self.source / "scripts/local.log").write_text("private runtime fixture\n")
        snapshot.sync(self.destination, self.source)
        target = self.destination / "snapshot/scripts/demo.py"
        self.assertEqual(target.read_text(), "print('committed fixture')\n")
        before = (self.destination / "snapshot.lock.json").read_bytes()
        snapshot.sync(self.destination, self.source)
        self.assertEqual((self.destination / "snapshot.lock.json").read_bytes(), before)
        self.assertFalse((self.destination / "snapshot/scripts/local.log").exists())
        self.assertEqual(self.git(self.destination, "ls-files"), b"")
        snapshot.check(self.destination)

    def test_logs_and_credentials_are_ignored_but_force_added_files_fail(self) -> None:
        snapshot.sync(self.destination, self.source)
        private = (
            ".env",
            "run.log",
            "snapshot/.codex/worktrees/item/config.json",
            "snapshot/events.ndjson",
        )
        for name in private:
            path = self.destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private fixture\n")
            self.git(self.destination, "check-ignore", "-q", "--", name)
        self.git(self.destination, "add", "--all")
        snapshot.check(self.destination, staged=True)
        self.git(self.destination, "add", "-f", ".env")
        with self.assertRaisesRegex(snapshot.SnapshotError, "unexpected"):
            snapshot.check(self.destination, staged=True)

    def test_guard_checks_staged_bytes_not_a_clean_working_copy(self) -> None:
        snapshot.sync(self.destination, self.source)
        self.git(self.destination, "add", "--all")
        readme = self.destination / "README.md"
        original = readme.read_bytes()
        fake_secret = "sk-" + "a" * 32
        readme.write_text(fake_secret)
        self.git(self.destination, "add", "README.md")
        readme.write_bytes(original)
        with self.assertRaisesRegex(snapshot.SnapshotError, "value withheld") as caught:
            snapshot.check(self.destination, staged=True)
        self.assertNotIn(fake_secret, str(caught.exception))

    def test_local_snapshot_edits_are_preserved(self) -> None:
        snapshot.sync(self.destination, self.source)
        target = self.destination / "snapshot/scripts/demo.py"
        target.write_text("owner's local changes\n")
        with self.assertRaisesRegex(snapshot.SnapshotError, "Local snapshot edit"):
            snapshot.sync(self.destination, self.source)
        self.assertEqual(target.read_text(), "owner's local changes\n")

    def test_source_symlink_is_rejected_without_reading_its_target(self) -> None:
        source_file = self.source / "scripts/demo.py"
        source_file.unlink()
        source_file.symlink_to(self.base / "unread-private-file")
        self.git(self.source, "add", "scripts/demo.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "Link fixture")
        with self.assertRaisesRegex(snapshot.SnapshotError, "symlink"):
            snapshot.sync(self.destination, self.source)
        self.assertFalse((self.destination / "snapshot.lock.json").exists())

    def test_local_executable_mode_change_is_preserved(self) -> None:
        snapshot.sync(self.destination, self.source)
        target = self.destination / "snapshot/scripts/demo.py"
        changed = 0o644 if target.stat().st_mode & 0o111 else 0o755
        target.chmod(changed)
        with self.assertRaisesRegex(snapshot.SnapshotError, "Local snapshot edit"):
            snapshot.sync(self.destination, self.source)
        self.assertEqual(target.stat().st_mode & 0o777, changed)

    def test_destination_symlink_cannot_escape_the_export(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.destination / "snapshot").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(snapshot.SnapshotError, "Symlink"):
            snapshot.sync(self.destination, self.source)
        self.assertEqual(list(outside.iterdir()), [])

    def test_changed_redaction_anchor_stops_before_writes(self) -> None:
        self.write_manifest(
            ["scripts/demo.py"],
            [{"path": "scripts/demo.py", "old": "missing private detail", "new": "generic"}],
        )
        with self.assertRaisesRegex(snapshot.SnapshotError, "replacement drift"):
            snapshot.sync(self.destination, self.source)
        self.assertFalse((self.destination / "snapshot").exists())

    def test_only_unchanged_managed_files_are_pruned(self) -> None:
        (self.source / "scripts/second.py").write_text("# second fixture\n")
        self.git(self.source, "add", "scripts/second.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "Second fixture")
        self.write_manifest(["scripts/demo.py", "scripts/second.py"])
        snapshot.sync(self.destination, self.source)
        local = self.destination / "snapshot/local.log"
        local.write_text("private local fixture\n")
        self.write_manifest(["scripts/demo.py"])
        snapshot.sync(self.destination, self.source)
        self.assertFalse((self.destination / "snapshot/scripts/second.py").exists())
        self.assertTrue(local.exists())
        snapshot.check(self.destination)

    def test_hook_checks_index_and_preserves_unrelated_hooks(self) -> None:
        snapshot.sync(self.destination, self.source)
        snapshot.install_hook(self.destination)
        self.git(self.destination, "add", "--all")
        hook = self.destination / ".git/hooks/pre-commit"
        self.assertTrue(os.access(hook, os.X_OK))
        result = subprocess.run(  # noqa: S603 -- own publication guard in a disposable repository.
            [str(hook)], cwd=self.destination, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        hook.write_text("#!/bin/sh\n# unrelated owner hook\n")
        with self.assertRaisesRegex(snapshot.SnapshotError, "Existing pre-commit hook preserved"):
            snapshot.install_hook(self.destination)
        self.assertIn("unrelated owner hook", hook.read_text())

    def test_relocation_prunes_old_path_and_remains_idempotent(self) -> None:
        snapshot.sync(self.destination, self.source)
        manifest_path = self.destination / "export-manifest.json"
        value = json.loads(manifest_path.read_text())
        value.update(version=2, destinations={"scripts/demo.py": "shared/scripts/demo.py"})
        manifest_path.write_text(json.dumps(value))
        snapshot.sync(self.destination, self.source)
        self.assertFalse((self.destination / "snapshot/scripts/demo.py").exists())
        self.assertTrue((self.destination / "shared/scripts/demo.py").exists())
        first = (self.destination / "snapshot.lock.json").read_bytes()
        snapshot.sync(self.destination, self.source)
        self.assertEqual((self.destination / "snapshot.lock.json").read_bytes(), first)
        self.git(self.destination, "add", "--all")
        snapshot.check(self.destination, staged=True)

    def test_relocation_preserves_edited_old_file(self) -> None:
        snapshot.sync(self.destination, self.source)
        old = self.destination / "snapshot/scripts/demo.py"
        old.write_text("local owner edit")
        path = self.destination / "export-manifest.json"
        value = json.loads(path.read_text())
        value.update(version=2, destinations={"scripts/demo.py": "adapters/TRIP/demo.py"})
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(snapshot.SnapshotError, "Local snapshot edit"):
            snapshot.sync(self.destination, self.source)
        self.assertEqual(old.read_text(), "local owner edit")
        self.assertFalse((self.destination / "adapters/TRIP/demo.py").exists())

    def test_mapping_rejects_collision_and_private_destinations(self) -> None:
        for targets in (
            ["shared/a.py", "shared/a.py"],
            ["shared/a.py", "shared/a.py/child"],
            [".git/config", "shared/a.py"],
            ["shared/../escape", "shared/a.py"],
            ["adapters/TRIP/state/run.json", "shared/a.py"],
        ):
            with self.subTest(targets=targets):
                value = {
                    "version": 2,
                    "files": ["scripts/a.py", "scripts/b.py"],
                    "replacements": [],
                    "destinations": dict(
                        zip(["scripts/a.py", "scripts/b.py"], targets, strict=True)
                    ),
                }
                with self.assertRaises(snapshot.SnapshotError):
                    snapshot.manifest(json.dumps(value).encode())

    def test_relinks_cross_adapter_relative_and_root_references(self) -> None:
        files = {
            ".claude/skills/nadili-process/adapters/trip.md": "adapters/TRIP/adapter.md",
            ".claude/skills/nadili-process/SKILL.md": "shared/process/SKILL.md",
            ".claude/skills/nadili-process/plan.md": "adapters/TRIP/plan.md",
        }
        text = (
            "[process](../SKILL.md#stage) `../SKILL.md` "
            "`.claude/skills/nadili-process/plan.md` "
            "[external](https://example.invalid/) `docs/private.md`"
        )
        result = snapshot.relink(
            ".claude/skills/nadili-process/adapters/trip.md",
            "adapters/TRIP/adapter.md",
            text.encode(),
            files,
        ).decode()
        self.assertIn("[process](../../shared/process/SKILL.md#stage)", result)
        self.assertIn("`shared/process/SKILL.md`", result)
        self.assertIn("`adapters/TRIP/plan.md`", result)
        self.assertIn("[external](https://example.invalid/)", result)
        self.assertIn("`docs/private.md`", result)

    def test_counted_substitutions_stop_when_reference_count_drifts(self) -> None:
        source = self.source / "scripts/demo.py"
        source.write_text("# legacy-name legacy-name\n")
        self.git(self.source, "add", "scripts/demo.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "References fixture")
        value = {
            "version": 1,
            "files": ["scripts/demo.py"],
            "replacements": [
                {
                    "path": "scripts/demo.py",
                    "old": "legacy-name",
                    "new": "synthetic-name",
                    "count": 2,
                }
            ],
        }
        manifest_path = self.destination / "export-manifest.json"
        manifest_path.write_text(json.dumps(value))
        snapshot.sync(self.destination, self.source)
        exported = self.destination / "snapshot/scripts/demo.py"
        self.assertEqual(exported.read_text(), "# synthetic-name synthetic-name\n")
        source.write_text("# legacy-name\n")
        self.git(self.source, "add", "scripts/demo.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "Drift fixture")
        with self.assertRaisesRegex(snapshot.SnapshotError, "replacement drift"):
            snapshot.sync(self.destination, self.source)
        self.assertEqual(exported.read_text(), "# synthetic-name synthetic-name\n")

    def test_private_manifest_paths_are_rejected(self) -> None:
        for name in (
            "../outside.py",
            ".env",
            "scripts/state/session.json",
            ".codex/worktrees/x/a.py",
        ):
            with self.subTest(name=name):
                self.write_manifest([name])
                with self.assertRaises(snapshot.SnapshotError):
                    snapshot.sync(self.destination, self.source)


if __name__ == "__main__":
    unittest.main()
