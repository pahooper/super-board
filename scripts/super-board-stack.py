#!/usr/bin/env python3
"""super-board-stack.py — the project's stack: what it is built with and how it is tested.

Onboard step 4 (🧱 Stack), `/super-board stack`, the lane workers and lint all read the
stack through this script, so nobody re-derives "which test runner?" from prose.

    super-board-stack.py detect   [--root DIR]                    what this repo is built with (read-only)
    super-board-stack.py presets                                  starter stacks for a new project
    super-board-stack.py preset   <id> [--name N]                 one preset as a stack
    super-board-stack.py resolve  (--config C) [--root DIR] [--files F ...] [--base REF]
                                                                  the components a change touches: runners, checks, skills
    super-board-stack.py verify   (--config C | --stack F) [--root DIR]
                                                                  is every runner and skill installed? (read-only, runs no tests)
    super-board-stack.py apply    --config C --stack F [--qa-mode browser|tests-only] [--verify-commands keep|auto|replace] [--dry-run]
                                                                  write the stack into a config (atomic)
    super-board-stack.py ci       (--config C | --stack F)        print a GitHub Actions workflow for the stack
    super-board-stack.py scaffold --preset ID [--name N] [--root DIR] [--dry-run] [--no-verify] [--commit] [--resume]
                                                                  new project: generator → starter tests → CI → first commit

Every command prints one JSON object (`ci` prints YAML).

The stack lives in the config as `stack` (see config-schema.json):

    "stack": {"source": "detected" | "preset:<id>" | "custom",
              "components": [{"name", "path", "language", "framework", "package_manager",
                              "test": {"unit"|"integration"|"e2e": {"command", "skill"} | null},
                              "checks": ["<command>", …], "skills": ["<skill>", …]}]},
    "qa": {"mode": "browser" | "tests-only"}

Commands run from the repo root; a component in a subfolder carries `cd <path> && `.
`path` also routes files: a changed file belongs to the component with the longest
matching path ("." matches everything).

`scaffold` runs the preset's generator in a staging folder, copies the result in WITHOUT
overwriting anything that exists (README.md, AGENTS.md, .claude/ stay yours; .gitignore lines
are merged), adds the starter tests and CI, then runs the stack's tests and checks. With
--commit and everything green it makes the first commit. Generators need the network.

Exit: 0 ok · 1 verify/scaffold found a problem · 64 usage · 65 unknown preset · 66 config unreadable.
Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", "venv", "env", ".claude",
             ".agents", "vendor", ".next", ".nuxt", ".svelte-kit", "__pycache__", ".turbo", ".cache",
             "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "out", ".idea", ".vscode"}
# Files that do not make a folder "a project" — onboard and install create several of them.
NOT_SOURCE = {"readme", "license", "licence", ".gitignore", ".gitattributes", "agents.md", "claude.md",
              ".ds_store", "changelog", "changelog.md", ".editorconfig"}
SB_IGNORE = [".claude/super-board/active", ".claude/super-board/onboard-answers.json",
             ".claude/super-board/onboard-staged/", ".claude/super-board/backup/",
             ".claude/super-board/inflight/", ".claude/super-board/migrations/",
             ".claude/super-board/upgrade.json", ".claude/super-board/scaffold/",
             ".claude/super-board/cache/"]
# Build output each language leaves behind — generators run with `--vcs none` write no .gitignore.
LANG_IGNORE = {
    "rust": ["target/"],
    "python": ["__pycache__/", ".venv/", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/", "*.pyc"],
    "typescript": ["node_modules/", "dist/", ".svelte-kit/", "test-results/", "playwright-report/"],
    "javascript": ["node_modules/", "dist/", ".svelte-kit/", "test-results/", "playwright-report/"],
    "go": [],
}
WEB_FRAMEWORKS = {"sveltekit", "svelte", "next", "nuxt", "vue", "react", "astro", "angular", "solid", "remix"}
LAYERS = ("unit", "integration", "e2e")
INSTALL_HINTS = {
    "pnpm": "npm install -g pnpm   (or: corepack enable pnpm)",
    "yarn": "npm install -g yarn   (or: corepack enable yarn)",
    "bun": "curl -fsSL https://bun.sh/install | bash",
    "npm": "install Node.js: https://nodejs.org",
    "npx": "install Node.js: https://nodejs.org",
    "node": "install Node.js: https://nodejs.org",
    "cargo": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
    "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "go": "install Go: https://go.dev/dl/",
    "python": "install Python 3: https://www.python.org/downloads/",
    "python3": "install Python 3: https://www.python.org/downloads/",
    "poetry": "pipx install poetry",
    "deno": "curl -fsSL https://deno.land/install.sh | sh",
    "dotnet": "install .NET: https://dotnet.microsoft.com/download",
    "mvn": "install Maven: https://maven.apache.org/install.html",
    "bundle": "gem install bundler",
}
CARGO_SUBS = {"nextest": "cargo install cargo-nextest --locked", "clippy": "rustup component add clippy",
              "fmt": "rustup component add rustfmt"}


# ── small helpers ──────────────────────────────────────────────────────────────

def out(obj, code=0):
    print(json.dumps(obj, indent=1))
    return code


def flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def flag_list(argv, name):
    """Every value after `name` up to the next --flag."""
    if name not in argv:
        return []
    vals = []
    for a in argv[argv.index(name) + 1:]:
        if a.startswith("--"):
            break
        vals.append(a)
    return vals


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def catalog_dir():
    """Installed: .claude/bin/ → .claude/skills/…  Pack: scripts/ → skills/…"""
    for base in (os.environ.get("SUPER_BOARD_STACKS"),
                 os.path.join(HERE, "..", "skills", "super-board", "references", "stacks")):
        if base and os.path.isfile(os.path.join(base, "presets.json")):
            return os.path.abspath(base)
    raise RuntimeError("stack presets not found (skills/super-board/references/stacks/presets.json)")


def catalog():
    data = load_json(os.path.join(catalog_dir(), "presets.json"))
    if not isinstance(data, dict):
        raise RuntimeError("presets.json is not valid JSON")
    return data


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "app"


def walk(root, depth=3):
    """Yield (dirpath, filenames) to `depth`, skipping build and tool folders."""
    root = os.path.abspath(root)
    base = root.count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and (not d.startswith(".") or d in (".github", ".config")))
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        yield dirpath, filenames


def rel(root, path):
    r = os.path.relpath(path, root)
    return "." if r in (".", "") else r.replace(os.sep, "/")


def in_dir(path, cmd):
    return cmd if path in (".", "") else f"cd {path} && {cmd}"


# ── detection ──────────────────────────────────────────────────────────────────

def ci_commands(root):
    """`run:` commands from GitHub Actions workflows — the best evidence of real test commands."""
    cmds = []
    for wf in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml"))):
        lines = read(wf).splitlines()
        i = 0
        while i < len(lines):
            m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", lines[i])
            if m:
                indent, val = len(m.group(1)), m.group(2).strip()
                if val in ("|", ">", "|-", ">-", ""):
                    i += 1
                    while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                        if lines[i].strip() and not lines[i].strip().startswith("#"):
                            cmds.append(lines[i].strip())
                        i += 1
                    continue
                cmds.append(val.strip("'\""))
            i += 1
    return [c for c in cmds if c]


def node_component(root, d, pkg, ci):
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(pkg.get(key) or {})
    scripts = pkg.get("scripts") or {}
    path = rel(root, d)
    has = lambda f: os.path.exists(os.path.join(d, f))  # noqa: E731
    pm = "npm"
    pmfield = str(pkg.get("packageManager") or "")
    if has("pnpm-lock.yaml") or pmfield.startswith("pnpm") or os.path.exists(os.path.join(root, "pnpm-lock.yaml")):
        pm = "pnpm"
    elif has("yarn.lock") or pmfield.startswith("yarn"):
        pm = "yarn"
    elif has("bun.lock") or has("bun.lockb") or pmfield.startswith("bun"):
        pm = "bun"
    run = lambda s: f"npm run {s}" if pm == "npm" else f"{pm} run {s}"  # noqa: E731
    ex = lambda b: {"npm": f"npx {b}", "pnpm": f"pnpm exec {b}", "yarn": f"yarn {b}", "bun": f"bunx {b}"}[pm]  # noqa: E731

    framework = None
    for dep, fw in (("@sveltejs/kit", "sveltekit"), ("next", "next"), ("nuxt", "nuxt"), ("astro", "astro"),
                    ("@remix-run/react", "remix"), ("@angular/core", "angular"), ("svelte", "svelte"),
                    ("solid-js", "solid"), ("vue", "vue"), ("react", "react"),
                    ("hono", "node-server"), ("fastify", "node-server"), ("express", "node-server")):
        if dep in deps:
            framework = fw
            break
    tauri = any(k in deps for k in ("@tauri-apps/api", "@tauri-apps/cli"))
    language = "typescript" if ("typescript" in deps or has("tsconfig.json")) else "javascript"

    test = {"unit": None, "integration": None, "e2e": None}
    evidence = [f"{path}/package.json" if path != "." else "package.json"]
    if "vitest" in deps:
        test["unit"] = {"command": in_dir(path, ex("vitest run")), "skill": "vitest"}
    elif "jest" in deps:
        test["unit"] = {"command": in_dir(path, ex("jest")), "skill": None}
    elif "mocha" in deps:
        test["unit"] = {"command": in_dir(path, ex("mocha")), "skill": None}
    elif scripts.get("test") and "no test specified" not in scripts["test"]:
        test["unit"] = {"command": in_dir(path, run("test")), "skill": None}
    if "@playwright/test" in deps:
        test["e2e"] = {"command": in_dir(path, ex("playwright test")), "skill": "playwright-best-practices"}
    elif "cypress" in deps:
        test["e2e"] = {"command": in_dir(path, ex("cypress run")), "skill": None}
    elif "@wdio/cli" in deps or "webdriverio" in deps:
        conf = next((c for c in ("wdio.conf.ts", "wdio.conf.js", "wdio.conf.mjs") if has(c)), None)
        test["e2e"] = {"command": in_dir(path, ex(f"wdio run {conf}" if conf else "wdio run")), "skill": None}
    checks = [in_dir(path, run(s)) for s in ("check", "typecheck", "type-check", "lint") if s in scripts]
    if not any(s in scripts for s in ("check", "typecheck", "type-check")) and language == "typescript" and "typescript" in deps:
        checks.insert(0, in_dir(path, ex("tsc --noEmit")))
    return {"name": slug(pkg.get("name") or os.path.basename(d)) if path != "." else slug(pkg.get("name") or "app"),
            "path": path, "language": language, "framework": framework, "package_manager": pm,
            "test": test, "checks": checks, "skills": [], "_tauri": tauri, "_evidence": evidence}


def rust_component(root, d, text, ci):
    path = rel(root, d)
    manifest = "" if path == "." else f" --manifest-path {path}/Cargo.toml"
    workspace = re.search(r"^\[workspace\]", text, re.M) is not None
    nextest = (os.path.exists(os.path.join(d, ".config", "nextest.toml"))
               or os.path.exists(os.path.join(root, ".config", "nextest.toml"))
               or any("nextest" in c for c in ci))
    ws = " --workspace" if workspace else ""
    unit = f"cargo nextest run{manifest}{ws}" if nextest else f"cargo test{manifest}{ws}"
    tauri = re.search(r"^\s*tauri\s*=", text, re.M) is not None
    name = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.M)
    has_bin = os.path.exists(os.path.join(d, "src", "main.rs"))
    return {"name": slug(name.group(1) if name else os.path.basename(d)), "path": path, "language": "rust",
            "framework": "tauri" if tauri else ("workspace" if workspace else ("bin" if has_bin else "lib")),
            "package_manager": "cargo",
            "test": {"unit": {"command": unit, "skill": None}, "integration": None, "e2e": None},
            "checks": [f"cargo clippy{manifest}{ws} --all-targets -- -D warnings", f"cargo fmt{manifest} --check"],
            "skills": [], "_workspace": workspace, "_tauri": tauri,
            "_evidence": [f"{path}/Cargo.toml" if path != "." else "Cargo.toml"]}


def python_component(root, d, ci):
    path = rel(root, d)
    has = lambda f: os.path.exists(os.path.join(d, f))  # noqa: E731
    text = read(os.path.join(d, "pyproject.toml")) + read(os.path.join(d, "requirements.txt")) \
        + read(os.path.join(d, "requirements-dev.txt")) + read(os.path.join(d, "setup.cfg"))
    if has("uv.lock") or "[tool.uv" in text:
        pm, pre = "uv", "uv run "
    elif has("poetry.lock") or "[tool.poetry" in text:
        pm, pre = "poetry", "poetry run "
    else:
        pm, pre = "pip", "python -m "
    low = text.lower()
    pytest = "pytest" in low or has("pytest.ini") or has("conftest.py") or \
        bool(glob.glob(os.path.join(d, "tests", "test_*.py")))
    unit = f"{pre}pytest -q" if pytest else ("python -m unittest" if pm == "pip" else f"{pre}python -m unittest")
    checks = []
    if "ruff" in low or has("ruff.toml") or has(".ruff.toml"):
        checks.append(f"{pre}ruff check .")
    if "mypy" in low:
        checks.append(f"{pre}mypy .")
    framework = next((fw for fw in ("django", "fastapi", "flask") if fw in low), None)
    return {"name": slug(os.path.basename(os.path.abspath(d))), "path": path, "language": "python",
            "framework": framework, "package_manager": pm,
            "test": {"unit": {"command": in_dir(path, unit), "skill": None}, "integration": None, "e2e": None},
            "checks": [in_dir(path, c) for c in checks], "skills": [],
            "_evidence": [f for f in ("pyproject.toml", "requirements.txt", "setup.cfg") if has(f)]}


def go_component(root, d):
    path = rel(root, d)
    checks = ["go vet ./..."]
    if glob.glob(os.path.join(d, ".golangci.*")):
        checks.append("golangci-lint run")
    mod = re.search(r"^module\s+(\S+)", read(os.path.join(d, "go.mod")), re.M)
    return {"name": slug((mod.group(1) if mod else os.path.basename(d)).split("/")[-1]), "path": path,
            "language": "go", "framework": None, "package_manager": "go",
            "test": {"unit": {"command": in_dir(path, "go test ./..."), "skill": None}, "integration": None, "e2e": None},
            "checks": [in_dir(path, c) for c in checks], "skills": [], "_evidence": ["go.mod"]}


def other_component(root, d, kind):
    path = rel(root, d)
    table = {
        "maven": ("java", "maven", "mvn -q test", []),
        "gradle": ("java", "gradle", "./gradlew test", []),
        "dotnet": ("csharp", "dotnet", "dotnet test", ["dotnet format --verify-no-changes"]),
        "ruby": ("ruby", "bundler", "bundle exec rspec" if os.path.isdir(os.path.join(d, "spec")) else "bundle exec rake test", []),
        "deno": ("typescript", "deno", "deno test", ["deno lint", "deno check ."]),
        "elixir": ("elixir", "mix", "mix test", ["mix format --check-formatted"]),
        "php": ("php", "composer", "vendor/bin/phpunit", []),
    }
    lang, pm, unit, checks = table[kind]
    return {"name": slug(os.path.basename(os.path.abspath(d))), "path": path, "language": lang, "framework": None,
            "package_manager": pm,
            "test": {"unit": {"command": in_dir(path, unit), "skill": None}, "integration": None, "e2e": None},
            "checks": [in_dir(path, c) for c in checks], "skills": [], "_evidence": [kind]}


def is_new_project(root):
    """No source files: only the files onboard, install and git create."""
    for dirpath, filenames in walk(root, depth=4):
        for f in filenames:
            if f.lower() in NOT_SOURCE or f.lower().startswith(("readme", "license", "licence")):
                continue
            if rel(root, dirpath).startswith(("docs/super-board", "docs/agents")):
                continue
            return False
    return True


def detect(root):
    root = os.path.abspath(root)
    ci = ci_commands(root)
    comps, workspace_dirs, node_ws = [], [], False
    root_pkg = load_json(os.path.join(root, "package.json")) or {}
    if isinstance(root_pkg, dict) and (root_pkg.get("workspaces") or os.path.exists(os.path.join(root, "pnpm-workspace.yaml"))):
        node_ws = True
    for dirpath, filenames in walk(root):
        names = set(filenames)
        is_root = os.path.abspath(dirpath) == root
        if "package.json" in names and (is_root or not node_ws):
            pkg = load_json(os.path.join(dirpath, "package.json"))
            if isinstance(pkg, dict):
                comps.append(node_component(root, dirpath, pkg, ci))
        if "Cargo.toml" in names and not any(dirpath.startswith(w + os.sep) for w in workspace_dirs):
            text = read(os.path.join(dirpath, "Cargo.toml"))
            c = rust_component(root, dirpath, text, ci)
            comps.append(c)
            if c["_workspace"]:
                workspace_dirs.append(os.path.abspath(dirpath))
        if names & {"pyproject.toml", "setup.py", "requirements.txt"} and not any(c["path"] == rel(root, dirpath) and c["language"] == "python" for c in comps):
            comps.append(python_component(root, dirpath, ci))
        if "go.mod" in names:
            comps.append(go_component(root, dirpath))
        for marker, kind in (("pom.xml", "maven"), ("build.gradle", "gradle"), ("build.gradle.kts", "gradle"),
                             ("Gemfile", "ruby"), ("deno.json", "deno"), ("mix.exs", "elixir"), ("composer.json", "php")):
            if marker in names:
                comps.append(other_component(root, dirpath, kind))
                break
        if any(n.endswith((".csproj", ".sln")) for n in names) and is_root:
            comps.append(other_component(root, dirpath, "dotnet"))

    # A Tauri front end with no unit runner still has one obvious place to test: the Rust core.
    tauri = any(c.get("_tauri") for c in comps)
    for c in comps:
        for k in ("_tauri", "_workspace"):
            c.pop(k, None)
    evidence = sorted({e for c in comps for e in c.pop("_evidence", [])})
    names_seen = set()
    for c in sorted(comps, key=lambda c: (c["path"] != ".", c["path"])):  # unique, readable names
        base, n = c["name"], 2
        if base in names_seen:  # e.g. a Tauri app: package.json and src-tauri/Cargo.toml share a name
            c["name"] = f"{base}-{(c['path'].split('/')[-1] if c['path'] != '.' else c['language'])}"
        while c["name"] in names_seen:
            c["name"] = f"{base}-{n}"
            n += 1
        names_seen.add(c["name"])
    web = any(c.get("framework") in WEB_FRAMEWORKS for c in comps)
    qa_mode = "browser" if web and not tauri else "tests-only"
    missing = [f"{c['name']}: no unit test runner found" for c in comps if not (c["test"].get("unit"))]
    new = not comps and is_new_project(root)
    stack = {"source": "detected", "components": sorted(comps, key=lambda c: (c["path"] != ".", c["path"]))}
    return {"new_project": new, "recognised": bool(comps), "stack": stack, "qa_mode": qa_mode,
            "desktop": tauri, "evidence": evidence, "ci_commands": [c for c in ci if re.search(
                r"\b(test|lint|check|clippy|vet|fmt|tsc|pytest|ruff|mypy|nextest|playwright|vitest|jest)\b", c)][:20],
            "gaps": missing}


# ── presets ────────────────────────────────────────────────────────────────────

def preset(pid, name=None):
    cat = catalog()
    p = next((x for x in cat["presets"] if x["id"] == pid), None)
    if not p:
        return None
    stack = {"source": f"preset:{pid}", "components": json.loads(json.dumps(p["components"]))}
    return {"id": pid, "label": p["label"], "description": p["description"], "generator": p.get("generator"),
            "qa_mode": p.get("qa_mode", "tests-only"), "stack": stack, "notes": p.get("notes"),
            "name": slug(name) if name else None}


def presets():
    cat = catalog()
    by_id = {p["id"]: p for p in cat["presets"]}
    return {"categories": [{"id": c["id"], "label": c["label"], "description": c["description"],
                            "presets": [{"id": i, "label": by_id[i]["label"], "description": by_id[i]["description"]}
                                        for i in c["presets"] if i in by_id]}
                           for c in cat["categories"]],
            "runners": {k: v for k, v in cat.get("runners", {}).items() if not k.startswith("_")}}


# ── config helpers ─────────────────────────────────────────────────────────────

def load_stack(argv, root):
    """(stack, qa_mode, config, source-of-stack) from --stack FILE or --config FILE."""
    sp, cp = flag(argv, "--stack"), flag(argv, "--config")
    if sp:
        data = load_json(sp)
        if not isinstance(data, dict):
            raise ValueError(f"--stack {sp} is not a JSON object")
        st = data.get("stack", data) if "components" not in data else data
        return st, data.get("qa_mode") or (data.get("qa") or {}).get("mode"), None, "file"
    if cp:
        cfg = load_json(cp)
        if not isinstance(cfg, dict):
            raise FileNotFoundError(f"config {cp} unreadable")
        if isinstance(cfg.get("stack"), dict) and cfg["stack"].get("components"):
            return cfg["stack"], (cfg.get("qa") or {}).get("mode", "browser"), cfg, "config"
        d = detect(root)
        return d["stack"], d["qa_mode"], cfg, "detected-at-runtime"
    raise ValueError("need --config or --stack")


def root_for(argv, config_path=None):
    r = flag(argv, "--root")
    if r:
        return os.path.abspath(r)
    if config_path:
        d = os.path.dirname(os.path.abspath(config_path))
        suffix = os.path.join(".claude", "super-board", "configs")
        if d.endswith(os.sep + suffix):
            return d[: -len(suffix)].rstrip(os.sep)
    return os.getcwd()


def components_for(stack, files):
    comps = sorted(stack.get("components") or [], key=lambda c: -len(c.get("path") or "."))
    if not files:
        return [dict(c, files=[]) for c in stack.get("components") or []]
    groups = {}
    for f in files:
        f = f.lstrip("./") if f.startswith("./") else f
        for c in comps:
            p = (c.get("path") or ".").rstrip("/")
            if p in (".", "") or f == p or f.startswith(p + "/"):
                groups.setdefault(c["name"], []).append(f)
                break
    return [dict(c, files=groups[c["name"]]) for c in stack.get("components") or [] if c["name"] in groups]


def changed_files(root, base):
    for ref in (f"origin/{base}", base):
        r = subprocess.run(["git", "-C", root, "diff", "--name-only", f"{ref}...HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            files = [x for x in r.stdout.splitlines() if x.strip()]
            r2 = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True)
            files += [ln[3:].strip() for ln in r2.stdout.splitlines() if ln.strip()]
            return sorted(set(files))
    return []


def skills_of(comp):
    s = [comp["test"][k]["skill"] for k in LAYERS if (comp.get("test") or {}).get(k) and comp["test"][k].get("skill")]
    return list(dict.fromkeys(s + list(comp.get("skills") or [])))


def resolve(argv):
    cp = flag(argv, "--config")
    root = root_for(argv, cp)
    stack, qa, cfg, src = load_stack(argv, root)
    files = flag_list(argv, "--files")
    if not files and "--files" not in argv:
        base = flag(argv, "--base") or ((cfg or {}).get("base_branch") or "main")
        files = changed_files(root, base)
    comps = components_for(stack, files)
    res = {"source": src, "qa_mode": qa or "browser", "files": len(files),
           "components": [{"name": c["name"], "path": c.get("path", "."), "language": c.get("language"),
                           "framework": c.get("framework"),
                           "unit": ((c.get("test") or {}).get("unit") or {}).get("command"),
                           "integration": ((c.get("test") or {}).get("integration") or {}).get("command"),
                           "e2e": ((c.get("test") or {}).get("e2e") or {}).get("command"),
                           "checks": c.get("checks") or [], "skills": skills_of(c), "files": c.get("files", [])}
                          for c in comps],
           "always_skills": ["mattpocock-skills:tdd", "verification-before-completion"]}
    if src == "detected-at-runtime":
        res["warning"] = "config has no stack — detected now; run /super-board stack to save it"
    if files and not comps:
        res["warning"] = "no component matches the changed files; run every component's tests"
        res["components"] = resolve_all(stack)
    return res


def resolve_all(stack):
    return [{"name": c["name"], "path": c.get("path", "."), "language": c.get("language"),
             "unit": ((c.get("test") or {}).get("unit") or {}).get("command"),
             "integration": ((c.get("test") or {}).get("integration") or {}).get("command"),
             "e2e": ((c.get("test") or {}).get("e2e") or {}).get("command"),
             "checks": c.get("checks") or [], "skills": skills_of(c), "files": []}
            for c in stack.get("components") or []]


# ── verify (read-only: runs version probes, never tests) ───────────────────────

def installed_skill(root, name):
    short = name.split(":")[-1]
    for base in (os.path.join(root, ".claude", "skills"), os.path.join(root, ".agents", "skills"),
                 os.path.expanduser("~/.claude/skills"), os.path.expanduser("~/.agents/skills")):
        if os.path.isfile(os.path.join(base, short, "SKILL.md")):
            return True
    for lock in (os.path.join(root, "skills-lock.json"), os.path.expanduser("~/.agents/.skill-lock.json")):
        if f'"{short}"' in read(lock):
            return True
    return False


def probe(root, command):
    """(status, detail, fix) for one command, without running it."""
    cwd, cmd = root, command.strip()
    m = re.match(r"^cd\s+(\S+)\s*&&\s*(.*)$", cmd)
    if m:
        cwd, cmd = os.path.join(root, m.group(1)), m.group(2)
    try:
        tok = shlex.split(cmd)
    except ValueError:
        return "unknown", "cannot parse command", None
    if not tok:
        return "unknown", "empty command", None
    tool = tok[0]
    if tool.startswith("./"):
        return ("ok", f"{tool} present", None) if os.path.exists(os.path.join(cwd, tool)) else \
            ("missing", f"{tool} not found", None)
    if not shutil.which(tool):
        return "missing", f"{tool} is not installed", INSTALL_HINTS.get(tool)
    if tool in ("npm", "pnpm", "yarn", "bun") and len(tok) >= 3 and tok[1] == "run":
        pkg = load_json(os.path.join(cwd, "package.json")) or {}
        if tok[2] not in (pkg.get("scripts") or {}):
            return "missing", f"package.json has no \"{tok[2]}\" script", None
        return "ok", f"{tool} run {tok[2]}", None
    exe = None
    if tool == "npx" and len(tok) > 1:
        exe = tok[1]
    elif tool in ("pnpm", "bun") and len(tok) > 2 and tok[1] == "exec":
        exe = tok[2]
    elif tool == "bunx" and len(tok) > 1:
        exe = tok[1]
    elif tool == "yarn" and len(tok) > 1 and tok[1] not in ("run", "install", "add"):
        exe = tok[1]
    if exe:
        d = os.path.abspath(cwd)
        while True:
            if os.path.exists(os.path.join(d, "node_modules", ".bin", exe)):
                return "ok", f"{exe} installed", None
            if d == os.path.abspath(root) or os.path.dirname(d) == d:
                break
            d = os.path.dirname(d)
        pm = "pnpm" if tool in ("pnpm",) else ("bun" if tool in ("bun", "bunx") else ("yarn" if tool == "yarn" else "npm"))
        return "missing", f"{exe} not in node_modules", f"{pm} install" if os.path.exists(os.path.join(cwd, "package.json")) else None
    if tool == "cargo" and len(tok) > 1 and tok[1] in CARGO_SUBS:
        r = subprocess.run(["cargo", tok[1], "--version"], capture_output=True, text=True)
        return ("ok", f"cargo {tok[1]}", None) if r.returncode == 0 else \
            ("missing", f"cargo {tok[1]} is not installed", CARGO_SUBS[tok[1]])
    if tool == "uv" and len(tok) > 2 and tok[1] == "run" and tok[2] not in ("python", "python3"):
        exe = tok[2]
        if os.path.exists(os.path.join(cwd, ".venv", "bin", exe)) or os.path.exists(os.path.join(cwd, ".venv", "Scripts", exe + ".exe")):
            return "ok", f"{exe} in .venv", None
        return "unknown", f"{exe} not in .venv yet", f"uv sync   (or: uv add --dev {exe})"
    return "ok", f"{tool} installed", None


def verify(argv):
    cp = flag(argv, "--config")
    root = root_for(argv, cp)
    stack, qa, _cfg, src = load_stack(argv, root)
    cat = catalog()
    rows, skills = [], {}
    for c in stack.get("components") or []:
        cmds = [(k, c["test"][k]["command"]) for k in LAYERS if (c.get("test") or {}).get(k)]
        cmds += [("check", x) for x in c.get("checks") or []]
        for layer, cmd in cmds:
            status, detail, fix = probe(root, cmd)
            rows.append({"component": c["name"], "layer": layer, "command": cmd, "status": status,
                         "detail": detail, "fix": fix})
        for s in skills_of(c):
            skills.setdefault(s, {"skill": s, "installed": installed_skill(root, s),
                                  "install": ((cat.get("skills") or {}).get(s) or {}).get("install")})
    missing = [r for r in rows if r["status"] == "missing"]
    res = {"source": src, "qa_mode": qa, "ok": not missing, "commands": rows, "skills": list(skills.values()),
           "missing": len(missing), "unknown": sum(r["status"] == "unknown" for r in rows)}
    if qa == "browser" and not any((c.get("test") or {}).get("e2e") for c in stack.get("components") or []):
        res["note"] = "qa mode is browser but no component has an e2e runner — Playwright is expected"
    return res


# ── apply (write into a config) ────────────────────────────────────────────────

def derived_verify(stack):
    cmds = []
    for c in stack.get("components") or []:
        cmds += list(c.get("checks") or [])
        for k in ("unit", "integration"):
            t = (c.get("test") or {}).get(k)
            if t and t.get("command"):
                cmds.append(t["command"])
    return list(dict.fromkeys(cmds))


def validate_stack(stack):
    errs = []
    if not isinstance(stack, dict) or not isinstance(stack.get("components"), list) or not stack["components"]:
        return ["stack needs a non-empty components list"]
    seen = set()
    for i, c in enumerate(stack["components"]):
        where = f"components[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{where} is not an object")
            continue
        for k in ("name", "path", "language"):
            if not c.get(k):
                errs.append(f"{where}.{k} is required")
        if c.get("name") in seen:
            errs.append(f"{where}.name '{c.get('name')}' repeats")
        seen.add(c.get("name"))
        t = c.get("test")
        if not isinstance(t, dict):
            errs.append(f"{where}.test is required ({{unit, integration, e2e}})")
            continue
        for k, v in t.items():
            if k not in LAYERS:
                errs.append(f"{where}.test.{k}: layers are unit, integration, e2e")
            elif v is not None and not (isinstance(v, dict) and v.get("command")):
                errs.append(f"{where}.test.{k} needs a command (or null)")
        if not isinstance(c.get("checks", []), list):
            errs.append(f"{where}.checks must be a list")
    return errs


def apply(argv):
    cp, sp = flag(argv, "--config"), flag(argv, "--stack")
    if not cp or not sp:
        raise ValueError("apply needs --config and --stack")
    cfg = load_json(cp)
    if not isinstance(cfg, dict):
        raise FileNotFoundError(f"config {cp} unreadable")
    data = load_json(sp)
    if not isinstance(data, dict):
        raise ValueError(f"--stack {sp} is not a JSON object")
    stack = data.get("stack", data) if "components" not in data else data
    stack = {k: v for k, v in stack.items() if not k.startswith("_")}
    errs = validate_stack(stack)
    if errs:
        return {"error": "invalid stack", "problems": errs}, 1
    mode = flag(argv, "--qa-mode") or data.get("qa_mode") or (cfg.get("qa") or {}).get("mode")
    if mode not in (None, "browser", "tests-only"):
        raise ValueError("--qa-mode is browser or tests-only")
    vc = flag(argv, "--verify-commands", "auto")
    changes = []
    new = dict(cfg)
    if cfg.get("stack") != stack:
        new["stack"] = stack
        changes.append("stack")
    if mode and (cfg.get("qa") or {}).get("mode") != mode:
        new["qa"] = dict(cfg.get("qa") or {}, mode=mode)
        changes.append(f"qa.mode={mode}")
    derived = derived_verify(stack)
    if vc == "replace" or (vc == "auto" and not cfg.get("verify_commands")):
        if derived and cfg.get("verify_commands") != derived:
            new["verify_commands"] = derived
            changes.append("verify_commands")
    dry = "--dry-run" in argv
    if changes and not dry:
        write_json(cp, new)
    return {"config": cp, "changes": changes, "written": bool(changes and not dry),
            "verify_commands": new.get("verify_commands", [])}, 0


# ── CI workflow ────────────────────────────────────────────────────────────────

def ci_yaml(stack):
    comps = stack.get("components") or []
    langs = {c.get("language") for c in comps}
    pms = {c.get("package_manager") for c in comps}
    tauri = any(c.get("framework") == "tauri" for c in comps)
    steps = ["      - uses: actions/checkout@v4"]
    if tauri:
        steps += ["      - name: Tauri system libraries",
                  "        run: sudo apt-get update && sudo apt-get install -y libwebkit2gtk-4.1-dev "
                  "build-essential libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev"]
    if langs & {"typescript", "javascript"}:
        if "pnpm" in pms:
            steps += ["      - uses: pnpm/action-setup@v4", "        with:", "          version: 10"]
        steps += ["      - uses: actions/setup-node@v4", "        with:", "          node-version: 22"]
        if "pnpm" in pms:
            steps += ["          cache: pnpm"]
    if "rust" in langs:
        steps += ["      - uses: dtolnay/rust-toolchain@stable", "        with:", "          components: clippy, rustfmt"]
    if "python" in langs:
        steps += ["      - uses: astral-sh/setup-uv@v6"]
    if "go" in langs:
        steps += ["      - uses: actions/setup-go@v5", "        with:", "          go-version: stable"]
    for c in comps:
        pm, path = c.get("package_manager"), c.get("path") or "."
        install = {"pnpm": "pnpm install --frozen-lockfile", "npm": "npm ci", "yarn": "yarn install --frozen-lockfile",
                   "bun": "bun install --frozen-lockfile", "uv": "uv sync"}.get(pm)
        if install and not any(s.endswith(in_dir(path, install)) for s in steps):
            steps += [f"      - name: Install ({c['name']})", f"        run: {in_dir(path, install)}"]
    for c in comps:
        for cmd in list(c.get("checks") or []) + [((c.get("test") or {}).get(k) or {}).get("command") for k in ("unit", "integration")]:
            if cmd:
                steps += [f"      - name: {c['name']}: {cmd.split('&& ')[-1][:60]}", f"        run: {cmd}"]
    return "\n".join([
        "# Written by super-board's scaffold from the project's stack (config `stack`).",
        "# The merge gate runs the same commands locally (verify_commands).",
        "name: CI",
        "on:",
        "  push:",
        "    branches: [main, master, staging]",
        "  pull_request:",
        "jobs:",
        "  checks:",
        "    runs-on: ubuntu-latest",
        "    steps:",
    ] + steps) + "\n"


# ── scaffold ───────────────────────────────────────────────────────────────────

def merge_tree(src, dest):
    """Copy src into dest; never overwrite. .gitignore lines are merged. Returns (copied, skipped)."""
    copied, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            s = os.path.join(dirpath, f)
            r = os.path.relpath(s, src)
            d = os.path.join(dest, r)
            if f == ".gitignore" and os.path.exists(d):
                have = read(d).splitlines()
                add = [ln for ln in read(s).splitlines() if ln.strip() and ln not in have]
                if add:
                    with open(d, "a", encoding="utf-8") as fh:
                        fh.write(("" if read(d).endswith("\n") or not read(d) else "\n") + "\n".join(add) + "\n")
                    copied.append(r.replace(os.sep, "/") + " (lines merged)")
                continue
            if os.path.exists(d):
                skipped.append(r.replace(os.sep, "/"))
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            copied.append(r.replace(os.sep, "/"))
    return copied, skipped


def write_templates(pid, dest, subs):
    base = os.path.join(catalog_dir(), "templates", pid)
    copied, skipped = [], []
    if not os.path.isdir(base):
        return copied, skipped
    for dirpath, _d, filenames in os.walk(base):
        for f in filenames:
            s = os.path.join(dirpath, f)
            r = os.path.relpath(s, base)
            d = os.path.join(dest, r)
            if os.path.exists(d):
                skipped.append(r.replace(os.sep, "/"))
                continue
            text = read(s)
            for k, v in subs.items():
                text = text.replace("{{" + k + "}}", v)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(d, "w", encoding="utf-8") as fh:
                fh.write(text)
            copied.append(r.replace(os.sep, "/"))
    return copied, skipped


def add_scripts(dest, scripts):
    path = os.path.join(dest, "package.json")
    pkg = load_json(path)
    if not isinstance(pkg, dict):
        return []
    have = pkg.setdefault("scripts", {})
    added = [k for k in scripts if k not in have]
    for k in added:
        have[k] = scripts[k]
    if added:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(pkg, fh, indent="\t" if "\t" in read(path)[:200] else 2)
            fh.write("\n")
        os.replace(tmp, path)
    return added


def ensure_gitignore(dest, stack=None):
    path = os.path.join(dest, ".gitignore")
    have = read(path).splitlines()
    want = list(SB_IGNORE)
    for c in (stack or {}).get("components") or []:
        prefix = "" if (c.get("path") or ".") == "." else c["path"].rstrip("/") + "/"
        want += [(ln if ln.startswith("*") else prefix + ln) for ln in LANG_IGNORE.get(c.get("language"), [])]
    add = [ln for ln in dict.fromkeys(want) if ln not in have]
    if add:
        with open(path, "a", encoding="utf-8") as fh:
            body = read(path)
            fh.write(("" if not body or body.endswith("\n") else "\n") + "\n".join(add) + "\n")
    return add


def run_cmd(argv, cwd, log, timeout=900):
    log.append({"run": " ".join(shlex.quote(a) for a in argv), "cwd": cwd})
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, CI="1"))
    except FileNotFoundError:
        log[-1].update(rc=127, tail=f"{argv[0]}: not installed")
        return 127
    except subprocess.TimeoutExpired:
        log[-1].update(rc=124, tail="timed out")
        return 124
    tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
    log[-1].update(rc=r.returncode, tail="\n".join(tail))
    return r.returncode


def scaffold(argv):
    pid = flag(argv, "--preset")
    root = os.path.abspath(flag(argv, "--root", os.getcwd()))
    name = slug(flag(argv, "--name") or os.path.basename(root))
    p = next((x for x in catalog()["presets"] if x["id"] == pid), None) if pid else None
    if not p:
        return {"error": f"unknown preset {pid!r}", "presets": [x["id"] for x in catalog()["presets"]]}, 65
    subs = {"name": name, "module": flag(argv, "--module") or f"example.com/{name}"}
    stack = {"source": f"preset:{pid}", "components": json.loads(json.dumps(p["components"]))}
    fill = lambda a: [x.replace("{name}", subs["name"]).replace("{module}", subs["module"]) for x in a]  # noqa: E731
    plan = []
    for op in p["scaffold"]:
        if "run" in op:
            plan.append({"run": " ".join(fill(op["run"])), "cwd": op.get("cwd", "stage")})
        elif "merge" in op:
            plan.append({"merge": op["merge"].replace("{name}", name)})
        else:
            plan.append({k: v for k, v in op.items()})
    if "--dry-run" in argv:
        return {"preset": pid, "name": name, "root": root, "plan": plan, "stack": stack,
                "new_project": is_new_project(root)}, 0
    resume = "--resume" in argv
    if resume:  # files are already in: re-run the tests and checks, then commit if green
        checks = []
        ok = all([run_cmd(["bash", "-c", c], root, checks) == 0 for c in derived_verify(stack)])
        res = {"preset": pid, "name": name, "ok": ok, "resumed": True, "stack": stack,
               "qa_mode": p.get("qa_mode", "tests-only"), "verify": checks,
               "committed": commit(root, p, stack) if ok and "--commit" in argv else None}
        if not ok:
            res["needs_system"] = system_libs_missing(checks, p)
        return res, 0 if ok else 1
    if not is_new_project(root) and "--force" not in argv:
        return {"error": "this folder already has source files — scaffold is for new projects (pass --force to add a starter anyway; --resume re-runs the checks of a starter already added)"}, 1
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stage = os.path.join(root, ".claude", "super-board", "scaffold", stamp)
    os.makedirs(stage, exist_ok=True)
    log, copied, skipped, added_scripts = [], [], [], []
    ignored = ensure_gitignore(root, stack)
    ok = True
    for op in p["scaffold"]:
        if "run" in op:
            cwd = root if op.get("cwd") == "project" else stage
            if run_cmd(fill(op["run"]), cwd, log) != 0:
                ok = False
                break
        elif "merge" in op:
            src = os.path.join(stage, op["merge"].replace("{name}", name))
            if not os.path.isdir(src):
                log.append({"merge": op["merge"], "rc": 1, "tail": f"generator output not found: {src}"})
                ok = False
                break
            c, s = merge_tree(src, root)
            copied += c
            skipped += s
        elif "scripts" in op:
            added_scripts += add_scripts(root, op["scripts"])
        elif op.get("templates"):
            c, s = write_templates(pid, root, subs)
            copied += c
            skipped += s
        elif op.get("ci"):
            path = os.path.join(root, ".github", "workflows", "ci.yml")
            if os.path.exists(path):
                skipped.append(".github/workflows/ci.yml")
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(ci_yaml(stack))
                copied.append(".github/workflows/ci.yml")
    checks = []
    if ok and "--no-verify" not in argv:
        for cmd in derived_verify(stack):
            rc = run_cmd(["bash", "-c", cmd], root, checks)
            ok = ok and rc == 0
    if ok:
        shutil.rmtree(stage, ignore_errors=True)
    committed = None
    if ok and "--commit" in argv:
        committed = commit(root, p, stack)
    res = {"preset": pid, "name": name, "ok": ok, "stack": stack, "qa_mode": p.get("qa_mode", "tests-only"),
           "steps": log, "verify": checks, "copied": len(copied), "kept_yours": skipped,
           "scripts_added": added_scripts, "gitignore_added": ignored, "committed": committed,
           "notes": p.get("notes")}
    if not ok:
        res["stage"] = os.path.relpath(stage, root) if os.path.isdir(stage) else None
        res["needs_system"] = system_libs_missing(log + checks, p)
        res["next"] = ("install what needs_system names, then: super-board-stack.py scaffold --preset "
                       f"{pid} --resume --commit" if res["needs_system"] else
                       "read the failing step's tail; fix the starter, then re-run with --resume --commit")
    return res, 0 if ok else 1


def system_libs_missing(steps, p):
    """A failure that is the machine, not the code: a system library or toolchain is missing."""
    pat = re.compile(r"system library .* (was not found|required)|pkg-config|\.pc` needs to be installed|"
                     r"not installed|command not found|linker .* not found", re.I)
    hits = [s for s in steps if s.get("rc") not in (0, None) and pat.search(s.get("tail") or "")]
    if not hits:
        return None
    return {"failed": [h["run"] for h in hits], "why": (hits[0].get("tail") or "").strip().splitlines()[0][:200],
            "fix": p.get("notes") or "install the missing system tool or library, then re-run with --resume"}


def commit(root, p, stack):
    if subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
        subprocess.run(["git", "-C", root, "init", "-q"], check=False)
    subprocess.run(["git", "-C", root, "add", "-A"], check=False)
    units = [((c.get("test") or {}).get("unit") or {}).get("command") for c in stack["components"]]
    msg = "\n".join([f"🔧 [chore] scaffold: {p['label']} starter project", "",
                     f"- generated with {p.get('generator') or p['id']}",
                     "- starter tests: " + " · ".join(u for u in units if u),
                     "- CI runs the same checks and tests as the merge gate"])
    r = subprocess.run(["git", "-C", root, "commit", "-q", "-m", msg], capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[-300:]}
    sha = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "-C", root, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    return {"ok": True, "sha": sha, "branch": branch}


# ── main ───────────────────────────────────────────────────────────────────────

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 64
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "detect":
            return out(detect(flag(rest, "--root", os.getcwd())))
        if cmd == "presets":
            return out(presets())
        if cmd == "preset":
            pid = rest[0] if rest and not rest[0].startswith("-") else None
            res = preset(pid, flag(rest, "--name")) if pid else None
            if not res:
                return out({"error": f"unknown preset {pid!r}", "presets": [p["id"] for p in catalog()["presets"]]}, 65)
            return out(res)
        if cmd == "resolve":
            return out(resolve(rest))
        if cmd == "verify":
            res = verify(rest)
            return out(res, 0 if res["ok"] else 1)
        if cmd == "apply":
            res, code = apply(rest)
            return out(res, code)
        if cmd == "ci":
            root = root_for(rest, flag(rest, "--config"))
            stack, _qa, _c, _s = load_stack(rest, root)
            sys.stdout.write(ci_yaml(stack))
            return 0
        if cmd == "scaffold":
            res, code = scaffold(rest)
            return out(res, code)
    except FileNotFoundError as e:
        return out({"error": str(e)}, 66)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as e:
        return out({"error": str(e)}, 64)
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
