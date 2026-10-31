import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import aurctl_lib as ctl


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "management"
        self.root.mkdir()
        self.remotes = self.base / "remotes"
        self.remotes.mkdir()
        self.root_patch = patch.object(ctl, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.env_patch = patch.dict(os.environ, {"AUR_REMOTE_ROOT": str(self.remotes)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.name = "example-git"
        self.seed = self.base / "seed"
        git("init", "-b", "master", str(self.seed))
        git("config", "user.name", "test", cwd=self.seed)
        git("config", "user.email", "test@example.invalid", cwd=self.seed)
        (self.seed / "PKGBUILD").write_text("pkgname=example-git\npkgver=r1.abcdef0\npkgrel=1\n")
        (self.seed / ".SRCINFO").write_text("pkgbase = example-git\n\tpkgver = r1.abcdef0\n\tpkgrel = 1\n\tmakedepends = git\n\tdepends = openrc\n\npkgname = example-git\n")
        (self.seed / "old.patch").write_text("old patch\n")
        git("add", ".", cwd=self.seed)
        git("commit", "-m", "chore: seed package", cwd=self.seed)
        git("clone", "--bare", str(self.seed), str(self.remotes / f"{self.name}.git"))
        ctl.import_package(self.name)

    def receipt(self):
        ctl.write_json(self.root / ".cache" / "validated.json", {self.name: ctl.fingerprint(self.name)})

    def remote_commit(self):
        return git("--git-dir", str(self.remotes / f"{self.name}.git"), "rev-parse", "master")

    def test_import_records_current_aur_and_does_not_copy_git_metadata(self):
        state = json.loads((self.root / ".aur" / f"{self.name}.json").read_text())
        self.assertEqual(state["commit"], self.remote_commit())
        self.assertEqual(state["files"], ctl.snapshot(ctl.package_path(self.name)))
        self.assertFalse((ctl.package_path(self.name) / ".git").exists())
        with self.assertRaises(ctl.Error):
            ctl.import_package(self.name)

    def test_dry_run_never_pushes_or_changes_state(self):
        original = self.remote_commit()
        state = (self.root / ".aur" / f"{self.name}.json").read_bytes()
        (ctl.package_path(self.name) / "old.patch").unlink()
        ctl.publish(self.name, dry_run=True)
        self.assertEqual(self.remote_commit(), original)
        self.assertEqual((self.root / ".aur" / f"{self.name}.json").read_bytes(), state)

    def test_publish_requires_fresh_full_build_receipt(self):
        with self.assertRaisesRegex(ctl.Error, "receipt"):
            ctl.publish(self.name)
        self.receipt()
        (self.root / "configs" / f"{self.name}.toml").write_text('[package]\nkind="vcs"\npublish=true\n# changed\n')
        with self.assertRaisesRegex(ctl.Error, "receipt"):
            ctl.publish(self.name)

    def test_publish_removes_files_and_preserves_aur_history(self):
        parent = self.remote_commit()
        recipe = ctl.package_path(self.name)
        (recipe / "old.patch").unlink()
        (recipe / "new.patch").write_text("new patch\n")
        self.receipt()
        ctl.publish(self.name)
        current = self.remote_commit()
        self.assertNotEqual(current, parent)
        repo = self.remotes / f"{self.name}.git"
        self.assertEqual(git("--git-dir", str(repo), "rev-parse", "master^"), parent)
        self.assertEqual(git("--git-dir", str(repo), "show", "master:new.patch"), "new patch")
        self.assertNotIn("old.patch", git("--git-dir", str(repo), "ls-tree", "--name-only", "master"))
        ctl.publish(self.name)
        self.assertEqual(self.remote_commit(), current, "No-op publishing must not create commits")

    def test_publish_tracks_explicit_recipe_files_even_when_gitignored(self):
        recipe = ctl.package_path(self.name)
        (recipe / ".gitignore").write_text("*.patch\n")
        (recipe / "new.patch").write_text("needed source patch\n")
        self.receipt()
        ctl.publish(self.name)
        self.assertEqual(git("--git-dir", str(self.remotes / f"{self.name}.git"), "show", "master:new.patch"), "needed source patch")

    def test_publish_handles_directory_to_file_changes(self):
        recipe = ctl.package_path(self.name)
        (recipe / "nested").mkdir()
        (recipe / "nested" / "source.patch").write_text("patch\n")
        self.receipt()
        ctl.publish(self.name)
        shutil.rmtree(recipe / "nested")
        (recipe / "nested").write_text("now a regular file\n")
        self.receipt()
        ctl.publish(self.name)
        self.assertEqual(git("--git-dir", str(self.remotes / f"{self.name}.git"), "show", "master:nested"), "now a regular file")

    def test_co_maintainer_changes_are_never_overwritten(self):
        (self.seed / "old.patch").write_text("co-maintainer change\n")
        git("add", ".", cwd=self.seed)
        git("commit", "-m", "fix: upstream patch", cwd=self.seed)
        git("push", str(self.remotes / f"{self.name}.git"), "master", cwd=self.seed)
        changed = self.remote_commit()
        (ctl.package_path(self.name) / "old.patch").write_text("our change\n")
        self.receipt()
        with self.assertRaisesRegex(ctl.Error, "independently"):
            ctl.publish(self.name)
        self.assertEqual(self.remote_commit(), changed)

    def test_successful_aur_push_is_recovered_if_state_commit_was_lost(self):
        state_path = self.root / ".aur" / f"{self.name}.json"
        original_state = state_path.read_bytes()
        (ctl.package_path(self.name) / "old.patch").write_text("new patch\n")
        self.receipt()
        ctl.publish(self.name)
        published = self.remote_commit()
        state_path.write_bytes(original_state)
        ctl.publish(self.name)
        self.assertEqual(self.remote_commit(), published)
        self.assertEqual(json.loads(state_path.read_text())["commit"], published)

    def test_symlink_and_path_traversal_rejected(self):
        for name in ("../other", "..", "/tmp/other", "-option", "x/y"):
            with self.subTest(name=name), self.assertRaises(ctl.Error):
                ctl.valid_name(name)
        (ctl.package_path(self.name) / "linked").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ctl.Error, "Unsupported"):
            ctl.snapshot(ctl.package_path(self.name))

    def test_dependency_exception_only_omits_runtime_dependencies(self):
        self.assertEqual(ctl.dependencies(self.name), ["git", "openrc"])
        path = self.root / "configs" / f"{self.name}.toml"
        with path.open("a") as file:
            file.write('\n[validation]\nskip_runtime_dependencies=true\nreason="OpenRC host required"\n')
        self.assertEqual(ctl.dependencies(self.name), ["git"])

    def test_vcs_updates_do_not_mutate_recipe(self):
        original = ctl.fingerprint(self.name)
        ctl.update(self.name)
        self.assertEqual(ctl.fingerprint(self.name), original)

    def test_build_changes_to_pkgver_remain_in_temporary_directory(self):
        original = ctl.fingerprint(self.name)
        metadata = (ctl.package_path(self.name) / ".SRCINFO").read_text()

        def fake_run(*args, cwd=None, **kwargs):
            if args[0] == "makepkg" and "--force" in args:
                (cwd / "PKGBUILD").write_text("pkgver=r999.1234567\n")
                (cwd / "example.pkg.tar.zst").write_text("package")
            return ""

        with patch.object(ctl, "srcinfo", return_value=metadata), patch.object(ctl, "run", side_effect=fake_run):
            ctl.validate(self.name, build=True)
        self.assertEqual(ctl.fingerprint(self.name), original)

    def test_srcinfo_drift_blocks_validation(self):
        with patch.object(ctl, "srcinfo", return_value="different metadata\n"), patch.object(ctl, "run"):
            with self.assertRaisesRegex(ctl.Error, "SRCINFO differs"):
                ctl.validate(self.name, build=True)

    def test_failed_revalidation_invalidates_previous_receipt(self):
        self.receipt()
        with patch.object(ctl, "validate", side_effect=ctl.Error("build failed")), patch.object(sys, "argv", ["aurctl", "validate", "--all", "--build"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                ctl.main()
        self.assertEqual(result.exception.code, 1)
        receipt = json.loads((self.root / ".cache" / "validated.json").read_text())
        self.assertNotIn(self.name, receipt)

    def test_release_update_is_atomic_on_checksum_failure(self):
        ctl.write_config(self.name, "release", "owner/project", publish=True)
        original = ctl.fingerprint(self.name)

        def fake_run(*args, **kwargs):
            if args[0] == "vercmp":
                return "1"
            raise ctl.Error("checksum download failed")

        with patch.object(ctl, "latest", return_value="2.0.0"), patch.object(ctl, "run", side_effect=fake_run):
            with self.assertRaisesRegex(ctl.Error, "checksum"):
                ctl.update(self.name)
        self.assertEqual(ctl.fingerprint(self.name), original)

    def test_release_downgrade_is_rejected(self):
        with patch.object(ctl, "latest", return_value="0.1"), patch.object(ctl, "run", return_value="-1"):
            with self.assertRaisesRegex(ctl.Error, "downgrade"):
                ctl.update(self.name)

    def test_initial_registration_requires_empty_aur_and_preserves_files(self):
        new_name = "new-example-git"
        destination = ctl.package_path(new_name)
        shutil.copytree(ctl.package_path(self.name), destination)
        for filename in ("PKGBUILD", ".SRCINFO"):
            path = destination / filename
            path.write_text(path.read_text().replace(self.name, new_name))
        ctl.write_config(new_name, "vcs", publish=True)
        git("init", "--bare", "--initial-branch=master", str(self.remotes / f"{new_name}.git"))
        ctl.write_json(self.root / ".cache" / "validated.json", {new_name: ctl.fingerprint(new_name)})
        ctl.register(new_name, dry_run=True)
        self.assertFalse((self.root / ".aur" / f"{new_name}.json").exists())
        ctl.register(new_name)
        state = json.loads((self.root / ".aur" / f"{new_name}.json").read_text())
        self.assertEqual(state["files"], ctl.snapshot(destination))
        with self.assertRaisesRegex(ctl.Error, "already imported"):
            ctl.register(new_name)

    def test_registration_refuses_existing_aur_repository(self):
        (self.root / ".aur" / f"{self.name}.json").unlink()
        self.receipt()
        before = self.remote_commit()
        with self.assertRaisesRegex(ctl.Error, "already exists"):
            ctl.register(self.name)
        self.assertEqual(before, self.remote_commit())


class OutputTests(unittest.TestCase):
    def test_logging_streams(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            ctl.log("debug", "DEBUG")
            ctl.log("info")
            ctl.log("warn", "WARN")
            ctl.log("error", "ERROR")
        self.assertEqual(stdout.getvalue(), "DEBUG debug\nINFO info\n")
        self.assertEqual(stderr.getvalue(), "WARN warn\nERROR error\n")


if __name__ == "__main__":
    unittest.main()
