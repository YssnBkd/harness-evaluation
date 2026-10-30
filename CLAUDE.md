# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`hev` runs coding-agent harnesses (agent CLIs) on a set of tasks with the **same model** behind each one, then reports whether any harness is measurably better, with confidence intervals. Every model call goes through a local LiteLLM gateway that the runner starts, so the runner controls which model serves each trial, what it costs, and when it is cut off.

The runner uses only the standard library on Python 3.11+ (`tomllib`) and is Linux-only (`/proc`, `unshare -rn`, `killpg`, `CLOCK_BOOTTIME`). The only third-party dependency is LiteLLM, pinned in `evals/gateway.py` (`LITELLM_VERSION`) and installed as a `uv` tool. Do not add packages to the runner.

## Commands

```bash
./hev doctor [--quick]                     # check the setup; without --quick, also runs a $0 scripted smoke test
./hev install [harness-glob] [--litellm]   # install pinned harnesses into ~/.cache/hev/<name>@<version>
./hev run -m scripted -H <harness> -t '0*' # $0 run: the scripted model replays each task's reference solution
./hev run -m opus -n 3 --budget 5          # paid run (asks before spending more than $1; --yes skips the prompt)
./hev report [--md|--json]                 # compare harnesses per model from results/
./hev check-task [task]                    # task is valid only if the starting code fails and the solution passes (3×)
./hev regrade                              # re-grade saved diffs with the current verifiers ($0)
./hev task from-pr <url> | from-commit <repo> <sha> | new <name>   # make a task (+ --review), then check it

python3 -m unittest tests.test_units                     # fast: no gateway, no network
python3 -m unittest tests.test_e2e                       # real gateway + scripted model + fake agent; $0, ~1–2 min, needs litellm
python3 -m unittest tests.test_units.Classify.test_statuses   # a single test
```

There is no linter or build step. Hidden `run` flags: `--fault {429,429once,quota,500,400,hang,midstream}` makes the scripted model misbehave (used by the e2e tests), `--route <protocol|all>` forces a route (`all` runs every route each harness declares; `doctor` uses it, so every route is tested at $0), `--canary <string>` replaces the run's random canary token, and `--results <dir>`. Environment variables: `HEV_ROOT` points the runner at another root (tests use it) and `HEV_WORK` sets where trial work folders go (default `$TMPDIR/hev/<nonce>`).

Use `-m scripted` to test runner, gateway, or harness changes. Only run a paid model when the user asks for one.

## Architecture

**Two processes, communicating through files.** `evals/cli.py` (the runner) starts one LiteLLM proxy per `hev run` (`evals/gateway.py`). LiteLLM imports `evals/gateway_hooks.py` through `PYTHONPATH` inside the proxy process, so the hooks can import `litellm`/`fastapi` but the runner cannot import the hooks. All shared state goes through `results/gateway/<nonce>/`:
- `keys/<api-key>.json`: the runner writes this before a trial starts and deletes it when the trial ends. The hooks use it to authenticate the request, **route by trial** (the model name the harness sends is overwritten), and enforce `max_requests`, `max_cost` and the run's `--budget`. Leftover processes therefore can't spend money.
- `<trial dir>/requests.jsonl`: one row per call (tokens, cost, served model, error) plus cap, quota and leak events. `classify()` in `trial.py` turns these rows into the trial's status.
- `dead/<model>`: quota circuit breaker. Once a model hits a non-retryable quota error, the rest of the run skips it.
- A watchdog thread in the proxy kills every process carrying `HEV_RUN=<nonce>` and removes the work folder if the runner dies, even from `kill -9`.
- **Canary** (always on): each run picks a random token, and `plant_decoys` writes it into decoy instruction files in the work folder and the trial folder, both above the repo. Any request body containing the token makes the trial `leak`. Claude Code loads `CLAUDE.md` from every parent folder, so its TOML sets `claudeMdExcludes = ["!{workdir}/**"]`.

**Scripted model** (bottom of `gateway_hooks.py`): an OpenAI-compatible server inside the proxy process. On the first turn it emits a single call to whatever shell tool the harness offers (`_pick_shell` heuristics); the call runs `git apply` on the task's reference solution. After that it answers "Done.". It knows which trial a request belongs to because the pre-call hook appends the key to the model name. What each harness offered is logged to `scripted.jsonl`, which is how adapter problems are debugged.

**A trial** (`evals/trial.py: run_trial`):
1. Build the starting repo: a copy of `fixture/` or a pinned `git`+`rev` from a bare-clone cache. If set, `repo.patch` is committed on top (the change under review in review tasks) and `repo.setup` is run. The result is a fresh git repo with deterministic author and date.
2. Give the harness a throwaway `HOME`/`TMPDIR` and an allowlisted environment. The harness TOML's `files` are written into that `HOME`.
3. Launch the harness in its own session. When it exits or times out, kill its process group, then sweep `/proc` for anything still carrying `HEV_TRIAL=<id>`.
4. Diff against the base commit (agent commits included, the harness's `artifacts` globs excluded).
5. **Grade a clean copy**: apply the diff, copy `hidden/` in last (it overwrites any agent copy of the tests), then run `verify.cmd` offline under `unshare -rn` with a minimal environment. If the last stdout line is `{"score": x}`, it gives partial credit. Review tasks (`verify.cmd = "review"`) are graded by `graders/review.py`, which compares `REVIEW.md` against the task's `[[expect]]` entries.

**Statuses**: `error*` statuses are infrastructure failures. Like `leak`, they are left out of pass rates and are re-run by `--retry-errors`. Every other status is a verdict on the harness.

**Resume** depends on `run_key()` in `cli.py`. It hashes the task's content, the model entry, the harness TOML text, the installed harness version, the LiteLLM version, **the bytes of `gateway_hooks.py`**, the caps and the trial index. Changing any of these gives a new trial directory `results/trials/<task>__<harness>__<model>__<idx>__<key8>/`, so the trial runs again. `result.json` is written atomically, so an interrupted trial leaves no result and runs again on resume.

**Report** (`evals/report.py`): for each model it shows each harness's pass rate with a Wilson CI. Each pair of harnesses gets a per-task bootstrap of the difference, an exact McNemar test and a Holm correction. A pair is called separated only when the CI excludes 0 and the Holm-adjusted p is below 0.05. Otherwise the report says "insufficient evidence" and estimates how many tasks would be needed. Scripted results are ignored once real ones exist.

## Config (strict by design)

`evals/config.py` loads `models.toml`, `harnesses/*.toml` and `tasks/*/task.toml`. **Unknown keys are errors**, so a typo can't silently change an experiment. When you add a key, also add it to the allowed set passed to `_check`. Errors use the format `file: key: problem`.

- **Placeholders**: strings in harness TOMLs can use `{name}`, filled in by `fill()`; an unknown name is an error. The available names are the `vars` dict in `run_trial`: `base_url key model home workdir tmp prompt prompt_file prefix version trial max_requests model.context model.max_output bin`. A string that is exactly one numeric placeholder (`"{model.context}"`) stays a number.
- **Harness routes**: `[route.<anthropic|openai|gemini>]` sets per-protocol `env`/`files`/`args`. A harness uses the model's native protocol when it declares it; otherwise it uses its first declared route and LiteLLM translates. A route's `files` table is merged over the shared `files` table for the same path, one level deep. Config tables are written as JSON, which is also valid YAML. `files` paths are relative to the trial's `HOME` unless absolute. `[scripted] model` sets the model name a harness is given in scripted runs.
- **The five harnesses** (`harnesses/*.toml`): their isolation settings come from the Phase 0 spike, which is recorded in the plan file linked from memory. Hermes runs each trial as a profile under one shared data root in its prefix (`cleanup` removes the profile), and its installer runs with `HOME` set inside the prefix. agy is a pinned tarball verified by sha512. The `HARNESS` string in `tests/test_e2e.py` is a minimal working harness definition.
- **Models**: `provider` must be a key of `PROVIDERS`. API keys come from `.env` in the repo root (template: `.env.example`) and are passed only to the gateway, never to harnesses.
- **Tasks** (`tasks/<name>/`): `task.toml`, plus `fixture/` (starting code), `hidden/` (verifier files added at grading time), and `solution/` or `solution.diff` (needed by `check-task` and for scripted runs). A task may not ship a `.env`. Run `./hev check-task <name>` after adding or editing a task. `TASKS.md` has one example per kind of task. Whatever `repo.setup` creates is added to `.git/info/exclude`, and whatever it edits is committed, so neither shows up in the agent's diff. `evals/author.py` implements `hev task`. It guesses the verify command (and any `setup`) from the repo layout and the test files the change touched, and those test files become `hidden/`. Python setups use `uv venv` (hev already requires uv; Debian's Python may lack `venv`). Packages that declare entry points must be installed for their tests to find plugins by name.

## Isolation rules to keep

- No instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `CLAUDE.local.md`) may exist above the trial work folder, because some harnesses would read it. **This file sits in the repo root, so never point `HEV_WORK` inside this repo.** `check_ancestors` refuses to run if you do.
- Harness processes get only the allowlisted environment built in `run_trial`. Don't forward the runner's environment.
- Debugging a trial: look in `results/trials/<id>/` (`stdout`, `stderr`, `requests.jsonl`, `scripted.jsonl`, `diff.patch`, `verify.log`, `result.json`) and in `results/gateway/<nonce>/gateway.log`. Pass `--keep` to keep the work folder.
