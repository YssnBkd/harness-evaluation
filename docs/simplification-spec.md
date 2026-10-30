# Simplification spec

Branch `simplify`, from `main` at 9469c6a. Goal: smaller, clearer code and docs with **no change in behavior**.
Later work this must leave room for, without building it: `hev report --by harness` and `hev exec` (run any
harness × model in a real folder).

## Invariants (checked before every commit)

- Statuses and `classify()` are byte-for-byte the same. `result.json` keeps exactly its fields.
- `report.py` is untouched, so every statistic is the same.
- Gateway semantics are untouched: `gateway_hooks.py` is not edited, so per-trial keys, routing by trial, caps,
  budget, the quota breaker, the canary and the watchdog cannot change. `run_key()` hashes the same inputs.
- Isolation: the harness env is built from the same allowlist, HOME/TMPDIR are throwaway, the kill sequence is
  `killpg(TERM)`, wait 5 s, `killpg(KILL)`, then the `/proc` sweep, then the trial key is closed; grading applies
  the diff to a fresh copy, copies `hidden/` last, and runs offline under `unshare -rn`.
- Stdlib only; LiteLLM stays pinned in `gateway.py` and is imported only inside the proxy.
- Every command and flag keeps working, hidden ones included (`--fault`, `--canary`, `--route`, `--results`).
  None is renamed or replaced.
- Public names imported across modules and by the tests keep their names and signatures: `run_trial`, `grade`,
  `materialize`, `diff_against`, `solution_patch`, `classify`, `plant_decoys`, `INSTRUCTION_FILES`,
  `offline_prefix`, `check_task`, `fill`, `load_*`, `Model`, `Harness`, `Task`, `ConfigError`, `Gateway`,
  `litellm_bin`, `load_dotenv`, `LITELLM_VERSION`.
- House style stays: dense stdlib Python, comma-joined imports, comments that say why, messages that say the fix,
  config errors as `file: key: problem`.

## Changes per file

### `evals/trial.py` (296 → ≤ 275)

1. **Split `run_trial`** into three functions. `run_trial` keeps its keyword signature and return value.
   - `harness_env(harness, model, route, *, base_url, key, prompt, workdir, home, tmp, trial_id, max_requests)`
     → `(argv, env, vars)`. Writes `prompt.md`, the harness's `files` (route table merged one level deep over the
     shared table) and `.gitconfig` into `home`; builds the allowlisted env and the argv. It knows nothing about
     tasks, gateways or results: **this is the seam `hev exec` will call** with a real folder as `workdir`.
   - `launch(argv, env, workdir, *, stdin_text, timeout, out, trial_id)` → `(returncode, timed_out, suspended,
     wall, escaped)`. Popen in a new session, prompt on stdin when asked, wait with timeout, the exact kill
     sequence, the `/proc` sweep. The caller closes the gateway key in its own `finally`, after this returns or
     raises, as today.
   - `run_trial`: result dir, work dirs, `materialize`, decoys, `gateway.register`, `harness_env`, `launch`,
     diff, grade, the result dict (unchanged), atomic write, cleanup.
   Reason: a 107-line function becomes three with one job each, and the first is the `exec` seam.
2. `offline_prefix`: `functools.cache` instead of the `_UNSHARE` global. Same result, 4 lines fewer.
3. `_tool_dirs`: one expression over `dict.fromkeys`. Same order, same dedup.
4. New `requests_of(dir)` → the rows of `requests.jsonl` or `[]`. Used by `run_trial`, and by `cmd_regrade`
   (cli, step A2 follow-up) which has the same four lines today.
5. `graders.review` imported at the top instead of inside `grade` (no cycle: the grader imports only `re` and
   `pathlib`).

Stays: `git`, `_git_source`, `materialize`, `diff_against`, `solution_patch`, `script_for`, `grade`, `sweep`,
`classify`, `plant_decoys`, `GIT_ENV`, `INSTRUCTION_FILES`.

### `evals/cli.py` (371 → ≤ 305)

1. **`cmd_run` in three parts.** `plan_run(a, root)` loads config, picks names, reads installed versions, and
   returns the trial plan plus what was skipped and why. `estimate(plan, results)` returns the estimate and the hard
   cap. `cmd_run` prints, asks, starts the gateway, runs the loop and prints the summary. Messages and return codes
   are unchanged.
2. **`cmd_doctor`**: one `check(good, what, fix="", blocking=True)` closure replaces the twin `line`/`warn`
   closures. The smoke test calls `cmd_run(parser(root).parse_args(["run", "-m", "scripted", "--route", "all",
   ...]), root)` instead of hand-building a `Namespace` with every field, so a new `run` flag no longer has to be
   added in two places. Reason: this is what made the hidden flags "pile up".
3. **`parser(root)`** holds the argparse setup; `main` parses and dispatches. The hidden flags are declared in one
   loop with one comment. Help strings are trimmed only where they repeat the subcommand's help.
4. `cmd_regrade` uses `trial.requests_of`.

Stays: `pick`, `installed_version`, `run_key` (same blob, same order), `check_ancestors`, `check_task`,
`report_check`, `cmd_check_task`, `cmd_task`, `cmd_install`, `cmd_report`, the colors, the SIGTERM handler.

### `evals/config.py` (210 → ≤ 195)

1. `load_models` and `load_task` build the dataclass with `**` from the table, and the allowed-key set for `_check`
   is the dataclass's fields plus the documentation keys (`notes`, `tags`). Each TOML key is then named once, in the
   dataclass. `load_harness` keeps explicit construction: its TOML keys `name`, `route`, `path` map to the fields
   `title`, `routes`, `path_dirs`, which are not renamed (used by cli and trial).
2. Every validation rule, message and attribute name stays. `tree_hash`, `fill`, `_check`, `_load`, `route_for`,
   `PROVIDERS`, `PROTOCOLS`, `CACHE` unchanged.

### `evals/gateway.py` (149 → ≤ 140)

1. `missing_keys` inlined into `start`, its only caller. `HOOKS_DIR` inlined (used once).
2. Nothing else: the config written for LiteLLM, the env passed to it, `register`/`close`/`dead_models`/`preflight`/
   `stop` are unchanged.

### `evals/author.py` (255 → ≤ 235)

1. `from_pr`: the review-comment branch becomes `_review_findings(owner, repo, n, src, head)` → `(head, expect)`.
2. `fresh(dest)` wraps the whole of `from_pr` and `new`, so the two extra "already exists" checks go. The collision
   is still reported before any clone or question, and a half-made folder is still removed on failure.
3. A `header(cmd, todo, name)` helper writes the three "Made by `hev task ...`" comment lines.

Stays: `TEST_FILE`, `REVIEW_PROMPT`, `CLOSES`, `guess_verify`, `write_toml`, `build`, `api`, `tree_at`, `tq`,
`tq_long`, `ask`, `slug`, `clean`, `from_commit`.

### Untouched, deliberately

- `evals/gateway_hooks.py` (252): every line serves a gateway guarantee that must not change, and the metadata
  lookups encode LiteLLM-version quirks found in the Phase 0 spike whose only test is the full replay. Leaving its
  bytes alone also leaves `run_key` alone.
- `evals/report.py` (129): the statistics. The `--by harness` seam is already there: `summarize` groups by model
  then by harness; a later change parametrizes the two keys.
- `graders/review.py`, `hev`, `harnesses/*.toml` (isolation settings stay identical), `models.toml`, `tasks/`,
  `.github/workflows/ci.yml`.

### Tests

- `tests/test_units.py`: imports unchanged. One test added for the seam: `harness_env` on the e2e fake harness
  gives an argv with the gateway URL and key, a HOME under the work folder, and an env without any variable of the
  runner's own environment.
- `tests/test_e2e.py`, `tests/fake_agent.py`: unchanged.

### Docs

Real minimalism, not relocation: no new doc, and the three together go from 297 lines to at most about 180.

- `README.md` (121 → ≤ 45): one-sentence pitch; the two quickstart commands; the two paid-run commands; an example
  of `hev report` output rendered by `report.render` on illustrative rows and labelled as an example (no results
  are published yet); three bullets on the guarantees (same model behind every harness; nothing of yours is read or
  changed, keys never reach a harness; the report says when it can't tell); a line each on what the statuses mean
  and on adding a model or a harness; links to `TASKS.md`, the harness TOMLs and `./hev --help`; license. The
  harness table, the commands table, the spending section and the status section are cut: `./hev --help`,
  `harnesses/*.toml` and `models.toml` say it.
- `TASKS.md` (106 → ≤ 70): the same one example per kind, with less prose around each; the loader rules that the
  error messages already state are dropped.
- `CLAUDE.md` (70 → ≤ 55): names the new functions, drops what the code now says, keeps the commands, the
  isolation rules and the config rule.

## Line budget

| File | Before | Budget |
|---|---:|---:|
| `evals/cli.py` | 371 | 305 |
| `evals/trial.py` | 296 | 275 |
| `evals/author.py` | 255 | 235 |
| `evals/gateway_hooks.py` | 252 | 252 |
| `evals/config.py` | 210 | 195 |
| `evals/gateway.py` | 149 | 140 |
| `evals/report.py` | 129 | 129 |
| `graders/review.py` | 49 | 49 |
| `hev` | 11 | 11 |
| **Python total** | **1,722** | **≤ 1,591** |
| `README.md` | 121 | 45 |
| `TASKS.md` | 106 | 70 |
| `CLAUDE.md` | 70 | 55 |
| **Docs total** | **297** | **≤ 180** |

The brief's "about 1,500" would need cuts in the hooks and the report, which this spec keeps whole on purpose.

## Verification

All $0. `V1` runs in seconds, `V2` in about a minute, `V3`–`V5` in minutes.

| | Command | Passes when |
|---|---|---|
| V1 | `python3 -m unittest tests.test_units` | ok |
| V2 | `python3 -m unittest tests.test_e2e` | ok (real gateway, scripted model, fake agent, kill -9) |
| V3 | `./hev check-task` | 13 tasks ok |
| V4 | `./hev doctor` | every route of all 5 harnesses passes |
| V5 | `R=$(mktemp -d); ./hev run -m scripted --route all --results $R` | 104/104 pass |
| V6 | compare V5's `result.json` files with the baseline's | same status, score, note, route, translation, served and requested models, patch lines, escaped processes, exit code, per trial (the trial dirs have the same names because `run_key` inputs are unchanged) |

A baseline of V1–V5 on the unchanged code is kept in `/tmp/hev-baseline` (results in `results/`, log in `log.txt`).

Per step: A1 runs V1, V2, V4; A2 runs V1, V2, V4; A3 runs V1, V2, V3. After wave A is merged: V1–V6. Docs steps:
V1 plus a proofread against `./hev --help`. Before proposing the merge: V1–V6 again, and the fresh-eyes review.

## Work breakdown

Each piece gets a brief with the goal, its file list, the invariants above, its verification commands and what to
report. No two concurrent pieces share a file; concurrent pieces run in their own git worktree and are merged by
the orchestrator after review.

| Step | Files | Who | Why |
|---|---|---|---|
| 0 | `docs/simplification-spec.md`, baseline | orchestrator | done |
| A1 | `evals/trial.py`, `tests/test_units.py` | fresh agent, Opus, high | the subtlest piece: the kill sequence, env allowlist and result dict must survive a split; a written brief with the invariants, not a fork, keeps the cost down |
| A2 | `evals/cli.py` | fresh agent, Opus, high | same reasons; `run_key` and every message must stay |
| A3 | `evals/config.py`, `evals/gateway.py`, `evals/author.py` | fresh agent, Opus, medium | mechanical and well specified; the unit tests pin the error messages and `from_commit` |
| merge | – | orchestrator | review each diff, merge, run V1–V6, commit one step at a time |
| B1 | `README.md` | orchestrator (Fable 5.1) | voice matters and the full picture is here |
| B2 | `TASKS.md`, `CLAUDE.md` | fresh agent, Sonnet, medium | a sync against the final code and `--help` |
| C | review of `git diff main..simplify` | fresh agent, Opus, high | fresh eyes: gets the diff and the invariants, not the orchestrator's conclusions |
| end | spec "Outcome", V1–V6 | orchestrator | |

A1, A2 and A3 run concurrently. B1 and B2 run concurrently after wave A is committed. C runs last.

Commits on `simplify`, in order: the spec; A1; A2 (with the `requests_of` follow-up in `cmd_regrade`); A3; B1 and
B2 together; the Outcome. The merge into `main` is the user's call.
