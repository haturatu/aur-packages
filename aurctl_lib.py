"""Small, dependency-free coordinator for makepkg, nvchecker, and AUR Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
NAME = re.compile(r"[a-z0-9][a-z0-9+_.-]*\Z")


class Error(Exception):
    pass


def log(message, level="INFO"):
    print(f"{level} {message}", file=sys.stderr if level in {"WARN", "ERROR"} else sys.stdout, flush=True)


def run(*args, cwd=None, capture=False):
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE if capture else None, check=True)
    return result.stdout.strip() if capture else ""


def valid_name(name):
    if not NAME.fullmatch(name) or name in {".", ".."}:
        raise Error(f"Invalid package base: {name}")
    return name


def package_path(name):
    return ROOT / "packages" / valid_name(name)


def config(name):
    path = ROOT / "configs" / f"{valid_name(name)}.toml"
    data = tomllib.loads(path.read_text())
    if data.get("package", {}).get("kind") not in {"vcs", "release"}:
        raise Error(f"{name}: package.kind must be vcs or release")
    if not isinstance(data["package"].get("publish", False), bool):
        raise Error(f"{name}: package.publish must be boolean")
    if data.get("validation", {}).get("skip_runtime_dependencies") and not data["validation"].get("reason"):
        raise Error(f"{name}: skipping runtime dependencies requires validation.reason")
    return data


def names(selected=None):
    found = sorted(p.stem for p in (ROOT / "configs").glob("*.toml"))
    if not found:
        raise Error("No package configurations found")
    selected = selected or found
    for name in selected:
        valid_name(name)
        if name not in found:
            raise Error(f"Unknown package: {name}")
        config(name)
        snapshot(package_path(name))
    return selected


def snapshot(directory):
    """Include modes and every recipe file; reject links and repository metadata."""
    files = {}
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory)
        if ".git" in rel.parts:
            continue
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise Error(f"Unsupported package file: {path}")
        if path.is_file():
            files[rel.as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "executable": bool(path.stat().st_mode & 0o111),
            }
    if "PKGBUILD" not in files or ".SRCINFO" not in files:
        raise Error(f"PKGBUILD and .SRCINFO required: {directory}")
    return files


def fingerprint(name):
    return {"files": snapshot(package_path(name)), "config": hashlib.sha256((ROOT / "configs" / f"{name}.toml").read_bytes()).hexdigest()}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def remote_url(name, write=False):
    # The override supports local bare repositories in integration tests.
    override = os.environ.get("AUR_REMOTE_ROOT")
    if override:
        return str(Path(override) / f"{name}.git")
    return f"ssh://aur@aur.archlinux.org/{name}.git" if write else f"https://aur.archlinux.org/{name}.git"


def clone(name, destination, write=False):
    run("git", "clone", "--quiet", "--branch", "master", remote_url(name, write), str(destination))


def copy_recipe(source, destination):
    for rel in snapshot(source):
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, target)


def srcinfo(directory):
    # Preserve makepkg's exact whitespace, including its final newline.
    return subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=directory, text=True)


def fields(directory):
    data = {}
    for line in (directory / ".SRCINFO").read_text().splitlines():
        if " = " in line:
            key, value = line.strip().split(" = ", 1)
            data.setdefault(key, []).append(value)
    return data


def write_config(name, kind, upstream=None, publish=False):
    text = f'[package]\nkind = "{kind}"\npublish = {str(publish).lower()}\n'
    if kind == "release" and upstream:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", upstream):
            raise Error("GitHub upstream must be owner/repository")
        text += f'\n[nvchecker]\nsource = "github"\ngithub = "{upstream}"\nuse_latest_release = true\nprefix = "v"\n'
    path = ROOT / "configs" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def import_package(name):
    valid_name(name)
    destination = package_path(name)
    conf = ROOT / "configs" / f"{name}.toml"
    if destination.exists() or conf.exists():
        raise Error(f"{name}: already managed; refusing to overwrite")
    with tempfile.TemporaryDirectory(prefix="aur-import-") as temp:
        repo = Path(temp) / name
        clone(name, repo)
        # Import only files tracked by AUR Git, never local build artifacts.
        snapshot(repo)
        destination.mkdir(parents=True)
        copy_recipe(repo, destination)
        revision = run("git", "rev-parse", "HEAD", cwd=repo, capture=True)
        write_json(ROOT / ".aur" / f"{name}.json", {"commit": revision, "files": snapshot(repo)})
    write_config(name, "vcs" if name.endswith(("-git", "-hg", "-svn")) else "release", publish=True)
    log(f"Imported {name} from AUR master ({revision[:12]})")


def discover(maintainer):
    url = "https://aur.archlinux.org/rpc/v5/search/" + urllib.parse.quote(maintainer, safe="") + "?by=maintainer"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    if data.get("type") == "error" or not data.get("results"):
        raise Error(f"AUR returned no packages for {maintainer}")
    return sorted({valid_name(row["PackageBase"]) for row in data["results"]})


def add_template(name, template, upstream):
    valid_name(name)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", upstream or ""):
        raise Error("--upstream owner/repository is required for templates")
    destination = package_path(name)
    if destination.exists() or (ROOT / "configs" / f"{name}.toml").exists():
        raise Error(f"{name}: already exists")
    project = upstream.split("/")[1]
    vcs = template == "vcs"
    source = f"git+https://github.com/{upstream}.git" if vcs else f"https://github.com/{upstream}/archive/refs/tags/v$pkgver.tar.gz"
    text = f'''# Review the recipe and enable package.publish in configs/{name}.toml.
pkgname={name}
pkgver={"r0.0000000" if vcs else "0.0.0"}
pkgrel=1
pkgdesc="TODO: describe the package"
arch=('x86_64')
url="https://github.com/{upstream}"
license=('LicenseRef-unknown')
makedepends=({'"git"' if vcs else ''})
source=("{source}")
sha256sums=({'"SKIP"' if vcs else '"TODO"'})
'''
    if vcs:
        text += f'''\npkgver() {{
  cd "$srcdir/{project}"
  printf 'r%s.%s' "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}}
'''
    text += '''\npackage() {
  # TODO: implement build(), check(), and package() for this upstream.
  return 1
}
'''
    destination.mkdir(parents=True)
    (destination / "PKGBUILD").write_text(text)
    try:
        (destination / ".SRCINFO").write_text(srcinfo(destination))
        write_config(name, "vcs" if vcs else "release", upstream)
    except Exception:
        shutil.rmtree(destination)
        raise
    log(f"Created {name}; finish PKGBUILD, validate, then set package.publish = true")


def latest(name):
    conf = config(name)
    if conf["package"]["kind"] == "vcs":
        return None
    source = conf.get("nvchecker")
    if not source or not source.get("source"):
        raise Error(f"{name}: release packages require an [nvchecker] table")
    with tempfile.TemporaryDirectory(prefix="aur-version-") as temp:
        path = Path(temp) / "nvchecker.toml"
        # Config values are TOML scalars or scalar lists; JSON encoding is TOML-compatible here.
        path.write_text(f'[{json.dumps(name)}]\n' + "\n".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in source.items()) + "\n")
        output = run("nvchecker", "--logger=json", "-c", str(path), capture=True)
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        versions = [e["version"] for e in events if e.get("name") == name and e.get("event") in {"updated", "up-to-date"} and e.get("version")]
        if any(e.get("level") in {"error", "critical"} for e in events) or not versions:
            raise Error(f"{name}: nvchecker failed: {output}")
        version = versions[-1]
        if not re.fullmatch(r"[A-Za-z0-9_+.]+", version):
            raise Error(f"{name}: invalid Arch pkgver: {version}")
        return version


def update(name, check_only=False):
    directory = package_path(name)
    version = latest(name)
    if version is None:
        log(f"{name}: VCS policy; latest sources are built without committing pkgver churn")
        return
    current = fields(directory)["pkgver"][0]
    comparison = int(run("vercmp", version, current, capture=True))
    log(f"{name}: packaged={current} upstream={version}")
    if check_only or comparison == 0:
        return
    if comparison < 0:
        raise Error(f"{name}: upstream version is older; refusing automatic downgrade")
    with tempfile.TemporaryDirectory(prefix="aur-update-") as temp:
        work = Path(temp) / name
        shutil.copytree(directory, work)
        path = work / "PKGBUILD"
        text = path.read_text()
        for key, value in (("pkgver", version), ("pkgrel", "1")):
            text, count = re.subn(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
            if count != 1:
                raise Error(f"{name}: expected one top-level {key}= assignment")
        path.write_text(text)
        run("updpkgsums", cwd=work)
        (work / ".SRCINFO").write_text(srcinfo(work))
        # Source downloads and build outputs remain outside the managed recipe.
        for filename in ("PKGBUILD", ".SRCINFO"):
            shutil.copy2(work / filename, directory / filename)
    log(f"{name}: updated to {version}")


def dependencies(name):
    data = fields(package_path(name))
    skip = config(name).get("validation", {}).get("skip_runtime_dependencies", False)
    keys = ("makedepends", "checkdepends") if skip else ("depends", "makedepends", "checkdepends")
    values = set()
    for key, entries in data.items():
        if any(key == k or key == f"{k}_x86_64" for k in keys):
            for dep in entries:
                # Pacman itself validates package versions during makepkg.
                value = re.split(r"[<>=]", dep, 1)[0]
                valid_name(value)
                values.add(value)
    return sorted(values)


def validate(name, build=False):
    directory = package_path(name)
    before = fingerprint(name)
    run("bash", "-n", "PKGBUILD", cwd=directory)
    with tempfile.TemporaryDirectory(prefix="aur-validate-") as temp:
        work = Path(temp) / name
        shutil.copytree(directory, work)
        generated = srcinfo(work)
        if generated != (work / ".SRCINFO").read_text():
            raise Error(f"{name}: .SRCINFO differs; run makepkg --printsrcinfo > .SRCINFO")
        if fields(work).get("pkgbase") != [name]:
            raise Error(f"{name}: .SRCINFO pkgbase mismatch")
        run("makepkg", "--verifysource", "--noconfirm", cwd=work)
        run("namcap", "PKGBUILD", cwd=work)
        if build:
            flags = ["--force", "--noconfirm", "--cleanbuild"]
            if config(name).get("validation", {}).get("skip_runtime_dependencies"):
                log(f"{name}: runtime integration not tested: {config(name)['validation']['reason']}", "WARN")
                # Still enforce ALL build/check dependencies and their versions.
                data = fields(work)
                required = [v for k, vs in data.items() if k in {"makedepends", "checkdepends", "makedepends_x86_64", "checkdepends_x86_64"} for v in vs]
                if required:
                    run("pacman", "-T", *required)
                flags.append("--nodeps")
            run("makepkg", *flags, cwd=work)
            packages = list(work.glob("*.pkg.tar.*"))
            if not packages:
                raise Error(f"{name}: makepkg produced no package")
            for artifact in packages:
                run("namcap", str(artifact), cwd=work)
    if fingerprint(name) != before:
        raise Error(f"{name}: managed recipe changed during validation")
    log(f"{name}: validation passed (full_build={build})")


def require_receipt(name):
    receipt_path = ROOT / ".cache" / "validated.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if receipt.get(name) != fingerprint(name):
        raise Error(f"{name}: full-build validation receipt missing or stale")


def register(name, dry_run=False):
    """Explicit first publication; normal scheduled publishing never creates packages."""
    if not config(name)["package"].get("publish", False):
        raise Error(f"{name}: review the recipe and set package.publish = true first")
    require_receipt(name)
    state_path = ROOT / ".aur" / f"{name}.json"
    if state_path.exists():
        raise Error(f"{name}: already imported; use publish")
    with tempfile.TemporaryDirectory(prefix="aur-register-") as temp:
        repo = Path(temp) / name
        run("git", "clone", "--quiet", remote_url(name, write=True), str(repo))
        existing = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True)
        if existing.returncode == 0:
            raise Error(f"{name}: already exists on AUR; use add to import and reconcile it")
        if dry_run:
            log(f"{name}: would create AUR master from the validated recipe")
            return
        run("git", "checkout", "--orphan", "master", cwd=repo)
        copy_recipe(package_path(name), repo)
        run("git", "add", "--all", cwd=repo)
        run("git", "-c", "user.name=haturatu", "-c", "user.email=taro@eyes4you.org", "commit", "-m", f"feat: add {name}", cwd=repo)
        run("git", "push", "origin", "HEAD:master", cwd=repo)
        write_json(state_path, {"commit": run("git", "rev-parse", "HEAD", cwd=repo, capture=True), "files": snapshot(package_path(name))})
    log(f"{name}: registered on AUR; scheduled publishing is now available")


def publish(name, dry_run=False):
    if not config(name)["package"].get("publish", False):
        log(f"{name}: publishing disabled")
        return
    desired = snapshot(package_path(name))
    if not dry_run:
        require_receipt(name)
    with tempfile.TemporaryDirectory(prefix="aur-publish-") as temp:
        repo = Path(temp) / name
        clone(name, repo, write=not dry_run)
        actual = snapshot(repo)
        revision = run("git", "rev-parse", "HEAD", cwd=repo, capture=True)
        state_path = ROOT / ".aur" / f"{name}.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        if actual == desired:
            log(f"{name}: AUR already matches; no push")
        else:
            if state is None or actual != state["files"]:
                raise Error(f"{name}: AUR changed independently; import/reconcile it before publishing")
            changed = sorted(k for k in set(actual) | set(desired) if actual.get(k) != desired.get(k))
            log(f"{name}: {'would publish' if dry_run else 'publishing'} {', '.join(changed)}")
            if dry_run:
                return
            for rel in actual:
                if rel not in desired:
                    (repo / rel).unlink()
            copy_recipe(package_path(name), repo)
            run("git", "add", "--all", cwd=repo)
            run("git", "-c", "user.name=haturatu", "-c", "user.email=taro@eyes4you.org", "commit", "-m", f"chore: update {name}", cwd=repo)
            # A normal fast-forward push rejects races with other maintainers.
            run("git", "push", "origin", "HEAD:master", cwd=repo)
            revision = run("git", "rev-parse", "HEAD", cwd=repo, capture=True)
        if not dry_run:
            write_json(state_path, {"commit": revision, "files": desired})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "check", "update", "validate", "publish", "deps"):
        sub = commands.add_parser(command)
        sub.add_argument("packages", nargs="*")
        sub.add_argument("--all", action="store_true", help="all packages (also the default)")
        if command in {"list", "deps"}:
            sub.add_argument("--json", action="store_true")
        if command == "validate":
            sub.add_argument("--build", action="store_true")
        if command == "publish":
            sub.add_argument("--dry-run", action="store_true")
    sub = commands.add_parser("import")
    sub.add_argument("--maintainer", required=True)
    sub = commands.add_parser("add")
    sub.add_argument("name")
    sub.add_argument("--template", choices=("vcs", "github-release"))
    sub.add_argument("--upstream")
    sub = commands.add_parser("register")
    sub.add_argument("name")
    sub.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "import":
            for name in discover(args.maintainer):
                if package_path(name).exists():
                    log(f"{name}: already managed; skipped")
                else:
                    import_package(name)
            return
        if args.command == "add":
            if args.template:
                add_template(args.name, args.template, args.upstream)
            else:
                import_package(args.name)
            return
        if args.command == "register":
            names([args.name])
            register(args.name, args.dry_run)
            return
        selected = names(args.packages)
        if args.command == "list":
            print(json.dumps(selected) if args.json else "\n".join(selected))
            return
        if args.command == "deps":
            deps = sorted({dep for name in selected for dep in dependencies(name)})
            print(json.dumps(deps) if args.json else "\n".join(deps))
            return
        receipt = {}
        if args.command == "validate" and args.build:
            receipt_path = ROOT / ".cache" / "validated.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                for name in selected:
                    receipt.pop(name, None)
                write_json(receipt_path, receipt)
        for name in selected:
            if args.command in {"update", "check"}:
                update(name, check_only=args.command == "check")
            elif args.command == "validate":
                validate(name, args.build)
                if args.build:
                    receipt[name] = fingerprint(name)
            elif args.command == "publish":
                publish(name, args.dry_run)
        if args.command == "validate" and args.build:
            write_json(ROOT / ".cache" / "validated.json", receipt)
    except (Error, OSError, ValueError, subprocess.CalledProcessError) as exc:
        log(str(exc), "ERROR")
        sys.exit(1)
