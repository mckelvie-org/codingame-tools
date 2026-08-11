# Final newlines

The most-measured decision in the project, because the obvious implementation is subtly wrong and
loses user data.

## The problem

CodinGame stores a contribution's editable text — test-case inputs and outputs, statement,
constraints, descriptions, stub generator, solution — as plain **strings**, authored through
textarea controls on the web site. A textarea's value has no trailing newline unless the author
deliberately ended on a blank line, so nearly all of these strings end *without* one, and moderators
ask contributors to keep it that way.

That collides with how text files work. Editors with "insert final newline", git, and every POSIX
tool expect a file's last line to be terminated. Write the string verbatim and you get a file your
tooling immediately wants to change.

## The trap

The obvious fix is to add the newline only when it's missing. That cannot be inverted: on the way
back, the reader can't tell whether the newline it sees belonged to the content or to the writer.
Composed with the matching strip at submission time, **any value that genuinely ended in a newline
lost one per round trip** — no user edit involved, until it ran out:

```
'\n\n\n\n' -> '\n\n\n' -> '\n\n' -> '\n' -> '' -> ''
'a\n\n'    -> 'a\n'    -> 'a'    -> 'a'
```

## The fix

`codingame_tools.common.text_files`, unconditional in both directions:

```
server -> file    append "\n", unless the value is zero-length
file -> server    strip up to one trailing "\n"
```

`file_to_server_text(server_text_to_file(s)) == s` for every `s`, so an untouched fetch/push cycle is
exactly the identity. The zero-length carve-out is what keeps an empty server value a genuinely
empty file, which matters because a zero-length solution file is how "no reference solution" is
spelled.

## Why it mattered more than the rate suggests

Surveying 1686 real values — the pending community-review queue plus published community puzzles —
only 12 end in a newline. That looks like a rounding error until you notice the distribution: **10
of the 12 are every single input and output of one puzzle** whose author consistently terminated the
last line.

The habit is per-*author*, not per-test-case. So the broken scheme didn't nibble at 0.8% of values;
it eroded *every* test case of roughly 1 in 12 contributions, on every push. All 1686 now round trip
exactly, including across repeated cycles.

Two cases stayed at zero across all 1686: nothing ends in `"\n\n"`, and nothing is zero-length. So
the single point where this conversion isn't injective — an empty file and a file holding just a
terminator both mean the empty string — is theoretical. It's also useful: an editor with "insert
final newline" enabled can't quietly turn "no reference solution" into a one-blank-line program.

## Solution source is not an exception

It looks like one. Solution source comes from an embedded code editor rather than a textarea, and
ends in a newline 40% of the time against 3–5% for the prose fields. That reads like the editor
preserving a file terminator — which would argue for treating it differently.

It isn't. If the editor had file semantics, essentially *every* solution would end in a newline,
because source files do. 40% is authors leaving a blank line, or pasting from an external editor and
bringing its terminator along as one. The sample shows it directly: one C# solution ends `}` with no
newline at all, which no file-semantics editor produces.

So a trailing newline on solution source means what it means everywhere else — an extra blank line —
and the solution file will show that blank line for the ~40% of solutions carrying one. That's the file
being honest.

## Puzzles are different, and must stay different

Puzzle test cases under `.meta/tests/` are **not** converted, and that asymmetry is deliberate.

For a puzzle, the server-side artifact is *already a file*, served byte-for-byte through
`fileservlet`. For a contribution, the server holds a *string* and the file is this client's
rendering of it. The conversion bridges string → file; there is no gap to bridge when it's already a
file.

Confirmed live rather than assumed: a probe solution reading `sys.stdin.buffer.read()` on a
community puzzle whose stored input is the single unterminated byte `"7"` reported `bytes=1
repr=b'7'`. **CodinGame's runner appends nothing.** So an unterminated final line of stdin is real
and solutions have to cope — and adding a terminator locally would hand a local run one byte more
than the same test gets remotely.

Incidentally, unterminated test files are a *community-contribution* phenomenon. Official CodinGame
puzzles' test files are properly terminated, consistent with staff authoring them as files rather
than typing them into a textarea.

### Every path that feeds stdin has to convert

Three of them exist, and all three had to be fixed:

| | |
| --- | --- |
| `play` | passes the decoded value to the runner |
| `run_debug_stdin` | takes `final_newline_added`, set by the contribution wrapper only |
| `CgLanguage.start_debug_session` | takes `stdin_text: str`, and writes its own copy to redirect from |

The last is the trap. Redirecting the container's stdin straight from the test-case file looks
obviously right — the working directory is already mounted — and it cannot be made correct inside
the plugin, because the same file is a *rendering* for contributions and the *value* for puzzles.
Only the caller knows which, so the caller supplies the bytes.

## Output comparison

The other half: how strictly local test results are compared.

`outputs_match` reproduces CodinGame's own comparison rather than being independently lenient,
because a local pass that fails on submission is the worst outcome it can produce. Mapped live
against `CgPlayResult.comparison.success` across two puzzles — one whose stored expected output ends
in a newline, one whose doesn't, since the rule only falls out of seeing both:

| actual, relative to the stored expected output | server |
| --- | --- |
| verbatim | pass |
| ± one trailing newline | pass |
| ± two trailing newlines | fail |
| trailing whitespace added to every line | fail |
| per-line trailing whitespace stripped | fail |
| leading space added to every line | fail |
| CRLF line endings | fail |
| a leading blank line | fail |

**Everything is exact except a difference of one trailing newline in either direction.** That single
allowance isn't optional: expected outputs usually have no final newline while every language's
`print` supplies one, so a byte-exact comparison would fail essentially every test.

The tolerance is a *difference*, not a cap — `expected + "\n\n"` fails even when the expected value
itself ends in a newline.

Note especially that **trailing whitespace and CRLF are not forgiven**. An earlier version of this
function normalized both away and so accepted output the server rejects, which is the failure
direction that actually hurts.

## Where this lives

- `codingame_tools/common/text_files.py` — the conversion, with the full rationale.
- `codingame_tools/test_runner/runner.py` — `outputs_match`, with the measured table.
- `tests/test_text_files.py`, `tests/test_test_runner_runner.py` — the tables as assertions.

Measurements date from 2026-08-03. If any of this needs to change, **re-measure rather than
reason** — every wrong turn recorded above came from reasoning about what the server ought to do.
