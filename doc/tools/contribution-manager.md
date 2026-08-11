# Contribution manager

`CgContributionManager` owns one contribution working directory. Unlike a puzzle, a contribution has
many editable files that can each change on the server while you're working, so it's backed by a
real git repository and has a real merge workflow.

```python
from pathlib import Path
from codingame_tools.client import CgClient
from codingame_tools.contribution_manager import CgContributionManager

async with CgClient() as client:
    manager = CgContributionManager(Path("./contribution"), client)
    await manager.import_("1493730...")
    for result in await manager.play():
        print(result.ordinal, result.side, "PASS" if result.passed else "FAIL")
    await manager.push()
```

## Lifecycle

| | |
| --- | --- |
| `create(title=...)` | A brand new, **purely local** working directory. No network, nothing server-side yet. |
| `import_(handle, ...)` | Build from an existing server-side contribution, cover image included. |
| `repair()` | Rebuild the git-dir from scratch without disturbing `data/`. |
| `push()` | Send content to the server, then advance the internal branches. |
| `delete()` | Delete from the server — unrecoverable — and by default remove the directory. |

`create()` being local-only is the useful property: you can start a contribution, change your mind,
and delete the directory having published nothing. The first `push()` then safely two-steps into
`createContribution`.

## The three branches

`.meta/`'s git repo carries more than your work:

| | |
| --- | --- |
| `main` | your working tree — what you're editing |
| `server` | the server's current content, as last fetched |
| `version-data` | an orphan branch with one commit per server version, holding the full redacted `CgContribution` |

Three branches are what make a genuine three-way merge possible: your edits, the server's, and a
common ancestor.

**None of it is a backup.** `.meta/` is gitignored, disposable, and rebuilt by `repair()`. The only
durable copy of a contribution's solution is on the server, which stores exactly one with no history
— every `push()` overwrites it.

## Syncing

| | |
| --- | --- |
| `fetch()` | Update `server`/`version-data`. Touches nothing you're editing. |
| `rebase()` | Reconcile when unambiguous: no-op if the server hasn't moved, fast-forward if you have no local edits. |
| `merge_start()` / `merge_continue()` / `merge_abort()` | A real `git merge` against the working tree, when it is ambiguous. |
| `discard_local()` | Reset to `server`'s tip exactly. |

`merge_continue()` refuses if a file still contains conflict markers.

## Running tests

```python
for test_case in manager.list_local_tests():
    result = await manager.run_local_test(test_case, "Python3")
```

Entirely local. Worth doing before every push: `updateContribution` validates the reference solution
against **every** test case server-side and rejects the whole push if any disagree.

## The empty-solution rule

A zero-length `data/solution.<ext>` means "no reference solution" and is pushed as a null
`solutionSource`. This isn't a quirk — it's required. `updateContribution` skips solution validation
entirely when the solution is null but validates any non-null one against every test case, so a
language with no working stub must leave the file empty rather than write a placeholder that would
fail validation and block the push.

## Test cases are rendered strings

`data/tests/` holds files this client renders from strings the server stores, with a final newline
added that is **not** part of the value. Everything that reads them converts back — `play`, the
debugger, and `push`. Getting that wrong is a one-byte divergence that line-oriented solutions don't
notice; see [final newlines](../design/final-newlines.md).
