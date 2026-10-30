# Writing tasks

A task is a folder in `tasks/` with a `task.toml`. The agent gets the `prompt` in a fresh copy of the starting repo.
Its changes are applied to another fresh copy, `hidden/` is added, and `verify.cmd` runs offline; exit 0 means solved.

```sh
./hev task from-pr https://github.com/owner/repo/pull/123            # the repo before the PR; the PR is the solution
./hev task from-commit ~/code/myrepo 1a2b3c4                         # the same from a local commit (copied, not uploaded)
./hev task from-pr https://github.com/owner/repo/pull/123 --review   # a code-review task instead
./hev task new my-task                                               # three questions, then a commented task.toml
```

Each ends by checking the task (starting code fails, solution passes 3 times); redo with `./hev check-task my-task`.

## One example per kind

**Fix or implement, with hidden tests** (`01-fix-slugify`, `02-implement-lru`). `hidden/` overwrites any copy the
agent wrote. `fixture/` is the starting code; `solution/` (laid over it) or `solution.diff` is replayed by the $0
scripted model.
```toml
prompt = "The function `slugify` in `textutil.py` is buggy: ... Fix it."
[verify]
cmd = "python3 -m unittest test_slugify"
hidden = "hidden"            # tasks/<name>/hidden/test_slugify.py
```

**Check the code itself, not only tests** (`03-rename-refactor`). Any shell works:
```toml
[verify]
cmd = """
! grep -rq calc_total --include='*.py' . || { echo 'calc_total still in code'; exit 1; }
python3 -m unittest discover -p 'test*.py'
"""
hidden = "hidden"
```

**Another language** (`07-js-durations`). Same shape; use whatever is installed, e.g. `cmd = "node --test duration.hidden.test.js"`.

**A real repository at a pinned commit** (what `from-pr` writes). `setup` runs before the agent, with network;
grading is offline. What `setup` creates is never part of the agent's change.
```toml
[repo]
git = "https://github.com/owner/repo.git"
rev = "0123456789abcdef0123456789abcdef01234567"     # required: results must be reproducible
setup = "uv venv -q .venv && uv pip install -q --python .venv/bin/python pytest -e ."
[verify]
cmd = ".venv/bin/python -m pytest -q tests/test_feature.py"
hidden = "hidden"
```

**Code review** (`11-review-catalog`, `12-review-members-db`). `repo.patch` is committed on top as "the change under
review". The agent writes `REVIEW.md`, one `path:line: text` per finding. A finding matches an `[[expect]]` when it
names the same file, is within 3 lines of `lines`, and mentions an `any` word if given. The score is the share of
expected findings found; a padded review fails. Keep expected findings at least 7 lines apart, so one finding can't
match two. `solution/REVIEW.md` lists them.
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

**Partial credit.** If the last line `verify.cmd` prints is `{"score": 0.6}`, that is the score; the exit code still
decides pass or fail.

Keep instruction files (`CLAUDE.md`, `AGENTS.md`, ...) inside the task's repo; hev refuses to run if one sits above it.
