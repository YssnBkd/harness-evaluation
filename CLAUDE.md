# CLAUDE.md

## What this is

`hev` runs coding-agent harnesses (agent CLIs) on a set of tasks with the **same model** behind each one, then reports whether any harness is measurably better, with confidence intervals. Every model call goes through a local LiteLLM gateway that the runner starts, so the runner controls which model serves each trial, what it costs, and when it is cut off. The runner is stdlib-only Python 3.11+ and Linux-only (`/proc`, `unshare -rn`, `killpg`); its one third-party dependency is LiteLLM, pinned in `evals/gateway.py` (`LITELLM_VERSION`) and installed as a `uv` tool. Do not add packages.

## Commands

```bash
./hev doctor [--quick]                     # check the setup; without --quick, also runs a $0 scripted smoke test
./hev install [harness-glob] [--litellm]   # install pinned harnesses into ~/.cache/hev/<name>@<version>
./hev run -m scripted -H <harness> -t '0*' # $0 run: the scripted model replays each task's reference solution
./hev run -m opus -n 3 --budget 5          # paid run (asks before spending more than $1; --yes skips the prompt)
./hev report [--md|--json]                 # compare harnesses per model from results/
./hev check-task [task]                    # valid only if the starting code fails and the solution passes (3×)
./hev regrade                              # re-grade saved diffs with the current verifiers ($0)
./hev task from-pr <url> | from-commit <repo> <sha> | new <name>   # make a task (+ --review), then check it

python3 -m unittest tests.test_units                     # fast: no gateway, no network
python3 -m unittest tests.test_e2e                       # real gateway + scripted model + fake agent; $0, ~1–2 min, needs litellm
python3 -m unittest tests.test_units.Classify.test_statuses   # a single test
```

No linter or build step. CI (`.github/workflows/ci.yml`) runs the unit and e2e tests, `check-task`, and `doctor` for the npm-installed harnesses. Hidden `run` flags: `--fault {429,429once,quota,500,400,hang,midstream}` makes the scripted model misbehave, `--route <protocol|all>` forces a route (`all` runs every declared route; `doctor` uses it), `--canary <string>` replaces the random canary token, `--results <dir>`. Environment: `HEV_ROOT` points the runner at another root (tests use it); `HEV_WORK` sets where trial work folders go (default `$TMPDIR/hev/<nonce>`).
Use `-m scripted` to test runner, gateway, or harness changes. Only run a paid model when the user asks for one.

## Architecture

**Two processes, communicating through files.** `evals/cli.py` starts one LiteLLM proxy per `hev run` (`evals/gateway.py`). LiteLLM imports `evals/gateway_hooks.py` through `PYTHONPATH`, so the hooks can import `litellm`/`fastapi` but the runner cannot import the hooks. Shared state lives in `results/gateway/<nonce>/`:
- `keys/<api-key>.json`: written by `Gateway.register` before a trial, deleted when it ends. The hooks use it to authenticate, **route by trial** (the model name the harness sends is overwritten), and enforce `max_requests`, `max_cost` and `--budget`. Leftover processes can't spend money.
- `<trial dir>/requests.jsonl`: one row per call plus cap, quota and leak events. `classify()` in `trial.py` turns the rows (read by `requests_of`) into the status.
- `dead/<model>`: quota circuit breaker; after a non-retryable quota error the rest of the run skips that model.
- A watchdog thread in the proxy kills every process carrying `HEV_RUN=<nonce>` and removes the work folder if the runner dies, even from `kill -9`.
- **Canary** (always on): `plant_decoys` writes a random token into decoy instruction files in the work folder and the trial folder, both above the repo. A request body containing it makes the trial `leak`. Claude Code loads `CLAUDE.md` from every parent folder, so its TOML sets `claudeMdExcludes = ["!{workdir}/**"]`.

**Scripted model** (bottom of `gateway_hooks.py`): an OpenAI-compatible server in the proxy process. On the first turn it makes one call to whatever shell tool the harness offers (`_pick_shell`), which runs `git apply` on the task's reference solution; then it answers "Done.". The pre-call hook appends the key to the model name so it knows the trial.

**A run.** `cmd_run` is `plan_run` (the trials not yet done) + `estimate` (expected cost and the cap, for the prompt) + the loop calling `run_trial`; `doctor` reuses `parser(root)` to call `cmd_run`. `run_trial` (`trial.py`) is: `materialize` (fresh git repo with deterministic author and date, from `fixture/` or a pinned `git`+`rev`; `repo.patch` committed on top, `repo.setup` run) + `gateway.register` + `harness_env` (writes the harness's `files` into a throwaway `HOME`, returns argv and an allowlisted env; it knows no task or gateway, so a planned `hev exec` command, not built yet, can reuse it) + `launch` (own session; on exit or timeout `killpg`, then a `/proc` sweep for `HEV_TRIAL=<id>`) + `diff_against` (agent commits included, the harness's `artifacts` globs excluded) + `grade` + `result.json`. **`grade` works on a clean copy**: apply the diff, copy `hidden/` in last, run `verify.cmd` offline under `unshare -rn` with a minimal environment. A last stdout line `{"score": x}` gives partial credit; `verify.cmd = "review"` runs `graders/review.py` on `REVIEW.md` against the `[[expect]]` entries.

**Resume** depends on `run_key()` in `cli.py`: it hashes the task's content, model entry, harness TOML text, installed harness version, LiteLLM version, **the bytes of `gateway_hooks.py`**, the caps, the trial index and the route. A change gives a new `results/trials/<task>__<harness>__<model>__<idx>__<key8>/`, so the trial runs again. `result.json` is written atomically, so an interrupted trial reruns. **Statuses**: `error*` are infrastructure failures; like `leak` they are left out of pass rates and re-run by `--retry-errors`; every other status is a verdict on the harness.

**Report** (`evals/report.py`): per model, each harness's pass rate with a Wilson CI; each pair gets a per-task bootstrap of the difference, an exact McNemar test and a Holm correction, and is "separated" only when the CI excludes 0 and the Holm-adjusted p < 0.05, else "insufficient evidence" plus an estimate of the tasks needed. Scripted results are ignored once real ones exist.

## Config (strict by design)

`evals/config.py` loads `models.toml`, `harnesses/*.toml` and `tasks/*/task.toml`. **Unknown keys are errors** (`file: key: problem`), so a typo can't change an experiment. For models and tasks the allowed keys are the dataclass fields (minus the internal `name`, `dir`, `hash`) plus `notes` (and `tags` for tasks), so a new key is a new field; for harnesses, add it to the set in `load_harness`. Model `provider` must be a key of `PROVIDERS`; API keys come from `.env` (template `.env.example`) and go only to the gateway.
- **Placeholders**: strings in harness TOMLs may use `{name}`, filled by `fill()`; an unknown name is an error. The names are the `vars` dict in `harness_env`: `base_url key model home workdir tmp prompt prompt_file prefix version trial max_requests model.context model.max_output bin`. A string that is exactly one numeric placeholder (`"{model.context}"`) stays a number.
- **Routes**: `[route.<anthropic|openai|gemini>]` sets per-protocol `env`/`files`/`args`. A harness uses the model's native protocol if it declares it, else its first route, and LiteLLM translates. A route's `files` merge over the shared `files` for the same path, one level deep. `files` paths are relative to `HOME` unless absolute. `[scripted] model` is the model name a harness gets in scripted runs. The `HARNESS` string in `tests/test_e2e.py` is a minimal working harness.
- **Tasks** (`tasks/<name>/`): see `TASKS.md`; run `./hev check-task <name>` after any edit. Whatever `repo.setup` creates is added to `.git/info/exclude` and whatever it edits is committed, so neither shows in the agent's diff. `evals/author.py` implements `hev task`: it guesses the verify command and `setup` from the repo layout and the test files the change touched, which become `hidden/`. Python setups use `uv venv`.

## Isolation rules to keep

- No instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `CLAUDE.local.md`) may exist above the trial work folder, because some harnesses would read it. **This file sits in the repo root, so never point `HEV_WORK` inside this repo.** `check_ancestors` refuses to run if you do.
- Harness processes get only the allowlisted environment built in `harness_env`. Don't forward the runner's environment.
- Debugging a trial: look in `results/trials/<id>/` (`stdout`, `stderr`, `requests.jsonl`, `scripted.jsonl`, `diff.patch`, `verify.log`, `result.json`) and `results/gateway/<nonce>/gateway.log`. `--keep` keeps the work folder.
