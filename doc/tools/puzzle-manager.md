# Puzzle manager

`CgPuzzleManager` owns one puzzle working directory. Deliberately much simpler than the contribution
manager: exactly one file is ever editable, so there is no git repository and no merge machinery.

```python
from pathlib import Path
from codingame_tools.client import CgClient
from codingame_tools.puzzle_manager import CgPuzzleManager

async with CgClient() as client:
    manager = CgPuzzleManager(Path("./puzzle"), client)
    puzzle_data = await manager.import_("temperatures")
    print(puzzle_data.solution_language)

    for result in await manager.play():
        print(result.index, "PASS" if result.passed else "FAIL")

    report = await manager.submit()
    print(report.score)
```

## Lifecycle

| | |
| --- | --- |
| `import_(puzzle_ref, *, language=None)` | Resolve a puzzle, start/resume its test session, write the working directory. Restores your saved answer if there is one. |
| `repair()` | Rebuild `.meta/` from `puzzle.json`'s `puzzle_id`. Never touches `data/`. |
| `status(refresh=False)` | Title, language, local-edit state; with `refresh`, live progress and a server comparison. |
| `delete()` | Remove the local directory. There is no server-side puzzle to delete. |

`puzzle_id` is the stable identity that makes `repair()` possible after a fresh clone, since
`.meta/` is gitignored.

## Running

| | |
| --- | --- |
| `play(test_indices=None)` | Run locally against `.meta/tests/`. No network at all. |
| `play_remote(...)` | Run server-side. **Durably saves your code as a side effect.** |
| `submit()` | A real graded submission. Returns the `CgSubmissionReport`. |
| `build(profile="run")` | Compile without running. |

`play()` compares output using the same rule CodinGame does, so a local pass predicts a remote one —
see [output comparison](../design/final-newlines.md#output-comparison).

## Language switching

```python
result = await manager.set_language("C++", force=False)
print(result.previous_language, "->", result.language, "from server:", result.from_server)
```

Needs the network despite only changing local state: CodinGame keeps your latest source per
language, so switching restores your own previous work rather than generating a stub. `from_server`
tells you which happened, which is otherwise invisible.

It refuses when `data/solution.<ext>` holds work the server doesn't have. "Safe to discard" means the
local text matches either the server's saved code for the current language, or the snapshot of the
stub `cg` itself wrote — recorded in `.meta/` precisely so this doesn't depend on stub generation
being byte-stable across releases.

## Debugging

```python
session = await manager.start_debug_session(test_index=1)
print(session.details["address"])
await manager.stop_debug_session()
```

Only meaningful for languages whose debugger attaches to a running target (C++ via gdbserver).
Python's debugger launches the program itself and never calls this. `provision_vscode()` writes the
configuration that drives it.

## Test cases are server bytes

`.meta/tests/` holds byte-exact `fileservlet` downloads. They're read-only, never pushed, and
already exactly what CodinGame puts on a solution's stdin — so nothing converts them on the way in
or out. This is the opposite of the contribution manager's handling, and the difference is load
bearing: see [final newlines](../design/final-newlines.md).
