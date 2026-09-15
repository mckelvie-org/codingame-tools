"""Tests for the two shapes `TestSession/play` returns.

A standard in/out puzzle reports one answer and a comparison against the expected output. An
interactive puzzle reports no answer at all -- it returns the whole game, frame by frame, already
played out and scored by a referee.

Both were reverse-engineered from live responses; the interactive one is captured here verbatim
from "Code vs Zombies" (2026-08-16), because nothing else pins a shape the server decides.
"""

from __future__ import annotations

import pytest

from codingame_tools.client.common.protocol.test_session import CgPlayResult

# Trimmed from a real response: two frames of a ten-turn run, keeping every field the server sent.
INTERACTIVE_RESPONSE = {
    "frames": [
        {
            "gameInformation": "Braaaaaiiiiiiiiiiiins...\n",
            "stderr": "Beginning turn #1\n",
            "view": " 0\nCode vs Zombies\n1\n8250 4500\n",
            "keyframe": True,
        },
        {
            "gameInformation": "¤GREEN¤You held off the wave of zombies and scored 10 "
                               "points.§GREEN§\nYou have destroyed zombie 0.\n",
            "stdout": "7893 4305\n",
            "stderr": "Playing move (7893, 4305)\n",
            "view": " 9 Win\n10\n",
            "keyframe": True,
        },
    ],
    "gameId": 899626173,
    "scores": [1.0],
    "metadata": {"score": 10.0},
}

STANDARD_RESPONSE = {
    "output": "5\n",
    "comparison": {"success": True, "expected": "5", "found": "5"},
}

FAILED_INTERACTIVE_RESPONSE = {
    "frames": [
        {
            "gameInformation": "Alright. Who wants some?\n",
            "keyframe": True,
            "error": {
                "message": "ModuleNotFoundError: No module named 'typing_extensions'",
                "stacktrace": [{"location": "ANSWER", "container": "Answer.py",
                                "function": " in <module>", "line": 11}],
            },
        },
        {
            "gameInformation": "¤RED¤Timeout: the program did not provide 1 input lines "
                               "in due time...§RED§\n",
            "stdout": "",
            "keyframe": True,
        },
    ],
    "gameId": 899625028,
    "scores": [0],
    "metadata": {"score": 0},
}


class TestInteractiveShape:
    def test_parses(self) -> None:
        """Before this shape was modelled, `output` and `comparison` were required, so every
           interactive response failed to parse outright."""
        result = CgPlayResult.from_dict(INTERACTIVE_RESPONSE)
        assert result.is_interactive
        assert len(result.frames) == 2
        assert result.game_id == 899626173
        assert result.scores == [1.0]

    def test_nothing_is_left_unmodelled(self) -> None:
        """Anything the server sends that we do not model lands silently in `extra_data`. For a
           shape this new, an empty catch-all is the evidence that it is fully described."""
        result = CgPlayResult.from_dict(INTERACTIVE_RESPONSE)
        assert result.extra_data == {}
        assert all(frame.extra_data == {} for frame in result.frames)

    def test_score_comes_from_metadata(self) -> None:
        """`scores` is per-player and normalized; `metadata.score` is the number the puzzle is
           actually ranked on--10 points here, not 1.0."""
        assert CgPlayResult.from_dict(INTERACTIVE_RESPONSE).score == 10.0

    def test_per_turn_streams_are_kept_apart(self) -> None:
        """CodinGame keeps each turn's stdout, stderr and referee narration separate, unlike a
           standard puzzle where they arrive interleaved in one `output` string."""
        frame = CgPlayResult.from_dict(INTERACTIVE_RESPONSE).frames[1]
        assert frame.stdout == "7893 4305\n"
        assert frame.stderr == "Playing move (7893, 4305)\n"
        assert "held off the wave" in (frame.game_information or "")

    def test_a_failure_is_reported_on_the_frame_it_happened_on(self) -> None:
        """There is no top-level `error` for an interactive run--the run stops at a turn."""
        result = CgPlayResult.from_dict(FAILED_INTERACTIVE_RESPONSE)
        assert result.error is None
        assert result.frames[0].error is not None
        assert "typing_extensions" in result.frames[0].error.message
        assert result.score == 0


class TestStandardShape:
    def test_still_parses(self) -> None:
        """Making every field optional to admit the interactive shape must not stop the standard
           one from being understood."""
        result = CgPlayResult.from_dict(STANDARD_RESPONSE)
        assert not result.is_interactive
        assert result.output == "5\n"
        assert result.comparison is not None
        assert result.comparison.success

    def test_has_no_frames_or_score(self) -> None:
        result = CgPlayResult.from_dict(STANDARD_RESPONSE)
        assert result.frames == []
        assert result.score is None


class TestRefereeMarkup:
    """CodinGame colours console text as `¤COLOUR¤text§COLOUR§`, in the referee's narration and in
       whatever the solution prints -- the IDE console renders both. Printed raw, a terminal shows
       the literal markers around every result line."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("¤GREEN¤You win.§GREEN§", "You win."),
        ("¤RED¤Timeout...§RED§\n", "Timeout...\n"),
        ("before ¤CYAN¤middle§CYAN§ after", "before middle after"),
        ("no markup at all", "no markup at all"),
    ])
    def test_markers_are_removed_and_their_text_kept(self, raw: str, expected: str) -> None:
        from codingame_tools.cli.main import _strip_referee_markup
        assert _strip_referee_markup(raw) == expected

    def test_a_colour_becomes_a_terminal_style(self) -> None:
        from codingame_tools.cli.main import _referee_text
        text = _referee_text("¤GREEN¤You win.§GREEN§")
        assert text.plain == "You win."
        assert any(str(span.style) == "green" for span in text.spans), text.spans

    def test_only_the_wrapped_span_is_styled(self) -> None:
        """A result line usually ends with unstyled narration after the coloured part; styling the
           remainder too would colour the whole console."""
        from codingame_tools.cli.main import _referee_text
        text = _referee_text("¤GREEN¤won§GREEN§ and then some")
        assert text.plain == "won and then some"
        styled = [s for s in text.spans if str(s.style) == "green"]
        assert len(styled) == 1
        assert text.plain[styled[0].start:styled[0].end] == "won"

    def test_an_unknown_colour_degrades_to_plain_text(self) -> None:
        """A colour name CodinGame adds later must lose its markers rather than leak them."""
        from codingame_tools.cli.main import _referee_text
        assert _referee_text("¤NEWCOLOUR¤hello§NEWCOLOUR§").plain == "hello"

    def test_program_output_is_never_treated_as_rich_markup(self) -> None:
        """These streams carry arbitrary program output. Building Rich markup from it would let a
           solution that printed `[bold]` change the styling, or crash the renderer."""
        from codingame_tools.cli.main import _referee_text
        assert _referee_text("printed [bold]literal[/bold]").plain == "printed [bold]literal[/bold]"

    def test_a_stray_unmatched_marker_is_still_removed(self) -> None:
        from codingame_tools.cli.main import _strip_referee_markup
        assert _strip_referee_markup("¤RED¤unclosed") == "unclosed"


class TestJsonRoundTrip:
    """`--json` prints `CgPlayResult.to_dict()`, so it is only a faithful view of the response if
       that round-trips -- including anything the client does not model, which rides in
       `extra_data`. That is the whole point of the flag: seeing what a response really contains."""

    def test_a_modelled_response_round_trips(self) -> None:
        restored = CgPlayResult.from_dict(INTERACTIVE_RESPONSE).to_dict()
        assert restored["gameId"] == INTERACTIVE_RESPONSE["gameId"]
        assert restored["metadata"] == INTERACTIVE_RESPONSE["metadata"]
        assert len(restored["frames"]) == len(INTERACTIVE_RESPONSE["frames"])
        assert restored["frames"][1]["stdout"] == "7893 4305\n"

    def test_unmodelled_fields_survive_into_the_json(self) -> None:
        """A field CodinGame adds later must still show up under `--json`, or the flag would hide
           exactly what someone reaches for it to find."""
        payload = {
            **INTERACTIVE_RESPONSE,
            "somethingNew": {"added": "later"},
            "frames": [{**INTERACTIVE_RESPONSE["frames"][0], "perFrameNovelty": 42}],
        }
        result = CgPlayResult.from_dict(payload)
        assert result.extra_data == {"somethingNew": {"added": "later"}}
        restored = result.to_dict()
        assert restored["somethingNew"] == {"added": "later"}
        assert restored["frames"][0]["perFrameNovelty"] == 42


class TestFailureVerdict:
    """`--json` and the rendered output must agree on pass/fail, since both drive the exit code."""

    def test_interactive_failure_is_detected_from_frames(self) -> None:
        from codingame_tools.cli.main import _play_result_failed
        assert _play_result_failed(CgPlayResult.from_dict(FAILED_INTERACTIVE_RESPONSE))

    def test_interactive_success(self) -> None:
        from codingame_tools.cli.main import _play_result_failed
        assert not _play_result_failed(CgPlayResult.from_dict(INTERACTIVE_RESPONSE))

    def test_standard_success(self) -> None:
        from codingame_tools.cli.main import _play_result_failed
        assert not _play_result_failed(CgPlayResult.from_dict(STANDARD_RESPONSE))

    def test_standard_mismatch_is_a_failure(self) -> None:
        from codingame_tools.cli.main import _play_result_failed
        payload = {"output": "4\n", "comparison": {"success": False, "expected": "5", "found": "4"}}
        assert _play_result_failed(CgPlayResult.from_dict(payload))


class TestInteractiveRendering:
    """The rendered trace mirrors CodinGame's own console: per turn, each stream under its own
       heading, stderr in red. Someone comparing cg's output against the IDE should see the same
       turns with the same numbers."""

    @staticmethod
    def _render(payload: dict, *, summary: bool = False, show_stdout: bool = True,
                show_stderr: bool = True) -> str:
        import io

        from rich.console import Console

        from codingame_tools.cli.main import _print_interactive_result

        class _Item:
            index = 1
            label = "Simple"

        buffer = io.StringIO()
        console = Console(file=buffer, width=100, no_color=True, highlight=False)
        _print_interactive_result(console, _Item(), CgPlayResult.from_dict(payload),
                                  summary=summary, show_stdout=show_stdout,
                                  show_stderr=show_stderr)
        return buffer.getvalue()

    def test_the_first_frame_is_the_setup_not_turn_one(self) -> None:
        """CodinGame sends the initial state as frame 1, before the solution has moved, and numbers
           the turns from the next one. A ten-frame run is nine turns in the IDE, and calling the
           setup 'turn 1' would offset every turn against what the IDE shows."""
        out = self._render(INTERACTIVE_RESPONSE)
        assert "start" in out
        assert "turn 1/1" in out
        assert "turn 0" not in out

    def test_turn_count_excludes_the_setup_frame(self) -> None:
        assert "-- 1 turns" in self._render(INTERACTIVE_RESPONSE)

    def test_each_stream_gets_its_own_heading(self) -> None:
        out = self._render(INTERACTIVE_RESPONSE)
        assert "Standard Error Stream:" in out
        assert "Standard Output Stream:" in out
        assert "Game information:" in out

    def test_stream_lines_are_prefixed_like_the_ide(self) -> None:
        out = self._render(INTERACTIVE_RESPONSE)
        assert "> 7893 4305" in out
        assert "> Playing move (7893, 4305)" in out

    def test_referee_markup_is_not_shown_literally(self) -> None:
        out = self._render(INTERACTIVE_RESPONSE)
        assert "You held off the wave of zombies and scored 10 points." in out
        assert "¤GREEN¤" not in out and "§GREEN§" not in out

    def test_stderr_is_red_by_default(self) -> None:
        """The IDE colours stderr red wholesale, with no markup in the text saying so."""
        import io

        from rich.console import Console

        from codingame_tools.cli.main import _print_interactive_result

        class _Item:
            index = 1
            label = "Simple"

        buffer = io.StringIO()
        console = Console(file=buffer, width=100, force_terminal=True, highlight=False)
        _print_interactive_result(console, _Item(), CgPlayResult.from_dict(INTERACTIVE_RESPONSE))
        rendered = buffer.getvalue()
        stderr_line = next(li for li in rendered.splitlines() if "Playing move" in li)
        assert "\x1b[31m" in stderr_line, stderr_line

    def test_every_stream_is_shown_by_default(self) -> None:
        """The default matches CodinGame's own console, which shows all three streams per turn.
           Opting in to output would make the common case -- reading what your solution did -- the
           one that needs a flag."""
        out = self._render(INTERACTIVE_RESPONSE)
        assert "Standard Error Stream:" in out
        assert "Standard Output Stream:" in out
        assert "Game information:" in out

    def test_no_stdout_keeps_stderr_and_narration(self) -> None:
        """Dropping only the moves is the useful middle ground: a solution that logs its own move
           to stderr makes the stdout section redundant, but its debug output is the reason to
           look at all."""
        out = self._render(INTERACTIVE_RESPONSE, show_stdout=False)
        assert "Standard Output Stream:" not in out
        assert "Standard Error Stream:" in out
        assert "Playing move (7893, 4305)" in out
        assert "Game information:" in out

    def test_no_stderr_keeps_the_moves_and_narration(self) -> None:
        out = self._render(INTERACTIVE_RESPONSE, show_stderr=False)
        assert "Standard Error Stream:" not in out
        assert "Standard Output Stream:" in out
        assert "> 7893 4305" in out
        assert "Game information:" in out

    def test_suppressing_both_still_is_not_summary(self) -> None:
        """Turn headings and the referee's narration survive, so the shape of the game is still
           visible--unlike --summary, which also drops the per-turn structure."""
        out = self._render(INTERACTIVE_RESPONSE, show_stdout=False, show_stderr=False)
        assert "turn 1/1" in out
        assert "Game information:" in out

    def test_summary_drops_the_streams_but_keeps_the_narration(self) -> None:
        out = self._render(INTERACTIVE_RESPONSE, summary=True)
        assert "Game information:" in out
        assert "You held off the wave of zombies" in out
        assert "Standard Error Stream:" not in out
        assert "Standard Output Stream:" not in out

    def test_a_failure_names_the_turn_it_happened_on(self) -> None:
        out = self._render(FAILED_INTERACTIVE_RESPONSE)
        assert "[FAIL]" in out
        assert "failed at start" in out, "the pasted failure crashed on the setup frame"
        assert "typing_extensions" in out


class TestNonKeyframeFrames:
    """Some games let the referee act several times per move of yours, emitting frames it produced
       without reading anything from the solution, marked `keyframe: false`.

       Every frame is still numbered. Whether the web IDE counts these or hides them is **not
       established**: every frame of both puzzles measured so far (Code vs Zombies, Travelling
       Salesman) is a keyframe, so "number every frame after the setup" and "number only keyframes"
       give the same answer on all available evidence -- a ten-frame run showing `9/9` either way.
       Numbering every frame reproduces the one numbering actually observed; treating non-keyframes
       as uncounted would be a guess about the IDE, made where the evidence is silent.

       What `keyframe` is known to affect is stepping: single-stepping pauses only at keyframes.
       So the flag is surfaced in the label rather than folded into the count."""

    PAYLOAD = {
        "frames": [
            {"gameInformation": "setup\n", "keyframe": True},
            {"stdout": "move 1\n", "keyframe": True},
            {"gameInformation": "referee moves again\n", "keyframe": False},
            {"stdout": "move 2\n", "keyframe": True},
        ],
        "scores": [1.0],
        "metadata": {"score": 7.0},
    }

    def _render(self) -> str:
        import io

        from rich.console import Console

        from codingame_tools.cli.main import _print_interactive_result

        class _Item:
            index = 1
            label = "Sample"

        buffer = io.StringIO()
        console = Console(file=buffer, width=100, no_color=True, highlight=False)
        _print_interactive_result(console, _Item(), CgPlayResult.from_dict(self.PAYLOAD))
        return buffer.getvalue()

    def test_every_frame_after_the_setup_is_numbered(self) -> None:
        out = self._render()
        assert "-- 3 turns" in out
        assert "turn 1/3" in out
        assert "turn 2/3" in out
        assert "turn 3/3" in out

    def test_a_referee_only_frame_is_flagged_rather_than_hidden(self) -> None:
        """It is where single-stepping would not pause, which is worth seeing -- and dropping it
           would hide referee narration that only appears on such a frame."""
        out = self._render()
        assert "turn 2/3 (no input read)" in out
        assert "referee moves again" in out

    def test_ordinary_frames_carry_no_flag(self) -> None:
        out = self._render()
        assert "turn 1/3\n" in out or "turn 1/3 " not in out.replace("turn 1/3\n", "")
        assert "turn 3/3 (no input read)" not in out
