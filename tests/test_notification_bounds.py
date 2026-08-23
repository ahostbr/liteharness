"""The inbox notification must fit the transport, or say that it did not.

The transport (Claude Code's Monitor surfacing) truncates SILENTLY by two
different mechanisms, both measured 2026-08-23 with a self-locating probe:

  PER-LINE at 500 chars of content — the long line is cut, "...(truncated)" is
    appended, and the rest of the message prints normally INCLUDING the trailing
    end-marker. This is the dangerous one: the guard reads green on a real loss.
    Three bodies lost 3.7%, 4.5% and 94% in one hour and nothing in the
    presentation told them apart.

  WHOLE-NOTIFICATION at 3000 chars — hard tail cut; the end-marker disappears
    with everything else, so the guard works.

compose_notification() removes the transport's opportunity rather than adding a
second detector. These tests hold it to that, and each one is paired with a
mutation proving it can fail (see test_mutations_prove_the_assertions_bite).
"""

import pytest

from liteharness.hooks import (
    NOTIFY_LINE_LIMIT,
    NOTIFY_TOTAL_BUDGET,
    _wrap_preserving_newlines,
    compose_notification,
)

SENDER = "ac965cc1-7c81-4ae5-a4ef-41ec2dc88bd0"
ME = "ba736bd4-d249-42c0-b1ed-04b597d753f0"
MSG = "0adde81f-6135-45ca-8983-0420b7cced23"

END_MARKER = "[END OF MESSAGE"
CUT_NOTICE = "BODY TRUNCATED BY THE PRODUCER"


def compose(body: str, **kw) -> str:
    return compose_notification(sender=SENDER, body=body, agent_id=ME, msg_id=MSG, **kw)


def test_short_body_is_passed_through_verbatim():
    body = "ALIVE. cwd C:/Projects/LiteTUI, tree clean, nothing in flight."
    out = compose(body)

    assert body in out, "a body that fits must appear unaltered"
    assert END_MARKER in out
    assert CUT_NOTICE not in out, "nothing was truncated, so nothing may claim it was"


def test_a_long_line_is_wrapped_and_nothing_is_lost():
    """The real 631-char case from 2026-08-23, which the transport cut at 503."""
    long_line = "   _execute_tool STAYS ON THE HOST. " + ("x" * 595)
    assert len(long_line) == 631
    body = f"header line\n{long_line}\ntrailer line"

    out = compose(body)

    # 1. no line can trip the per-line cut
    assert all(len(l) <= NOTIFY_LINE_LIMIT for l in out.split("\n")), (
        "a line over the limit would be cut by the transport with the end-marker "
        "still present — the exact silent failure this exists to prevent"
    )
    # 2. and the wrap lost nothing: the content is still there, contiguously
    assert long_line.replace(" ", "") in out.replace("\n", "").replace(" ", "")
    assert CUT_NOTICE not in out, "631 chars fits the budget; only the LINE needed wrapping"
    assert END_MARKER in out


def test_wrapping_is_lossless_and_does_not_reflow():
    """Wrapping must be reversible. textwrap.fill would not be."""
    body = "cmd --flag value\n" + ("y" * 1000) + "\n  indented tail"
    wrapped = _wrap_preserving_newlines(body, NOTIFY_LINE_LIMIT)

    # Re-joining the pieces of each original line reconstructs the body exactly.
    assert "".join(wrapped) == body.replace("\n", ""), (
        "wrapping must preserve every character including runs of whitespace; "
        "reflowing would silently rewrite commands and shas agents re-run"
    )
    assert all(len(w) <= NOTIFY_LINE_LIMIT for w in wrapped)


def test_oversized_body_is_truncated_by_us_and_says_so():
    """The 3999-char probe: the transport cut it at exactly 3000 and ate the marker."""
    body = "\n".join(f"L{i:03d} OFFSET{(i - 1) * 40:05d} ".ljust(39, ".") for i in range(1, 101))
    assert len(body) == 3999

    out = compose(body)

    assert len(out) <= NOTIFY_TOTAL_BUDGET, "must fit under the measured 3000 cut"
    assert CUT_NOTICE in out, "an unannounced truncation is the bug, not the fix"
    assert END_MARKER in out, (
        "the marker must SURVIVE our own truncation — it is the reader's check "
        "against the transport, and it is worthless if we cut it off ourselves"
    )
    assert all(len(l) <= NOTIFY_LINE_LIMIT for l in out.split("\n"))
    assert "L001 OFFSET00000" in out, "the head of the body must still be delivered"


def test_declared_length_is_the_original_not_the_delivered():
    """A reader compares the declared count against what arrived; declaring the
    post-truncation length would make the two agree and hide the loss."""
    body = "z" * 5000
    out = compose(body)

    assert "5000 chars" in out
    assert len(out) < 5000


def test_envelope_is_measured_not_estimated():
    """Budget must hold when the envelope grows — thread and project are optional
    lines, and a fixed estimate would overflow once both are present."""
    body = "\n".join(f"line {i} " + "q" * 60 for i in range(200))

    plain = compose(body)
    fat = compose(body, thread="T" * 80, project="P" * 80, prefix="[URGENT] ")

    assert len(plain) <= NOTIFY_TOTAL_BUDGET
    assert len(fat) <= NOTIFY_TOTAL_BUDGET, "a longer envelope must shrink the body, not overflow"
    assert END_MARKER in fat


@pytest.mark.parametrize(
    "mutation,body,predicate,why",
    [
        (
            "no wrapping",
            "a" * 900,
            lambda out: all(len(l) <= NOTIFY_LINE_LIMIT for l in out.split("\n")),
            "without wrapping a 900-char line survives and the transport cuts it at 500",
        ),
        (
            "no budget",
            "b" * 9000,
            lambda out: len(out) <= NOTIFY_TOTAL_BUDGET,
            "without budgeting the notification exceeds 3000 and loses its own marker",
        ),
    ],
)
def test_mutations_prove_the_assertions_bite(mutation, body, predicate, why):
    """An assertion that has never failed is not evidence.

    Each mutation is the un-fixed behaviour: emit head + raw body + tail with no
    wrapping and no budget. The predicate that guards against it MUST go false,
    otherwise the corresponding test above is decorative.
    """
    head = f"[LITEHARNESS] Message from {SENDER} (notification, {len(body)} chars, id {MSG}):"
    tail = f"{END_MARKER} {MSG} ...]"
    unfixed = "\n".join([head, body, tail])

    assert not predicate(unfixed), f"MUTATION {mutation!r} did not break the check: {why}"
    # and the real implementation holds where the mutant does not
    assert predicate(compose(body)), "the fix must satisfy the check the mutant fails"
