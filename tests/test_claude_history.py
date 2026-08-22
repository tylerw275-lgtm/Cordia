"""Conversation replay must produce a transcript the Messages API accepts.

The original code replayed a persisted assistant turn as a raw JSON *string*
and replayed tool results as a user turn whose tool_use_ids had no matching
tool_use — a guaranteed 400. Every conversation broke after Cord's first tool
call, surfacing to Cordia as "Something went wrong on my end."
"""
import json

import pytest

from app.services import claude_service


def assert_valid_transcript(messages: list[dict]) -> None:
    """The invariants the Anthropic Messages API enforces on input."""
    if not messages:
        return
    assert messages[0]["role"] == "user", "transcript must start with a user turn"
    for i, msg in enumerate(messages):
        content = msg["content"]
        assert content != [] and content != "", f"turn {i} is empty"
        blocks = content if isinstance(content, list) else []
        assert not any(b.get("type") in ("thinking", "redacted_thinking") for b in blocks), \
            f"turn {i} replays a thinking block"

        tool_uses = {b["id"] for b in blocks if b.get("type") == "tool_use"}
        if tool_uses:
            assert i + 1 < len(messages), f"turn {i}: trailing tool_use with no result"
            nxt = messages[i + 1]["content"]
            nxt_blocks = nxt if isinstance(nxt, list) else []
            answered = {b["tool_use_id"] for b in nxt_blocks if b.get("type") == "tool_result"}
            assert tool_uses <= answered, f"turn {i}: unanswered tool_use {tool_uses - answered}"

        results = {b["tool_use_id"] for b in blocks if b.get("type") == "tool_result"}
        if results:
            assert i > 0, "transcript starts with a tool_result"
            prev = messages[i - 1]["content"]
            prev_blocks = prev if isinstance(prev, list) else []
            prev_ids = {b["id"] for b in prev_blocks if b.get("type") == "tool_use"}
            assert results <= prev_ids, f"turn {i}: orphaned tool_result {results - prev_ids}"


def _user(text):
    return {"role": "user", "content": text}


def _assistant_tool_use(tool_id="tu_1"):
    return {"role": "assistant", "content": [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": tool_id, "name": "list_family_members", "input": {}},
    ]}


def _tool_result(tool_id="tu_1"):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "{}"},
    ]}


def test_assistant_json_is_decoded_into_blocks():
    raw = json.dumps([{"type": "text", "text": "Hi there", "citations": None}])
    decoded = claude_service._decode_content(raw, "assistant")
    assert decoded == [{"type": "text", "text": "Hi there"}]


def test_plain_text_content_survives_unchanged():
    assert claude_service._decode_content("just a string", "user") == "just a string"


def test_thinking_blocks_are_stripped():
    raw = json.dumps([
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "text", "text": "Answer"},
    ])
    assert claude_service._decode_content(raw, "assistant") == [{"type": "text", "text": "Answer"}]


def test_optional_null_fields_are_dropped():
    raw = json.dumps([{"type": "tool_use", "id": "tu_1", "name": "f", "input": {}, "cache_control": None}])
    assert claude_service._decode_content(raw, "assistant") == [
        {"type": "tool_use", "id": "tu_1", "name": "f", "input": {}}
    ]


def test_complete_tool_exchange_is_preserved():
    out = claude_service._sanitize_history([
        _user("who is in my family?"), _assistant_tool_use(), _tool_result(),
        {"role": "assistant", "content": [{"type": "text", "text": "Three sons."}]},
    ])
    assert len(out) == 4
    assert_valid_transcript(out)


def test_orphaned_tool_result_is_dropped():
    # What a truncated window produces: results with no preceding tool_use.
    out = claude_service._sanitize_history([
        _tool_result(), {"role": "assistant", "content": [{"type": "text", "text": "Three sons."}]},
        _user("and the youngest?"),
    ])
    assert_valid_transcript(out)
    assert all(
        not any(b.get("type") == "tool_result" for b in m["content"])
        for m in out if isinstance(m["content"], list)
    )


def test_unanswered_tool_use_is_dropped_but_prose_kept():
    out = claude_service._sanitize_history([_user("hi"), _assistant_tool_use()])
    assert_valid_transcript(out)
    assert out[-1]["content"] == [{"type": "text", "text": "Let me look."}]


def test_partial_tool_results_drop_the_whole_pair():
    turn = {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_1", "name": "a", "input": {}},
        {"type": "tool_use", "id": "tu_2", "name": "b", "input": {}},
    ]}
    out = claude_service._sanitize_history([_user("hi"), turn, _tool_result("tu_1")])
    assert_valid_transcript(out)
    assert out == [_user("hi")]


def test_transcript_never_starts_with_a_tool_result():
    out = claude_service._sanitize_history([_tool_result(), _user("hello")])
    assert out and out[0]["role"] == "user"
    assert_valid_transcript(out)


@pytest.mark.asyncio
async def test_round_trip_through_the_database(db):
    """The end-to-end shape: persist a real tool exchange, load it back, and
    confirm the result is a transcript the API would accept."""
    conv = await claude_service.get_or_create_conversation(db, "+15550001111")

    await claude_service._persist_message(db, conv.id, "user", "who is in my family?")
    await claude_service._persist_message(db, conv.id, "assistant", [
        {"type": "thinking", "thinking": "check the roster", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "list_family_members", "input": {}, "cache_control": None},
    ])
    await claude_service._persist_message(db, conv.id, "tool", [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"count": 15}'},
    ])
    await claude_service._persist_message(db, conv.id, "assistant", [
        {"type": "text", "text": "Three sons, three daughters-in-law, and ten grandkids."},
    ])

    history = await claude_service._load_history(db, conv.id)
    assert_valid_transcript(history)
    # And no assistant turn comes back as a raw JSON blob.
    assert all(not (isinstance(m["content"], str) and m["content"].lstrip().startswith("["))
               for m in history)


def test_trim_cuts_only_at_a_plain_user_turn():
    turns = [_user("one"), _assistant_tool_use("tu_a"), _tool_result("tu_a"),
             {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
             _user("two")]
    out = claude_service._trim_to_chars(claude_service._sanitize_history(turns), budget=20)
    # Cutting mid-exchange would strand the tool_result, so it cuts at "two".
    assert out == [_user("two")]
    assert_valid_transcript(out)


def test_trim_is_a_no_op_when_history_fits():
    turns = [_user("one"), {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    assert claude_service._trim_to_chars(turns, budget=48_000) == turns


def test_user_turn_cannot_carry_a_tool_use_block():
    """Someone texting a literal JSON array of tool_use blocks used to poison
    their own thread: tool_use is assistant-only, so replaying it 400s."""
    raw = json.dumps([{"type": "tool_use", "id": "x", "name": "send_sms", "input": {}}])
    assert claude_service._decode_content(raw, "user") == ""


def test_assistant_turn_cannot_carry_a_tool_result_block():
    raw = json.dumps([{"type": "tool_result", "tool_use_id": "x", "content": "{}"}])
    assert claude_service._decode_content(raw, "assistant") == ""


def test_blocks_missing_a_required_key_are_dropped():
    # A tool_use with a null id previously survived and then raised KeyError.
    raw = json.dumps([{"type": "tool_use", "id": None, "name": "f", "input": {}},
                      {"type": "text", "text": "ok"}])
    assert claude_service._decode_content(raw, "assistant") == [{"type": "text", "text": "ok"}]


def test_history_never_begins_with_a_paired_tool_result():
    """Trimming to a user boundary could expose the tool_result that had been
    paired with a dropped assistant turn."""
    turns = [_assistant_tool_use("tu_1"), _tool_result("tu_1"),
             {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
             _user("thanks")]
    out = claude_service._sanitize_history(turns)
    assert_valid_transcript(out)
    assert not out or out[0]["role"] == "user"
