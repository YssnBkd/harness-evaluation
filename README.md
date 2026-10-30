# harness-evals

Which coding-agent harness gets the most out of a model? `hev` runs Claude Code, opencode, Hermes Agent, Antigravity
CLI, DeepSeek Harness, or any CLI you describe in a TOML file, on the same tasks with **the same model behind every
one of them**, then says which harness is better, or that the data can't tell yet.

## Quickstart

Linux or WSL2, Python 3.11+, git, curl, [uv](https://docs.astral.sh/uv/) and Node.js. Nothing of yours is touched.

```sh
./hev install          # the five harnesses, pinned, into ~/.cache/hev, plus the LiteLLM gateway
./hev doctor           # checks the setup, then drives every harness with a $0 scripted model
cp .env.example .env   # your API keys; they only ever reach hev's local gateway
./hev run -m opus      # shows the estimate and asks before spending; rerun to resume
./hev report
```

```
== opus ==
harness    pass   rate  95% CI   $/trial  requests  secs  route   excluded
harness-a  15/16  94%   72%–99%  0.274    16        203   native  0       
harness-b  11/16  69%   44%–86%  0.287    22        150   native  0       
harness-c  3/16   19%   7%–43%   0.388    20        106   native  0       

  harness-a vs harness-b: insufficient evidence (+25 pts [+0, +50], 16 tasks); ≈121 tasks needed to detect 10 pts
  harness-a > harness-c: +75 pts [+50, +94], 16 tasks
  harness-b > harness-c: +50 pts [+25, +75], 16 tasks
```

Example output: no results are published yet. What `hev` guarantees:

- **One model behind every harness.** A local gateway routes each trial's calls by its own key, whatever model name
  the harness sends, so side calls go to the same model too. It meters cost and enforces the task caps and `--budget`.
- **A clean room.** Each harness runs in a throwaway home with an allowlisted environment and never sees a key. The
  change is graded on a fresh copy, hidden tests copied in last, offline. Decoy instruction files above the repo
  catch a harness that reads where it shouldn't (`leak`).
- **An honest verdict.** Harnesses are compared per model, paired by task (bootstrap, exact McNemar, Holm). When two
  can't be separated, the report says so, and how many tasks it would take.

Statuses: `pass`, `fail`, `fail:incompatible` (the model rejected what the harness sent), `timeout`, `capped`,
`leak`, and `error:*` for infrastructure problems, which are left out of the rates and rerun with `--retry-errors`.

A model is a few lines in `models.toml`. A harness is a TOML file in `harnesses/` (how to install and run it headless,
one `[route.*]` per protocol it speaks); the five included are the examples. A task is `./hev task from-pr <url>`, or
see [TASKS.md](TASKS.md). `./hev --help` has the rest. [MIT](LICENSE).
