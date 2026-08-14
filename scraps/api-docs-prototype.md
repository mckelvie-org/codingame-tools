<!-- PROTOTYPE OUTPUT from scripts/gen_api_docs.py -- not wired into the docs build.
     Generated 2026-08-13 09:29 to show the shape before committing to the approach. -->

# `codingame_tools.client.common.protocol.contribution.schema`

JSON-serializable dataclasses for the findContribution and updateContribution Codingame API methods.

## `CgTopic`

A topic associated with a contribution, e.g. "Parsing", "Sorting", etc. Most of the fields are fetched from the server in a search for topics. **Only `label_map` is guaranteed.** A topic can arrive carrying nothing but its localized label, with every identifying/statistical field omitted outright (not null)--seen on author-typed free-form topics that don't correspond to an entry in CodinGame's own topic catalogue, e.g. `{"labelMap": {"2": "Logic Gates"}}`. Surveying the 80 topic objects across the pending community-review queue (2026-08-03): `labelMap` appeared 80/80, every other field 70/80, and `pageTitle`/`contentDetailsId` 38/80. So the catalogue fields are optional, and code that reads them must handle `None` rather than assuming a topic is always a real catalogue entry.

**Fields**

- **`label_map`** — `dict[str, str]`. Localized display label for the topic (language code -> label), e.g. {"1": "Parsing", "2": "Parsing"}. The only field always present--see the class docstring.
- **`id`** — `int | None`. The topic's unique identifier, or None for a topic that isn't a catalogue entry.
- **`handle`** — `str | None`. Opaque short identifier for the topic, e.g. "parsing". None for a non-catalogue topic.
- **`category`** — `str | None`. e.g. "FUNDAMENTALS", "ADVANCED", "INTERMEDIATE". None for a non-catalogue topic.
- **`puzzle_count`** — `int | None`. The number of puzzles tagged with this topic. None for a non-catalogue topic.
- **`parent_topic_id`** — `int | None`. The ID of this topic's parent topic in the topic hierarchy. None for a non-catalogue topic.
- **`page_title`** — `str | None`. Title of the topic's help-center page, if it has one.
- **`content_details_id`** — `int | None`. ID of the topic's help-center content, if it has one.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgTestCase`

A single test case for the contribution, including the input and expected output for the test. May represent either a local test case or a server-sideq validator test case. Tests are numbers in the order given, separately for local tests and validator tests. The server-side validator test cases are not shared with the puzzler, and used to validate the solution and score the submission. See the note on `CgTestSessionTestCase` (test_session/schema.py) re: a possible future shared "puzzle test case" model--that class represents the same underlying concept from the solve/IDE side (TestSession/startTestSession), with binary-ID references instead of this class's inline text content.

**Fields**

- **`title`** — `str`. Friendly title for the test case, e.g. "Large grid test case
- **`test_in`** — `str`. stdin text content for the test case
- **`test_out`** — `str`. Expected stdout text content for the test case
- **`is_test`** — `bool`. True if a local test shown to player during development prior to submission
- **`is_validator`** — `bool`. True if a server-side validator test case, hidden from player; used for validation / scoring
- **`need_validation`** — `bool`. Unclear what this field means; it is always true in current protocol tests.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContributionData`

The actual contribution content, including the problem statement, input/output descriptions, constraints, difficulty, solution language, stub generator, topics, and test cases.

**Fields**

- **`title`** — `str`. The title of the puzzle, e.g. "Grid Pathfinding" or "Sorting Challenge".
- **`statement`** — `CgMarkdown | None`. The problem statement, in simplified Markdown format, including the description of the problem, input/output formats, and examples.
- **`input_description`** — `CgMarkdown | None`. The description of the provided stdin input format, in simplified Markdown format.
- **`output_description`** — `CgMarkdown | None`. The description of the expected stdout output format, in simplified Markdown format.
- **`constraints`** — `CgMarkdown | None`. The constraints for the problem, in simplified Markdown format, e.g. "1 ≤ N ≤ 1000" or "1 ≤ A[i] ≤ 10^9".
- **`difficulty`** — `str | None`. The difficulty category for the puzzle, e.g. "easy", "medium", or "hard".
- **`stub_generator`** — `CgStubGenerator | None`. The stub generator used for the puzzle.
- **`topics`** — `list[CgTopic]`. The topics associated with the puzzle. Topic objects include metadata that is retrieved from the server when searching for topics by name.
- **`test_cases`** — `list[CgTestCase]`. The test cases for the puzzle. Both local test cases shown to the player during development and server-side validator test cases are included here. The server-side validator test cases are not shared with the puzzler, and used to validate the solution and score the submission. When rendered, test cases are numbered in the order given, begining at 1, separately for local tests and validator tests. The way the input form is set up, the list will always consist of contiguous pairs of tests, with local test first and validator test second.
- **`solution_language`** — `CgSolutionLanguage | None`. The programming language used for the reference solution, e.g. "Python3", "Java", "C++", etc. May be missing if the reference solution is not yet provided. See `codingame_tools.language.get_language_by_extension` for mapping from file extension to solution language string.
- **`solution`** — `str | None`. The reference solution code for the puzzle, in the specified solution language. May be missing if the reference solution is not yet provided. When a submission is made, this solution must pass all test cases for the submission to be accepted.
- **`cover_binary_id`** — `int | None`. The ID of an uploaded graphical cover image for the puzzle. The image is uploaded separately and the server returns a binary ID for the image, which can be included here to associate the image with the contribution.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContributionVersion`

The wrapper for a specific version of a contribution, including the contribution data and metadata such as version number.

**Fields**

- **`version`** — `int`. A sequentially incrementing version number for the contribution, starting at 1 for the first version. When submitting an edit, the previous version number must be provided as a parameter to updateContribution; this serves to make the API idempotent and prevent race conditions from concurrent edits.
- **`data`** — `CgContributionData`. The actual contribution content, including the problem statement, input/output descriptions, constraints, difficulty, solution language,
- **`draft`** — `bool | None`. Whether this version of the contribution is a draft. Draft versions are private to the contributor and are not shared for comment/approval. Present in both `findContribution` and `updateContribution` responses (confirmed 2026-07-26 via round-trip test).
- **`ready_for_moderation`** — `bool | None`. Whether this version of the contribution is ready for moderation. Present in both `findContribution` and `updateContribution` responses (confirmed 2026-07-26 via round-trip test).
- **`statement_html`** — `CgHtml | None`. Server-rendered HTML of the statement, input/output descriptions, and constraints, used only for display on the contribution view page. Entirely derivative of `data.statement` (and the other `CgContributionData` text fields it's rendered from)--non-authoritative, and never needed to reconstruct or resubmit a version. Present in `findContribution` responses; omitted from `updateContribution` responses (confirmed 2026-07-26), presumably because the update response doesn't wait for/include the server-side re-render. Fetch via `findContribution` if the rendered HTML for a just-submitted version is needed. Explicitly aliased: the server sends "statementHTML" (all-caps acronym), which the automatic camelCase transform doesn't produce from `statement_html` (it produces "statementHtml").

**Derived**

- **`autoclose_time`** — `datetime | None`. The time at which the contribution will be automatically closed for voting and comments, always UTC. None if the contribution does not have an autoclose time set.
- **`freeze_time`** — `datetime | None`. See the field docstring for `_freeze_time`. Always UTC. None if not set.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContributionStatusChange`

Details of a single status transition, embedded in `CgContributionStatusHistoryEntry.data`.

**Fields**

- **`author`** — `str`. Who/what triggered the transition, e.g. "SYSTEM" (an automatic transition) or "ACTION" (triggered by the contributor's own action, e.g. editing the contribution).
- **`reason`** — `str | None`. Why the transition happened, e.g. "INACTIVITY" (automatically refused after a period of no activity) or "EDIT" (moved back to pending after the contributor edited it). **Absent for some transitions**, so optional. Observed live (2026-08-12): a contribution moving to "ACCEPTED" carries `{"author": "ACTION"}` and nothing else, while "REFUSED" and "PENDING" both carry a reason. Requiring it meant every call touching a contribution's status history broke the moment that contribution was accepted--including `updateContribution`, so an accepted contribution could not be edited at all.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContributionStatusHistoryEntry`

A single entry in a contribution's status history (`CgContribution.status_history` / `CgPendingContribution.status_history`).

**Fields**

- **`status`** — `str`. The status transitioned to, e.g. "PENDING", "REFUSED".
- **`data`** — `CgContributionStatusChange`. Details of what triggered this transition.
- **`date`** — integer (epoch milliseconds) -> `datetime`, Python attribute `_date`. When this status transition occurred.

**Derived**

- **`date`** — `datetime`. See the field docstring for `_date`. Always UTC.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgValidateAction`

The status of an asynchronous server-side validation action for a contribution (e.g. triggered by editing/submitting a puzzle). Only a single example has been observed so far, so field optionality is not yet well established--all three fields are currently required.

**Fields**

- **`action_id`** — `int`. Opaque identifier for the validation action.
- **`progress`** — `float`. Fractional progress of the validation action, from 0.0 to 1.0.
- **`already_done`** — `bool`. Whether the validation action has already completed.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgDeleteContributionResult`

The response to deleteContribution.

**Fields**

- **`action_id`** — `int`. Opaque identifier for the (apparently asynchronous, like `CgValidateAction`) deletion action.
- **`result`** — `bool`. Whether the deletion succeeded.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContribution`

The complete response to findContribution. Also the response shape for updateContribution (see `CgContributionService.update_contribution`)--but see `active_version` and `CgContributionVersion.statement_html` for two fields confirmed to differ between the two in practice.

**Fields**

- **`id`** — `int`. The unique identifier for the contribution, assigned by the server.
- **`active_version`** — `int`. The version number of the currently active version of the contribution. In an updateContribution response, this has been confirmed live (2026-07-28) to lag by one version behind the version just created--e.g. after submitting what becomes version 63, `active_version` is still 62 even though `last_version.version` in that same response is already 63. A `findContribution` call moments later correctly reports 63. Likely the new version's activation happens slightly asynchronously server-side, similar to why `CgContributionVersion.statement_html` isn't rendered yet either. Use `last_version.version` (not this field) when a just-submitted version's number is needed.
- **`score`** — `int`. The score of the contribution.
- **`votable_id`** — `int`. The unique identifier for the votable entity associated with the contribution.
- **`codingamer_id`** — `int`. The unique identifier for the codingamer (contributor) who created the contribution.
- **`views`** — `int`. The number of views the contribution has received.
- **`commentable_id`** — `int`. The unique identifier for the commentable entity associated with the contribution.
- **`title`** — `str`. The title of the contribution.
- **`status`** — `str`. The status of the contribution, e.g. "PENDING", "APPROVED", "REJECTED".
- **`nickname`** — `str`. The nickname of the contributor.
- **`public_handle`** — `str`. The public handle of the contribution. This is the identifier used for finding the contribution and updating it.
- **`codingamer_handle`** — `str`. The long, opaque string identifier for the contributor.
- **`last_version`** — `CgContributionVersion`. The most recent version of the contribution, including all content.
- **`comment_count`** — `int`. The number of comments on the contribution.
- **`up_votes`** — `int`. The number of up votes on the contribution.
- **`down_votes`** — `int`. The number of down votes on the contribution.
- **`editable`** — `bool`. Whether the contribution is currently editable by the contributor.
- **`draft`** — `bool`. Whether the contribution is currently a draft.
- **`ready_for_moderation`** — `bool`. Whether the contribution is ready for moderation.
- **`type`** — `CgPuzzleType`, Python attribute `contribution_type`. The type of the contribution, e.g. "PUZZLE_INOUT" for a standard noninteractive solo puzzle.
- **`avatar`** — `int | None`. The binary image ID of the contributor's avatar image, or None for a codingamer who has never set one. Omitted entirely (not null); seen on 3 of the 54 contributions in the pending community-review queue (2026-08-03). `CgPendingContribution` already treated it this way-- these two classes describe the same underlying codingamer and had simply drifted.
- **`status_history`** — `list[CgContributionStatusHistoryEntry]`. The history of status changes for the contribution.
- **`validate_action`** — `CgValidateAction | None`. The status of an in-progress server-side validation action for the contribution, if any.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgPendingContribution`

A single contribution summary, as returned (in a bare JSON array) by getAllPendingContributions. A lighter-weight summary than `CgContribution`--notably, it has no `last_version` (full content), but adds `publication_date`/`autoclose_time` and `user_moderation_status` not present on `CgContribution`.

**Fields**

- **`id`** — `int`. The unique identifier for the contribution.
- **`votable_id`** — `int`. The unique identifier for the votable entity associated with the contribution.
- **`commentable_id`** — `int`. The unique identifier for the commentable entity associated with the contribution.
- **`title`** — `str`. The title of the contribution.
- **`status`** — `str`. The status of the contribution. Always "PENDING" when listed by getAllPendingContributions; other values (e.g. "REFUSED") observed only in `status_history`.
- **`user_moderation_status`** — `str`. The requesting codingamer's moderation standing for this contribution, e.g. "PENDING" (can moderate) or "FORBIDDEN" (cannot, e.g. having already voted/commented).
- **`codingamer_id`** — `int`. The unique identifier for the codingamer (contributor) who created the contribution.
- **`codingamer_handle`** — `str`. The long, opaque string identifier for the contributor.
- **`nickname`** — `str`. The nickname of the contributor.
- **`public_handle`** — `str`. The public handle of the contribution.
- **`active_version`** — `int`. The version number of the currently active version of the contribution.
- **`draft`** — `bool`. Whether the contribution is currently a draft.
- **`editable`** — `bool`. Whether the contribution is currently editable by the contributor.
- **`ready_for_moderation`** — `bool`. Whether the contribution is ready for moderation.
- **`score`** — `int`. The score of the contribution.
- **`up_votes`** — `int`. The number of up votes on the contribution.
- **`down_votes`** — `int`. The number of down votes on the contribution.
- **`comment_count`** — `int`. The number of comments on the contribution.
- **`views`** — `int`. The number of views the contribution has received.
- **`status_history`** — `list[CgContributionStatusHistoryEntry]`. The history of status changes for the contribution.
- **`type`** — `CgPuzzleType`, Python attribute `contribution_type`. The type of the contribution, e.g. "PUZZLE_INOUT", "CLASHOFCODE".
- **`publicationDate`** — integer (epoch milliseconds) -> `datetime`, Python attribute `_publication_date`. When the contribution was first published/submitted.
- **`autocloseTime`** — integer (epoch milliseconds) -> `datetime`, Python attribute `_autoclose_time`. When the contribution will be automatically closed for voting and comments.
- **`avatar`** — `int | None`. The binary image ID of the contributor's avatar image. Not always present--observed absent for a few contributors.
- **`validate_action`** — `CgValidateAction | None`. The status of an in-progress server-side validation action for the contribution, if any.

**Derived**

- **`publication_date`** — `datetime`. See the field docstring for `_publication_date`. Always UTC.
- **`autoclose_time`** — `datetime`. See the field docstring for `_autoclose_time`. Always UTC.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgPersonalContribution`

A single contribution summary, as returned (in a bare JSON array) by Contribution/getPersonalContributions--every contribution (any status, not just PENDING) authored by the queried codingamer, e.g. for a "my contributions" listing page. Another lighter-weight summary than `CgContribution` (no `last_version`), and not quite the same shape as `CgPendingContribution` either--no `user_moderation_status`/`publication_date` here, but adds `avatar`/`validate_action`, and `autoclose_time` is optional (absent for draft/never-submitted-for-moderation contributions) rather than always present.

**Fields**

- **`id`** — `int`. The unique identifier for the contribution.
- **`votable_id`** — `int`. The unique identifier for the votable entity associated with the contribution.
- **`commentable_id`** — `int`. The unique identifier for the commentable entity associated with the contribution.
- **`codingamer_id`** — `int`. The unique identifier for the codingamer (contributor) who created the contribution.
- **`codingamer_handle`** — `str`. The long, opaque string identifier for the contributor.
- **`nickname`** — `str`. The nickname of the contributor.
- **`public_handle`** — `str`. The public handle of the contribution.
- **`title`** — `str`. The title of the contribution.
- **`status`** — `str`. The status of the contribution, e.g. "PENDING", "APPROVED", "REFUSED".
- **`active_version`** — `int`. The version number of the currently active version of the contribution.
- **`draft`** — `bool`. Whether the contribution is currently a draft.
- **`editable`** — `bool`. Whether the contribution is currently editable by the contributor.
- **`ready_for_moderation`** — `bool`. Whether the contribution is ready for moderation.
- **`score`** — `int`. The score of the contribution.
- **`up_votes`** — `int`. The number of up votes on the contribution.
- **`down_votes`** — `int`. The number of down votes on the contribution.
- **`comment_count`** — `int`. The number of comments on the contribution.
- **`views`** — `int`. The number of views the contribution has received.
- **`status_history`** — `list[CgContributionStatusHistoryEntry]`. The history of status changes for the contribution.
- **`type`** — `CgPuzzleType`, Python attribute `contribution_type`. The type of the contribution, e.g. "PUZZLE_INOUT", "CLASHOFCODE".
- **`avatar`** — `int | None`. The binary image ID of the contributor's avatar image, or None for a codingamer who has never set one. Optional for the same reason as `CgContribution.avatar`--see there.
- **`validate_action`** — `CgValidateAction | None`. The status of an in-progress server-side validation action for the contribution, if any.

**Derived**

- **`autoclose_time`** — `datetime | None`. See the field docstring for `_autoclose_time`. Always UTC. None if not set.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.

## `CgContributionModerator`

A single moderator who has cast a `"validate"`/`"deny"` vote on a contribution, as returned (in a bare JSON array) by `Contribution/findContributionModerators`.

**Fields**

- **`user_id`** — `int`. The moderator's numeric codingamer ID.
- **`pseudo`** — `str`. The moderator's display nickname.
- **`public_handle`** — `str`. The moderator's opaque public handle.
- **`avatar`** — `int`. The binary image ID of the moderator's avatar.
- **`cover`** — `int`. The binary image ID of the moderator's profile cover image.

Any JSON key not listed above is preserved in `extra_data` rather than discarded, so an unrecognized field survives a decode/encode round trip.
