"""Tests for seeding a new puzzle's solution from a template.

The rendered puzzle description is pasted into a block comment in a language this code does not
know, so the risks are that it silently *ends* that comment, that it presents an example the
program will never actually be fed, or that a mistyped `--template` is quietly ignored.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codingame_tools.puzzle_manager.solution_template import (
    PUZZLE_DETAILS_TOKEN,
    CgTemplateError,
    comment_safe,
    expand_template,
    parse_search_path,
    render_puzzle_details,
    resolve_template,
)
from codingame_tools.puzzle_manager.statement_render import CgStatementBlock


def _blocks(*pairs: tuple[str, str]) -> list[CgStatementBlock]:
    return [CgStatementBlock(kind=k, text=t) for k, t in pairs]


class TestCommentSafety:
    """A statement is arbitrary prose. If it contains a block-comment terminator, pasting it into
       one ends the comment early and leaves the rest of the description parsed as code."""

    def test_a_c_terminator_is_defused(self) -> None:
        assert "*/" not in comment_safe("divide a/b then b*/c")

    def test_a_docstring_terminator_is_defused(self) -> None:
        rendered = comment_safe('the marker """ ends it')
        assert '"""' not in rendered

    def test_single_quote_docstrings_too(self) -> None:
        assert "'''" not in comment_safe("the marker ''' ends it")

    def test_longer_runs_are_also_defused(self) -> None:
        """Replacing only exact triples would leave `\"\"\"\"` still containing one."""
        assert '"""' not in comment_safe('""""""')

    def test_ordinary_quoting_survives(self) -> None:
        assert comment_safe('say "hello" and it\'s fine') == 'say "hello" and it\'s fine'

    def test_it_is_idempotent(self) -> None:
        once = comment_safe('a """ b */ c')
        assert comment_safe(once) == once


class TestRendering:
    def test_sections_and_example(self) -> None:
        rendered = render_puzzle_details(
                _blocks(("header", "The Goal"), ("text", "Find the closest to zero."),
                        ("header", "Input"), ("text", "Line 1: N")),
                title="Temperatures", example_input="5\n1 -2", example_output="1")
        assert rendered.startswith("Temperatures\n===========")
        assert "The Goal" in rendered and "Find the closest to zero." in rendered
        assert "Example" in rendered and "Input:" in rendered and "5\n1 -2" in rendered
        assert "Output:" in rendered and rendered.rstrip().endswith("1")

    def test_the_statements_own_example_is_replaced_by_the_real_one(self) -> None:
        """A statement's example is prose and may be abbreviated; the test case is what the puzzle
           will actually feed. Keeping both would show two conflicting examples."""
        rendered = render_puzzle_details(
                _blocks(("header", "Example"), ("example_input", "STATEMENT INPUT"),
                        ("example_output", "STATEMENT OUTPUT")),
                example_input="REAL INPUT", example_output="REAL OUTPUT")
        assert "STATEMENT INPUT" not in rendered
        assert "REAL INPUT" in rendered
        assert rendered.count("Example") == 1

    def test_an_interactive_puzzle_shows_no_worked_example(self) -> None:
        """Its stored test case is the referee's world configuration, not the input the program
           reads -- printing it under "Input:" would be a worked example of something that never
           happens."""
        rendered = render_puzzle_details(
                _blocks(("header", "The Goal"), ("text", "Shoot the closest enemy.")),
                example_input="15\nZap 6 160 20", example_output="", interactive=True)
        assert "15\nZap 6 160 20" not in rendered
        assert "interactive" in rendered

    def test_an_optimization_puzzle_says_why_there_is_no_output(self) -> None:
        rendered = render_puzzle_details(
                _blocks(("header", "The Goal")), example_input="5", example_output="")
        assert "scored by a referee" in rendered

    def test_no_example_when_there_is_no_test_case(self) -> None:
        rendered = render_puzzle_details(_blocks(("header", "The Goal"), ("text", "Do it.")))
        assert "Example" not in rendered

    def test_the_rendering_is_comment_safe(self) -> None:
        rendered = render_puzzle_details(
                _blocks(("header", "The Goal"), ("text", 'compute a*/b and quote """ it')))
        assert "*/" not in rendered and '"""' not in rendered


class TestExpansion:
    def test_token_is_replaced(self) -> None:
        assert expand_template(f"before\n{PUZZLE_DETAILS_TOKEN}\nafter", "X\nY") == "before\nX\nY\nafter"

    def test_indentation_is_preserved(self) -> None:
        """A token inside an indented block comment should produce indented text, not text jammed
           against the margin."""
        out = expand_template(f"    {PUZZLE_DETAILS_TOKEN}", "one\ntwo")
        assert out == "    one\n    two"

    def test_blank_lines_are_not_given_trailing_whitespace(self) -> None:
        out = expand_template(f"    {PUZZLE_DETAILS_TOKEN}", "one\n\ntwo")
        assert "    \n" not in out

    def test_a_template_without_the_token_is_used_verbatim(self) -> None:
        """Seeding boilerplate with no puzzle text is a legitimate use."""
        assert expand_template("import sys\n", "DETAILS") == "import sys\n"

    def test_dollar_and_braces_in_your_code_are_untouched(self) -> None:
        """Not `string.Template` or `format`: a template is source code, which is full of `$` and
           `{}` that must survive."""
        code = 'let x = ${not_a_token}; printf("%d\\n", ${y});\n'
        assert expand_template(code, "D") == code


class TestResolution:
    def test_a_path_is_used_as_given(self, tmp_path: Path) -> None:
        target = tmp_path / "mine.py"
        target.write_text("x")
        assert resolve_template(str(target), [], extension="py") == target

    def test_a_bare_name_is_searched(self, tmp_path: Path) -> None:
        (tmp_path / "t.py").write_text("x")
        assert resolve_template("t.py", [tmp_path], extension="py") == tmp_path / "t.py"

    def test_search_order_is_respected(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        first.mkdir()
        second.mkdir()
        (first / "t.py").write_text("first")
        (second / "t.py").write_text("second")
        assert resolve_template("t.py", [first, second], extension="py") == first / "t.py"

    def test_default_lookup_uses_the_language_extension(self, tmp_path: Path) -> None:
        (tmp_path / "solution.rs").write_text("x")
        assert resolve_template(None, [tmp_path], extension="rs") == tmp_path / "solution.rs"

    def test_default_lookup_finding_nothing_is_not_an_error(self, tmp_path: Path) -> None:
        """No template for this language just means the placeholder is used."""
        assert resolve_template(None, [tmp_path], extension="rs") is None

    def test_a_named_template_that_is_missing_is_an_error(self, tmp_path: Path) -> None:
        """Asymmetric with the default on purpose: naming a template that does not exist is a
           typo worth stopping for, and silently ignoring it would seed the placeholder instead."""
        with pytest.raises(CgTemplateError, match="not found"):
            resolve_template("absent.py", [tmp_path], extension="py")

    def test_a_missing_explicit_path_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(CgTemplateError, match="does not exist"):
            resolve_template(str(tmp_path / "gone.py"), [], extension="py")

    def test_a_bare_name_with_no_search_path_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(CgTemplateError, match="nowhere to look"):
            resolve_template("t.py", [], extension="py")


class TestSearchPathParsing:
    def test_separator_delimited(self) -> None:
        parsed = parse_search_path([f"/a{os.pathsep}/b"])
        assert [str(p) for p in parsed] == ["/a", "/b"]

    def test_repeated_options_accumulate_in_order(self) -> None:
        assert [str(p) for p in parse_search_path(["/a", "/b"])] == ["/a", "/b"]

    def test_empty_entries_are_dropped(self) -> None:
        assert parse_search_path([f"/a{os.pathsep}{os.pathsep}"]) == [Path("/a")]

    def test_no_option_is_an_empty_path(self) -> None:
        assert parse_search_path(None) == []


class TestSetLanguageTemplating:
    """`cg puzzle set solution-language` seeds from a template on the same terms as `import`.

       Switching language is the other moment a solution file is created from nothing, so having
       one of the two honour templates and not the other would be an arbitrary split."""

    def test_the_target_language_chooses_the_default_template(self, tmp_path: Path) -> None:
        """The default lookup must use the language being switched *to*. Using the current one
           would seed a Rust file from solution.py -- silently, since a template is just text."""
        (tmp_path / "solution.rs").write_text("// rust")
        (tmp_path / "solution.py").write_text("# python")

        assert resolve_template(None, [tmp_path], extension="rs") == tmp_path / "solution.rs"

    def test_no_template_for_the_target_language_falls_back(self, tmp_path: Path) -> None:
        """A directory of per-language templates need not cover every language; the ones it misses
           get the built-in placeholder rather than an error."""
        (tmp_path / "solution.py").write_text("# python")

        assert resolve_template(None, [tmp_path], extension="go") is None

    def test_a_named_template_is_still_required_to_exist(self, tmp_path: Path) -> None:
        with pytest.raises(CgTemplateError):
            resolve_template("absent.rs", [tmp_path], extension="rs")


class TestCgUrlToken:
    """`${CG_URL}` puts the puzzle's IDE link in the file you're editing, so the statement,
       leaderboard and "Submit" button are one click away from your solution."""

    def test_the_url_shape(self) -> None:
        from codingame_tools.puzzle_manager.solution_template import puzzle_ide_url
        assert puzzle_ide_url("temperatures") == "https://www.codingame.com/ide/puzzle/temperatures"

    def test_the_host_comes_from_the_client(self) -> None:
        """Built from the client's own base URL rather than a second copy of the host, so the two
           cannot drift."""
        from codingame_tools.client.common.raw_client import CgRawClient
        from codingame_tools.puzzle_manager.solution_template import puzzle_ide_url
        assert puzzle_ide_url("x").startswith(CgRawClient.CODINGAME_BASE_URL + "/")

    def test_it_is_substituted(self) -> None:
        from codingame_tools.puzzle_manager.solution_template import CG_URL_TOKEN
        out = expand_template(f"// {CG_URL_TOKEN}\ncode\n", "D", variables={CG_URL_TOKEN: "URL"})
        assert out == "// URL\ncode\n"

    def test_it_works_alongside_puzzle_details(self) -> None:
        from codingame_tools.puzzle_manager.solution_template import CG_URL_TOKEN
        template = f"// {CG_URL_TOKEN}\n\"\"\"\n{PUZZLE_DETAILS_TOKEN}\n\"\"\"\n"
        out = expand_template(template, "line one\nline two", variables={CG_URL_TOKEN: "URL"})
        assert out == '// URL\n"""\nline one\nline two\n"""\n'

    def test_a_template_using_only_the_url_still_works(self) -> None:
        """The details token is optional; a template may want just the link."""
        from codingame_tools.puzzle_manager.solution_template import CG_URL_TOKEN
        assert expand_template(f"// {CG_URL_TOKEN}\n", "D", variables={CG_URL_TOKEN: "URL"}) == "// URL\n"

    def test_variables_do_not_reach_into_the_rendered_puzzle_text(self) -> None:
        """Substituted on the template alone. A statement that happens to contain the token is
           puzzle prose, and must survive exactly as written."""
        from codingame_tools.puzzle_manager.solution_template import CG_URL_TOKEN
        # A value that is not a substring of the token itself, so the negative assertion means
        # what it says.
        out = expand_template(f"{PUZZLE_DETAILS_TOKEN}\n", f"prose mentioning {CG_URL_TOKEN}",
                              variables={CG_URL_TOKEN: "REPLACED"})
        assert CG_URL_TOKEN in out
        assert "REPLACED" not in out

    def test_code_that_looks_like_a_token_is_untouched(self) -> None:
        from codingame_tools.puzzle_manager.solution_template import CG_URL_TOKEN
        code = "let x = ${CG_URLS}; // not the token\n"
        assert expand_template(code, "D", variables={CG_URL_TOKEN: "URL"}) == code


class TestDefaultLanguageTemplateLookup:
    """`cg puzzle import PUZZLE` with no --language must still find `solution.<ext>`.

       It did not: the CLI computed the extension only when --language was given, so the default
       lookup was asked for `extension=None` and returned nothing. Every plain
       `cg puzzle import PUZZLE` on a never-attempted puzzle therefore fell back to the one-line
       placeholder while a perfectly good template sat in the search path -- silently, since a
       missing default template is not an error.
    """

    def test_no_language_still_resolves_the_default_template(self, tmp_path: Path) -> None:
        from codingame_tools.cli.main import _template_extension_for

        extension = _template_extension_for(None)
        (tmp_path / f"solution.{extension}").write_text("template")

        assert resolve_template(None, [tmp_path], extension=extension) == tmp_path / f"solution.{extension}"

    def test_the_cli_never_asks_for_a_null_extension(self, tmp_path: Path) -> None:
        """The bug itself: with --language absent the CLI passed None, and the lookup then found
           nothing however many templates were in the path."""
        from codingame_tools.cli.main import _template_extension_for

        assert _template_extension_for(None) is not None

    def test_an_explicit_language_still_wins(self) -> None:
        from codingame_tools.cli.main import _template_extension_for
        from codingame_tools.language import get_language

        assert _template_extension_for("Rust") == get_language("Rust").extension

    def test_a_none_extension_finds_nothing(self, tmp_path: Path) -> None:
        """The behaviour that made the bug silent, pinned so the fix is not undone by 'simplifying'
           the caller back to passing None."""
        (tmp_path / "solution.py").write_text("template")

        assert resolve_template(None, [tmp_path], extension=None) is None

    def test_the_default_import_language_is_one_cg_knows(self) -> None:
        """The lookup asks it for an extension, so an unknown language would raise rather than
           fall back."""
        from codingame_tools.language import get_language, list_language_cg_ids
        from codingame_tools.puzzle_manager import DEFAULT_IMPORT_LANGUAGE

        assert DEFAULT_IMPORT_LANGUAGE in list_language_cg_ids()
        assert get_language(DEFAULT_IMPORT_LANGUAGE).extension
