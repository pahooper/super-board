#!/usr/bin/env bash
# Tests scripts/super-board-stack.py — stack detection, presets, resolve (file → component
# routing), verify (read-only probes), apply (config writes) and the scaffold flow.
# Offline: fixtures are written here; the scaffold case uses a stub catalog
# (SUPER_BOARD_STACKS) whose "generator" is a shell command, so no network is touched.
#
#   bash tests/test-stack.sh
set -euo pipefail
cd "$(dirname "$0")/.."
S="$PWD/scripts/super-board-stack.py"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }
q() { jq -e "$@" >/dev/null; }
n=0
pass() { n=$((n + 1)); }

# 1 — a Tauri app: root package.json (pnpm, vitest, svelte-kit, @tauri-apps) + src-tauri/Cargo.toml.
T="$WORK/tauri"; mkdir -p "$T/src-tauri/src" "$T/src/lib"
cat > "$T/package.json" <<'JSON'
{"name":"ledger","scripts":{"check":"svelte-check","test":"vitest"},
 "devDependencies":{"vitest":"^3","@sveltejs/kit":"^2","svelte":"^5","typescript":"^5","@tauri-apps/cli":"^2"}}
JSON
: > "$T/pnpm-lock.yaml"
printf '[package]\nname = "ledger"\n\n[dependencies]\ntauri = { version = "2" }\n' > "$T/src-tauri/Cargo.toml"
: > "$T/src-tauri/src/main.rs"
OUT=$(python3 "$S" detect --root "$T")
echo "$OUT" | q '.new_project == false and .desktop == true and .qa_mode == "tests-only"' || fail "tauri: desktop, tests-only: $OUT"
echo "$OUT" | q '.stack.components | length == 2' || fail "tauri: two components: $OUT"
echo "$OUT" | q '.stack.components[0] | .path == "." and .package_manager == "pnpm" and .test.unit.command == "pnpm exec vitest run" and .test.unit.skill == "vitest" and (.checks | index("pnpm run check"))' \
  || fail "tauri ui: pnpm + vitest run (never the watch-mode test script) + check: $OUT"
echo "$OUT" | q '.stack.components[1] | .path == "src-tauri" and .framework == "tauri" and .test.unit.command == "cargo test --manifest-path src-tauri/Cargo.toml"' \
  || fail "tauri core: cargo test with manifest path: $OUT"
echo "$OUT" | q '[.stack.components[].name] | unique | length == 2' || fail "tauri: component names are unique: $OUT"
pass

# 2 — CI says nextest → the Rust runner follows CI.
R="$WORK/rust"; mkdir -p "$R/src" "$R/.github/workflows"
printf '[package]\nname = "tool"\n' > "$R/Cargo.toml"; : > "$R/src/main.rs"
cat > "$R/.github/workflows/ci.yml" <<'YML'
jobs:
  t:
    steps:
      - run: |
          cargo nextest run
          cargo clippy -- -D warnings
YML
OUT=$(python3 "$S" detect --root "$R")
echo "$OUT" | q '.stack.components[0].test.unit.command == "cargo nextest run" and (.ci_commands | index("cargo nextest run"))' \
  || fail "rust: CI nextest wins over cargo test: $OUT"
pass

# 3 — Python with uv + ruff, Go, and a subfolder component gets `cd <path> &&`.
P="$WORK/py"; mkdir -p "$P/tests"
printf '[project]\nname="svc"\n[dependency-groups]\ndev=["pytest","ruff"]\n' > "$P/pyproject.toml"; : > "$P/uv.lock"
OUT=$(python3 "$S" detect --root "$P")
echo "$OUT" | q '.stack.components[0] | .package_manager == "uv" and .test.unit.command == "uv run pytest -q" and .checks == ["uv run ruff check ."]' \
  || fail "python: uv run pytest + ruff: $OUT"
M="$WORK/mono"; mkdir -p "$M/api" "$M/web"
printf 'module example.com/api\n' > "$M/api/go.mod"
printf '{"name":"web","devDependencies":{"jest":"29"}}' > "$M/web/package.json"
OUT=$(python3 "$S" detect --root "$M")
echo "$OUT" | q '[.stack.components[] | {path, unit: .test.unit.command}] | sort_by(.path) == [{"path":"api","unit":"cd api && go test ./..."},{"path":"web","unit":"cd web && npx jest"}]' \
  || fail "monorepo: per-folder components with cd prefixes: $OUT"
pass

# 4 — pnpm workspace: one component at the root, nested package.json files are not separate.
W="$WORK/ws"; mkdir -p "$W/packages/a"
printf '{"name":"root","devDependencies":{"vitest":"3"}}' > "$W/package.json"; printf 'packages:\n  - packages/*\n' > "$W/pnpm-workspace.yaml"
printf '{"name":"a"}' > "$W/packages/a/package.json"
OUT=$(python3 "$S" detect --root "$W")
echo "$OUT" | q '.stack.components | length == 1 and .[0].path == "."' || fail "workspace: one root component: $OUT"
pass

# 5 — new vs unrecognised: README/.gitignore/AGENTS.md/.claude only = new; other files = not new.
E="$WORK/empty"; mkdir -p "$E/.claude/skills/x"
echo "# Idea" > "$E/README.md"; echo x > "$E/AGENTS.md"; echo node_modules > "$E/.gitignore"; echo x > "$E/.claude/skills/x/SKILL.md"
python3 "$S" detect --root "$E" | q '.new_project == true and .recognised == false' || fail "an empty project is new"
D="$WORK/docs"; mkdir -p "$D"; echo "notes" > "$D/notes.txt"
python3 "$S" detect --root "$D" | q '.new_project == false and .recognised == false' || fail "a folder with files is not new"
pass

# 6 — presets: categories fit AskUserQuestion (≤ 4 options), every preset is valid.
OUT=$(python3 "$S" presets)
echo "$OUT" | q '(.categories | length) <= 4 and all(.categories[]; (.presets | length) >= 1 and (.presets | length) <= 4)' \
  || fail "presets: ≤ 4 categories of ≤ 4 presets: $OUT"
for id in $(echo "$OUT" | jq -r '.categories[].presets[].id'); do
  P1=$(python3 "$S" preset "$id" --name "My App")
  echo "$P1" | q '.stack.components | length >= 1' || fail "preset $id has components"
  echo "$P1" | jq '.stack' > "$WORK/p.json"
  echo '{"version":1}' > "$WORK/pc.json"
  python3 "$S" apply --config "$WORK/pc.json" --stack "$WORK/p.json" --dry-run | q '.changes | index("stack")' \
    || fail "preset $id must pass apply's validation"
  [ -d "skills/super-board/references/stacks/templates/$id" ] || fail "preset $id needs a templates/ folder"
done
python3 "$S" preset nope >/dev/null && fail "unknown preset must fail" || [ $? -eq 65 ] || fail "unknown preset exits 65"
pass

# 7 — apply: writes stack + qa.mode, derives verify_commands only when empty (auto), keeps or replaces.
C="$WORK/c.json"
echo '{"version":1,"base_branch":"main","verify_commands":[]}' > "$C"
python3 "$S" detect --root "$T" > "$WORK/det.json"
OUT=$(python3 "$S" apply --config "$C" --stack "$WORK/det.json")
echo "$OUT" | q '.written and (.changes | index("stack")) and (.changes | index("qa.mode=tests-only")) and (.verify_commands | index("pnpm exec vitest run")) and (.verify_commands | index("cargo test --manifest-path src-tauri/Cargo.toml"))' \
  || fail "apply: stack, qa.mode and derived verify_commands: $OUT"
jq -e '.stack.components | length == 2' "$C" >/dev/null || fail "apply wrote the stack"
jq '.verify_commands = ["make check"]' "$C" > "$C.t" && mv "$C.t" "$C"
python3 "$S" apply --config "$C" --stack "$WORK/det.json" | q '.changes == [] and .verify_commands == ["make check"]' \
  || fail "apply auto keeps hand-written verify_commands"
python3 "$S" apply --config "$C" --stack "$WORK/det.json" --verify-commands replace | q '.changes == ["verify_commands"]' \
  || fail "apply replace rewrites verify_commands"
echo '{"components":[{"name":"x","path":".","language":"go","test":{"unit":{"skill":"y"},"smoke":null}}]}' > "$WORK/bad.json"
OUT=$(python3 "$S" apply --config "$C" --stack "$WORK/bad.json" || true)
echo "$OUT" | q '.error == "invalid stack" and (.problems | length == 2)' || fail "apply rejects a layer with no command and an unknown layer: $OUT"
python3 "$S" apply --config "$C" --stack "$WORK/det.json" --qa-mode crawl >/dev/null 2>&1 && fail "qa mode is browser|tests-only" || true
pass

# 8 — resolve: longest path wins, "." takes the rest, skills include the layer skills.
OUT=$(python3 "$S" resolve --config "$C" --files src-tauri/src/lib.rs src/routes/+page.svelte)
echo "$OUT" | q '.source == "config" and .qa_mode == "tests-only" and (.components | length == 2)' || fail "resolve: both components: $OUT"
echo "$OUT" | q '.components[] | select(.path == "src-tauri") | .files == ["src-tauri/src/lib.rs"] and .unit == "cargo test --manifest-path src-tauri/Cargo.toml"' \
  || fail "resolve: the Rust file goes to src-tauri: $OUT"
echo "$OUT" | q '.components[] | select(.path == ".") | .skills == ["vitest"]' || fail "resolve: ui skills: $OUT"
python3 "$S" resolve --config "$C" --files src-tauri/Cargo.toml | q '.components | length == 1 and .[0].path == "src-tauri"' \
  || fail "resolve: one touched component only"
# No stack in the config → detected at runtime, with a warning.
mkdir -p "$T/.claude/super-board/configs"; echo '{"version":1}' > "$T/.claude/super-board/configs/x.json"
python3 "$S" resolve --config "$T/.claude/super-board/configs/x.json" --files src/a.ts \
  | q '.source == "detected-at-runtime" and (.warning | test("super-board stack"))' || fail "resolve falls back to detection"
pass

# 9 — verify: read-only probes. Missing tool, missing package.json script, missing node_modules bin.
V="$WORK/v"; mkdir -p "$V/node_modules/.bin"; : > "$V/node_modules/.bin/vitest"
printf '{"scripts":{"check":"x"}}' > "$V/package.json"
cat > "$WORK/vs.json" <<'JSON'
{"components":[{"name":"a","path":".","language":"typescript",
  "test":{"unit":{"command":"npx vitest run","skill":"vitest"},"integration":null,
          "e2e":{"command":"npx playwright test","skill":null}},
  "checks":["npm run check","npm run lint","definitely-not-a-tool-xyz --go"]}]}
JSON
OUT=$(python3 "$S" verify --stack "$WORK/vs.json" --root "$V" || true)
echo "$OUT" | q '.ok == false and .missing == 3' || fail "verify: three missing: $OUT"
echo "$OUT" | q '[.commands[] | {command, status}] == [
  {"command":"npx vitest run","status":"ok"},{"command":"npx playwright test","status":"missing"},
  {"command":"npm run check","status":"ok"},{"command":"npm run lint","status":"missing"},
  {"command":"definitely-not-a-tool-xyz --go","status":"missing"}]' || fail "verify: per-command status: $OUT"
echo "$OUT" | q '.skills == [{"skill":"vitest","installed":false,"install":"npx -y skills@latest add antfu/skills --skill vitest -a claude-code -y"}]' \
  || fail "verify: lists the mechanics skill with its install command: $OUT"
pass

# 10 — ci: one workflow with the stack's setup, install and every check + test.
YML=$(python3 "$S" ci --stack "$WORK/det.json")
for want in "pnpm/action-setup@v4" "dtolnay/rust-toolchain@stable" "libwebkit2gtk-4.1-dev" "pnpm install --frozen-lockfile" \
            "run: pnpm exec vitest run" "run: cargo test --manifest-path src-tauri/Cargo.toml"; do
  echo "$YML" | grep -qF -- "$want" || fail "ci yaml should contain '$want'"
done
pass

# 11 — scaffold, offline: a stub catalog whose generator is a shell command. Files merge in
#      without overwriting, .gitignore lines merge, templates fill {{name}}, CI is written,
#      the tests run, and --commit makes the first commit with build output ignored.
CAT="$WORK/cat"; mkdir -p "$CAT/templates/stub/tests"
echo 'name = "{{name}}"' > "$CAT/templates/stub/tests/starter.txt"
cat > "$CAT/presets.json" <<'JSON'
{"categories":[{"id":"x","label":"X","description":"x","presets":["stub"]}],
 "presets":[{"id":"stub","label":"Stub","description":"offline","generator":"bash","qa_mode":"tests-only",
   "components":[{"name":"app","path":".","language":"rust","framework":null,"package_manager":"cargo",
     "test":{"unit":{"command":"test -f tests/starter.txt","skill":null},"integration":null,"e2e":null},
     "checks":["test -f src/main.rs"],"skills":[]}],
   "scaffold":[{"run":["bash","-c","mkdir -p {name}/src {name}/target && echo fn > {name}/src/main.rs && echo theirs > {name}/README.md && printf 'gen-ignore\\n' > {name}/.gitignore && echo junk > {name}/target/x"]},
               {"merge":"{name}"},{"templates":true},{"ci":true}]}],
 "skills":{},"runners":{}}
JSON
N="$WORK/new"; mkdir -p "$N"; git -C "$N" init -q
git -C "$N" config user.email t@t; git -C "$N" config user.name t
echo "# mine" > "$N/README.md"; echo "my-ignore" > "$N/.gitignore"
export SUPER_BOARD_STACKS="$CAT"
python3 "$S" scaffold --preset stub --root "$N" --name "My App" --dry-run | q '(.plan | length == 4) and (.plan[0].run | test("my-app/src"))' \
  || fail "scaffold --dry-run shows the plan with the name filled in"
OUT=$(python3 "$S" scaffold --preset stub --root "$N" --name "My App" --commit)
echo "$OUT" | q '.ok and .committed.ok and .kept_yours == ["README.md"]' || fail "scaffold: green, committed, README kept: $OUT"
[ "$(cat "$N/README.md")" = "# mine" ] || fail "scaffold never overwrites the user's README"
grep -qx "my-ignore" "$N/.gitignore" && grep -qx "gen-ignore" "$N/.gitignore" && grep -qx "target/" "$N/.gitignore" \
  && grep -qx ".claude/super-board/onboard-answers.json" "$N/.gitignore" || fail "scaffold merges .gitignore lines: $(cat "$N/.gitignore")"
grep -qx 'name = "my-app"' "$N/tests/starter.txt" || fail "templates fill {{name}}"
[ -f "$N/.github/workflows/ci.yml" ] || fail "scaffold writes CI"
git -C "$N" ls-files | grep -q '^target/' && fail "build output must not be committed"
git -C "$N" log -1 --format=%s | grep -q '^🔧 \[chore\] scaffold: Stub starter project$' || fail "commit follows the writing standard"
[ ! -d "$N/.claude/super-board/scaffold" ] || [ -z "$(ls -A "$N/.claude/super-board/scaffold")" ] || fail "staging folder removed on success"
python3 "$S" scaffold --preset stub --root "$N" >/dev/null && fail "scaffold refuses a folder with source files" || true
python3 "$S" scaffold --preset stub --root "$N" --resume | q '.ok and .resumed' || fail "--resume re-runs the checks"
pass

# 12 — a failing check that is the machine (missing system library) is named, with --resume as the next step.
jq '.presets[0].components[0].checks = ["echo \"The system library `gdk-3.0` required by crate `gdk-sys` was not found.\" >&2; exit 101"]' \
  "$CAT/presets.json" > "$CAT/p2" && mv "$CAT/p2" "$CAT/presets.json"
N2="$WORK/new2"; mkdir -p "$N2"
OUT=$(python3 "$S" scaffold --preset stub --root "$N2" --name app || true)
echo "$OUT" | q '.ok == false and (.needs_system.why | test("gdk-3.0")) and (.next | test("--resume --commit"))' \
  || fail "scaffold names a missing system library and the resume step: $OUT"
unset SUPER_BOARD_STACKS
pass

echo "PASS: test-stack.sh ($n scenarios)"
