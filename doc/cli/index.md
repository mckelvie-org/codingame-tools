# The `cg` CLI

Everything this project does is exposed as a command. If you're here to get something done, you
almost certainly want one of the two workflow guides.

- **[Solving puzzles](puzzles.md)** — import, edit, test, submit.
- **[Authoring contributions](contributions.md)** — create or import, edit, validate, push, and
  merge when the server moves under you.
- **[Debugging](debugging.md)** — VS Code integration, breakpoints in your solution, containerised
  toolchains.
- **[Command reference](reference/index.md)** — 162 commands, generated from the parser.

## First run

```bash
cg login
cg whoami
```

See [authentication](../concepts/authentication.md) if the browser flow isn't an option, and
[profiles](../concepts/profiles.md) if you want more than one identity or somewhere safe to
experiment.

## Shape of the command set

| Group | What it's for |
| --- | --- |
| [`cg puzzle`](reference/puzzle.md) | Solve an existing CodinGame puzzle locally. |
| [`cg contribution`](reference/contribution.md) | Author and maintain your own contributions. |
| [`cg config`](reference/config.md), [`cg settings`](reference/settings.md) | Configuration you edit, and state the app remembers. |
| [`cg topics`](reference/top-level.md) | Search the catalogue of puzzle topics a contribution can be tagged with. |
| [`cg docker`](reference/docker.md) | Manage the container toolchain image and the containers built from it. |
| [`cg api`](reference/api/index.md) | Thin wrappers over CodinGame's own service endpoints, one subcommand per method. |
| [`cg api-helper`](reference/api-helper.md) | The same endpoints with retries and polling layered on. |
| [`cg raw-api`](reference/raw-api.md) | Send a raw JSON request to any endpoint. |

The `api` group is worth knowing about even if you never use it: it's how the protocol was mapped,
and it's the fastest way to check what the server actually returns for something.

```bash
cg api contribution get-all-pending-contributions | jq '.[0]'
```

## Global options

These go **before** the command:

```bash
cg --profile dev puzzle status
cg --json puzzle status
```

| | |
| --- | --- |
| `--profile NAME` | Which [profile](../concepts/profiles.md) to use. |
| `--json` | Machine-readable output, for commands that render text by default. |
| `--config PATH` | Use a specific `config.yaml`. |
| `--log-level LEVEL` | `DEBUG` is genuinely useful when the server misbehaves. |
| `--trace-http` | Dump the HTTP conversation. The first thing to reach for when a call fails inexplicably. |
| `--tb` | Full tracebacks instead of a one-line error. |

## Which commands touch the network

Worth knowing, because several are irreversible:

- **Entirely local, always:** `play`, `build`, `description`, `where`, `status` (without
  `--refresh`), `cg contribution set`, `cg contribution topic remove`, and the whole merge
  state machine.
- **Reads only:** `import`, `repair`, `diff`, `fetch`, `status --refresh`, `cg topics`, and
  `cg contribution topic add` (which needs the topic catalogue).
- **Writes something real:** `cg puzzle submit` (a permanent graded submission),
  `cg contribution push` (updates published content), `cg contribution delete` (unrecoverable), and
  `cg puzzle play-server` — which durably overwrites your saved code on the server as a side effect
  of running a test.

That last one surprises people. Running a server-side test saves your code, whether you meant it to
or not.
