# Writing tasks

A task is a folder in `tasks/` with a `task.toml`. The agent gets the `prompt` in a fresh copy of the starting repo.
When it stops, its changes are applied to another fresh copy, the `hidden` files are added, and `verify.cmd` runs
there, offline. Exit code 0 means solved.

Most tasks come from history. Make one from a change that already solved a problem:

```sh
./hev task from-pr https://github.com/owner/repo/pull/123            # the repo before the PR; the PR is the solution
./hev task from-commit ~/code/myrepo 1a2b3c4                         # the same from a local commit (copied, not uploaded)
./hev task from-pr https://github.com/owner/repo/pull/123 --review   # a code-review task instead
./hev task new my-task                                               # three questions, then a commented task.toml
```

Every command ends by checking the task. A task is valid when the starting code fails the check and the reference
solution passes it 3 times in a row. Re-check after editing with `./hev check-task my-task`.

## One example per kind

**Fix or implement, with hidden tests** (`01-fix-slugify`, `02-implement-lru`). The agent never sees `hidden/`. Any
file in it that the agent also wrote is overwritten before grading, so editing the tests doesn't help.

```toml
prompt = "The function `slugify` in `textutil.py` is buggy: ... Fix it."

[verify]
cmd = "python3 -m unittest test_slugify"
hidden = "hidden"            # tasks/<name>/hidden/test_slugify.py
```

Folders: `fixture/` (the starting code), `hidden/`, and `solution/`, which holds the solved files and is laid over
`fixture/`. A `solution.diff` can replace `solution/`. The solution proves the task is solvable, and the $0 scripted
model replays it to test harnesses.

**Check the code itself, not only tests** (`03-rename-refactor`). Any shell works:

```toml
[verify]
cmd = """
! grep -rq calc_total --include='*.py' . || { echo 'calc_total still in code'; exit 1; }
python3 -m unittest discover -p 'test*.py'
"""
hidden = "hidden"
```

**Another language** (`07-js-durations`). Use whatever is installed. `node --test` needs no packages:

```toml
[verify]
cmd = "node --test duration.hidden.test.js"
hidden = "hidden"
```

**A real repository at a pinned commit** (what `from-pr` writes). Dependencies are installed by `setup`, which runs
before the agent starts and has network access. Grading runs offline:

```toml
[repo]
git = "https://github.com/owner/repo.git"
rev = "0123456789abcdef0123456789abcdef01234567"     # required: results must be reproducible
setup = "uv venv -q .venv && uv pip install -q --python .venv/bin/python pytest -e ."

[verify]
cmd = ".venv/bin/python -m pytest -q tests/test_feature.py"
hidden = "hidden"
```

What `setup` creates, such as `.venv` or `node_modules`, never counts as the agent's change.

**Code review** (`11-review-catalog`, `12-review-members-db`). `repo.patch` is committed on top of the starting code
as "the change under review". The agent writes `REVIEW.md` with one finding per line, as `path:line: text`. A finding
matches an `[[expect]]` entry when it names the same file, falls within 3 lines of `lines`, and mentions one of the
`any` words if any are given. The score is the share of expected findings that were found. A review padded with many
unrelated findings does not pass.

```toml
prompt = "Review the change in the last commit (HEAD~1..HEAD) for bugs. ... Write your findings to REVIEW.md ..."

[repo]
path = "fixture"
patch = "change.diff"

[verify]
cmd = "review"

[[expect]]
file = "shop/catalog.py"
lines = [37, 37]
any = ["off-by-one", "page - 1", "first page", "skip"]
```

Keep expected findings at least 7 lines apart within a file, so that one finding can't match two of them. A review
task's `solution/REVIEW.md` lists the expected findings.

**Partial credit.** If the last line `verify.cmd` prints is `{"score": 0.6}`, that is the trial's score. The exit code
still decides pass or fail.

## Rules the loader enforces

- Unknown keys are errors, reported as `file: key: problem`.
- `repo` has exactly one of `path` or `git`, and `git` needs `rev`.
- A review task needs at least one `[[expect]]`.
- A task may not contain a `.env` file, because some harnesses load it as credentials.
- Keep instruction files (`CLAUDE.md`, `AGENTS.md`, ...) inside the task's repo if the task needs them. hev refuses to
  run when one sits above its work folder.
