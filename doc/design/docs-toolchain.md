# The documentation toolchain

The site is built with **[ProperDocs]**, not MkDocs. This note records why, and what would have to
be true for that to change — because "which static site generator" turned into a real decision in
2026 and the answer is likely to move again.

## Why not MkDocs 2.0

MkDocs 2.0 **removes the plugin system**, with no migration path offered.

For most projects that is disruptive. For this one it is terminal: the documentation *is* plugins.

| Plugin | What it does here |
| --- | --- |
| `mkdocstrings` | Generates the entire API reference from source |
| `mkdocs-gen-files` | Runs [`scripts/gen_api_pages.py`](https://github.com/mckelvie-org/codingame-tools/blob/main/scripts/gen_api_pages.py), which emits 125 pages |
| `mkdocs-literate-nav` | Builds the API navigation from the generated `SUMMARY.md` |
| `mkdocs-autorefs` | Turns backticked references inside docstrings into real cross-page links |
| `mkdocs-material` | The theme |
| `mike` | Versioned deploys to GitHub Pages |

Migrating to 2.0 would not be an upgrade path; it would mean deleting the generated API reference and
having nothing to replace it with. So there is no "migrate early to reduce risk" option — the
destination does not exist.

## Why ProperDocs

ProperDocs is a fork of MkDocs 1.x that intends to stay a fork: bug fixes and incremental features,
no breaking changes. Three things made it the choice rather than a gamble:

1. **It already builds this site unchanged** — `properdocs build --strict` against the existing
   `mkdocs.yml` produces the same 178 pages as MkDocs 1.6.1, exit 0. No config was renamed; reading
   `mkdocs.yml` is deliberate on its part.
2. **The plugin ecosystem went first.** `mkdocs-gen-files` and `mkdocs-literate-nav` already
   *require* `properdocs>=1.6.5` and cap `mkdocs<=1.6.1` themselves. It arrived in this venv as a
   transitive dependency before it was ever chosen.
3. **`mike` already prefers it** — it detects ProperDocs and uses it as the builder automatically,
   while still finding `mkdocs.yml`. The deploy workflow needed no change at all.

`mkdocs` remains in the dependency group only because those plugins still import it, capped `<2` so
a future relock cannot cross the major version underneath us.

## Zensical, and when to reconsider

[Zensical] is the other successor, from the author of Material for MkDocs — a new Rust-backed
generator rather than a fork. It is the likelier long-term home, since that is where the theme is
going, and it is dramatically faster (0.42s versus 7s for this site).

**It is not usable for this project yet.** Tested at 0.0.54:

```
zensical build --strict -f mkdocs.yml     # → "No issues found", 53 pages
properdocs build --strict -f mkdocs.yml   # → 178 pages
```

The 125-page gap is the whole generated API reference: Zensical does not run `mkdocs-gen-files`, so
`gen_api_pages.py` never executes and `api/` is not created. The failure is silent — `--strict`
reports **"No issues found"** while dropping it, which is worse than an error, because CI would go
green on a site with no API documentation.

Note that `zensical build` also requires `site_dir` to be inside the project root, and resolves
`docs_dir` relative to the config file.

### Re-test recipe

Zensical moves fast — 0.0.11 to 0.0.54 in the months around this decision, and mkdocstrings
cross-reference support landed in that window. Re-check with:

```bash
python3 -m venv /tmp/zen && /tmp/zen/bin/pip install -q \
    zensical 'mkdocstrings[python]' mkdocs-gen-files mkdocs-literate-nav
sed 's|^site_dir:.*|site_dir: site-zentest|' mkdocs.yml > .zen-test.yml
/tmp/zen/bin/zensical build --strict -f .zen-test.yml
find site-zentest/api -name '*.html' | wc -l     # must be ~125, not 0
rm -rf site-zentest .zen-test.yml
```

**Page count is the test, not exit status.** A clean build proves nothing here.

Switch when that count matches, and when `mike` (or a Zensical equivalent) can still do versioned
GitHub Pages deploys — see [`.github/workflows/docs.yml`](https://github.com/mckelvie-org/codingame-tools/blob/main/.github/workflows/docs.yml).

[ProperDocs]: https://properdocs.org/
[Zensical]: https://zensical.org/
