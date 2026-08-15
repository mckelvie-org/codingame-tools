"""Generate the API reference tree at docs build time, one page per module.

Run by the `gen-files` mkdocs plugin, not by hand. Each page is a `:::` directive that mkdocstrings
expands from the source, so nothing here is committed and nothing can go stale: the reference is
built from the package that is actually being released.

Deliberately one page per *module* rather than one enormous page per area. There are 19 protocol
schema modules and 19 service modules; concatenated they would be unreadable, and--more
importantly--every cross-reference would land on the same page, so `autorefs` links would stop
telling you where a symbol lives.

The counterpart to the CLI reference under `doc/cli/reference/`, which is generated *ahead* of time
and committed because it has to be readable on GitHub. This one is not: it exists to be
cross-linked, which only works once mkdocstrings and autorefs have resolved 643 references that are
plain backticked text in the source.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

PACKAGE = "codingame_tools"

AREAS = {
    "protocol": (
        "client/common/protocol",
        "Protocol",
        "The JSON-serializable dataclasses that mirror CodinGame's own API payloads. These describe "
        "the wire format: what the server sends, what it accepts, and which fields are optional "
        "because the server genuinely omits them.",
    ),
    "services": (
        "client/service",
        "Client services",
        "One method per CodinGame API endpoint, plus the helpers that layer retries and polling on "
        "top. These speak the protocol directly and do no bookkeeping.",
    ),
    "client": (
        "client/common",
        "Client core",
        "Transport, authentication, credential storage and the raw request machinery the service "
        "wrappers are built on.",
    ),
    "contribution-manager": (
        "contribution_manager",
        "Contribution manager",
        "Authoring and maintaining a contribution: the working directory, the git repository "
        "backing `data/`, and the fetch/merge/push state machine.",
    ),
    "puzzle-manager": (
        "puzzle_manager",
        "Puzzle manager",
        "Solving an existing puzzle: the working directory, local test execution, and the "
        "per-language server-side code CodinGame keeps for you.",
    ),
    "language": (
        "language",
        "Languages and toolchains",
        "Per-language build, run and debug behaviour, and the composable container toolchain that "
        "makes it work without a local compiler.",
    ),
}

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = Path("api")

# `client/common/protocol` and `client/service` are carved out of `client/common` and `client` above,
# so the broader areas must not re-document them.
CARVED_OUT = ("client/common/protocol", "client/service")


def _module_path(source: Path) -> str:
    """Dotted module path for a source file, e.g. `codingame_tools.puzzle_manager.manager`."""
    parts = source.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _modules_under(subdir: str) -> list[Path]:
    """Every documentable module under a subdirectory, excluding private ones and carve-outs."""
    found = []
    for source in sorted((ROOT / PACKAGE / subdir).rglob("*.py")):
        relative = source.relative_to(ROOT / PACKAGE).as_posix()
        if any(part.startswith("_") and part != "__init__.py" for part in relative.split("/")):
            continue  # private module or package
        if subdir not in CARVED_OUT and any(relative.startswith(c) for c in CARVED_OUT):
            continue  # documented by its own, more specific area
        found.append(source)
    return found


nav = mkdocs_gen_files.Nav()

# A landing page for `api/` itself. Without one the section exists only in the nav sidebar, so
# `.../api/` is a 404 -- which is exactly the URL README and doc/index.md send people to, and the
# natural thing to type. Written first so it sorts to the top of the generated nav.
overview = API_ROOT / "index.md"
with mkdocs_gen_files.open(overview, "w") as fd:
    fd.write("# API reference\n\n")
    fd.write("Generated from the source at build time, one page per module. Everything here is "
             "cross-linked: type names in signatures and backticked references inside docstrings "
             "resolve to the pages that define them.\n\n")
    fd.write("For the hand-written guides -- how to use the client, the managers and the CLI -- "
             "start from [the documentation home](../index.md).\n\n")
    for area_slug, (_subdir, area_title, area_blurb) in AREAS.items():
        fd.write(f"- **[{area_title}]({area_slug}/index.md)** — {area_blurb}\n")
nav[("Overview",)] = overview.relative_to(API_ROOT).as_posix()

for slug, (subdir, title, blurb) in AREAS.items():
    for source in _modules_under(subdir):
        module = _module_path(source)
        leaf = module.split(".")[-1] if not module.endswith(PACKAGE) else "index"
        page = API_ROOT / slug / f"{module}.md"

        with mkdocs_gen_files.open(page, "w") as fd:
            fd.write(f"# `{module}`\n\n::: {module}\n")
        mkdocs_gen_files.set_edit_path(page, Path("..") / source.relative_to(ROOT))
        # Relative to SUMMARY.md's own directory. `nav_file: SUMMARY.md` scopes literate-nav to
        # the directory it sits in, so these agree with how mkdocs resolves the file's links --
        # a docs-root-relative path satisfies the nav but makes the link checker look for api/api/.
        nav[(title, module)] = page.relative_to(API_ROOT).as_posix()

    index = API_ROOT / slug / "index.md"
    with mkdocs_gen_files.open(index, "w") as fd:
        fd.write(f"# {title}\n\n{blurb}\n")
    nav[(title,)] = index.relative_to(API_ROOT).as_posix()

with mkdocs_gen_files.open(API_ROOT / "SUMMARY.md", "w") as fd:
    fd.writelines(nav.build_literate_nav())
