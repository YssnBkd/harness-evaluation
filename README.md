# harness-evals

Which coding-agent harness gets the most out of a given model? `hev` runs harnesses (Claude Code, opencode, Hermes,
Antigravity CLI, DeepSeek Harness, or any CLI you describe in a TOML file) on the same tasks, with **the same model
behind every one of them**. Then it tells you which harness is better, or that the data can't tell yet.

The same setup also takes the harness out of model comparisons: compare models within one harness, and the harness
stops being a hidden variable.

## Quickstart

Needs Linux or WSL2 (x86-64), Python 3.11+, git, curl, [uv](https://docs.astral.sh/uv/), and Node.js with npm.

```sh
git clone https://github.com/YssnBkd/harness-evaluation.git harness-evals && cd harness-evals
./hev install    # the five harnesses, pinned, into ~/.cache/hev (about 5 GB), plus the LiteLLM gateway
./hev doctor     # checks the setup, then a $0 test of every harness on every route
```

`doctor` needs no API key. Its test uses a **scripted model**: a fake model that drives each harness's own shell
tool to apply each task's reference solution. It proves the whole pipeline works without spending anything.

For a real run, put your keys in `.env` (`cp .env.example .env`), then:

```sh
./hev run -m opus -t '0*' -n 1    # shows an estimate and asks before spending
./hev report                      # harnesses compared per model
```

Nothing you already have is touched. Harnesses are installed under `~/.cache/hev` and run with a throwaway home
folder, so your own Claude Code, opencode or Hermes setup is neither read nor changed.

## How a run works

- **One gateway for every model call.** `hev run` starts a local [LiteLLM](https://github.com/BerriAI/litellm)
  proxy. Each trial gets its own key, and the gateway routes **by key, not by the model name the harness sends**:
  even a harness's side calls (titles, summaries, "small model" slots) go to the trial's model. It also meters
  tokens and cost, and enforces each task's request and cost caps and the run's `--budget`.
- **Isolated trials.** Each trial gets a fresh git repo, a throwaway `HOME`, and an environment built from an
  allowlist. **API keys never reach a harness**, only the gateway. Afterwards every process of the trial is killed,
  including ones that left its process group, even if `hev` itself is killed.
- **A canary on every run.** Decoy instruction files (`CLAUDE.md`, `AGENTS.md`, ...) carrying a random token sit
  above each trial's repo. A request quoting the token marks the trial `leak`, and it is excluded.
- **Grading on a clean copy.** The agent's change, including any commits it made, is applied to a fresh copy of the
  starting repo. Hidden tests are added last, so editing the tests doesn't help, and the check runs offline.
- **Resumable.** A finished trial is never paid for twice. Run the same command again to continue, and use
  `hev regrade` to re-score saved changes for $0 after fixing a check.

## Reading the results

`hev report` compares harnesses **per model, paired by task**. For each harness it shows the pass rate with a 95%
confidence interval, cost per trial, requests and time. For each pair of harnesses it gives either a verdict
("A > B: +18 pts [+5, +31], 14 tasks") or **"insufficient evidence"**, with an estimate of how many tasks would
settle it. It uses a bootstrap over tasks and an exact McNemar test, corrected for multiple comparisons. More tasks
help more than more trials.

| Status | Meaning |
|---|---|
| `pass` / `fail` | the check passed / failed |
| `fail:incompatible` | the model rejected what the harness sent (HTTP 400/404/422), usually a protocol translation problem |
| `timeout`, `capped` | ran out of time / hit the task's request or cost cap |
| `leak` | something from outside the trial reached the model; excluded from rates |
| `error:*` | an infrastructure problem (quota, network, laptop asleep); excluded from rates, rerun with `--retry-errors` |

## What's included

| Harness | Version | Speaks | Notes |
|---|---|---|---|
| Claude Code | 2.1.314 | Anthropic | |
| opencode | 1.19.3 | OpenAI, Anthropic | |
| Hermes Agent | 0.22.2 | OpenAI, Anthropic | its installer fetches its own Python and tools (about 2.7 GB) |
| Antigravity CLI (`agy`) | 1.4.10 | Gemini | closed source, Linux x64 only; its telemetry is tied to the Google account |
| DeepSeek Harness (`dsh`) | 0.2.1-rc.5 | OpenAI, Anthropic | pre-release |

When a harness doesn't speak a model's protocol, the gateway translates. Results are labelled with any translation,
so a native pairing is never silently compared with a translated one.

**Tasks:** 12 small ones plus a smoke test. There are bug fixes, implementations, a refactor and a multi-file feature
in Python, one JavaScript task, four tasks from real pull requests on more-itertools and Python-Markdown, and three
code-review tasks graded on the findings they report.

## Commands

| Command | |
|---|---|
| `hev run -m MODELS [-H HARNESSES] [-t TASKS] [-n TRIALS] [--budget USD]` | run the matrix (names or globs, comma-separated) |
| `hev report [--md \| --json]` | compare harnesses per model |
| `hev install [HARNESSES] [--force] [--litellm]` | install pinned harnesses and the gateway |
| `hev doctor [-H HARNESSES] [--quick]` | check the setup; without `--quick`, run the $0 test |
| `hev task from-pr URL \| from-commit REPO SHA \| new NAME` | make a task (add `--review` for a review task) |
| `hev check-task [TASKS]` | a task is valid when the starting code fails and the solution passes |
| `hev regrade` | re-score saved trials with the current checks ($0) |

## Adding your own

- **A model:** a few lines in `models.toml`. Providers: `anthropic`, `gemini`, `openai`, `deepseek`. Add a `price`
  for models LiteLLM's price list doesn't know yet.
- **A harness:** a TOML file in `harnesses/` with how to install it, how to run it headless, and one `[route.*]`
  table per protocol it speaks, giving the environment and config files that point it at the gateway. The five
  included files are the examples. Unknown keys are errors, so a typo can't silently change an experiment.
- **A task:** `./hev task from-pr https://github.com/owner/repo/pull/123` turns a merged PR into a task: the repo
  before the PR, the PR as the solution, and the test files it touched as hidden tests. `TASKS.md` covers every kind
  of task.

## Spending

Before a paid run, `hev run` shows an estimate and a hard cap, and asks above $1 (`--confirm-above`). A tiny
**preflight** request per model catches a bad key or an empty quota before any trial starts. Each task caps its own
requests and cost, `--budget` caps the whole run, and a model that runs out of quota is skipped for the rest of the
run instead of failing every remaining trial.

## Status

v1 is in progress. **No results are published yet.** The comparison matrix will come only from a real paid run,
never from scripted numbers. Not built yet: running trials in parallel, a harness's subscription login instead of an
API key, and an optional Docker sandbox (v1.1). Planned: local models served by llama.cpp, at several quantization
levels.

## License

[MIT](LICENSE)
