"""Gemini's schema dialect, pinned where it actually broke.

``sanitize_schema`` filters JSON-Schema keywords down to the subset Gemini
documents. The filter is correct at the top level and was catastrophically
wrong one level down: inside ``properties`` the dictionary keys are the
caller's own field names, and running them through a keyword allow-list
deleted every one of them. The request then carried ``properties: {}`` beside a
``required`` list naming fields that no longer existed, and the API answered

    HTTP 400 … response_schema.required[0]: property is not defined

for *every* schema-constrained call in the codebase. Nothing caught it: the
re-ask in :meth:`LLMProvider.generate_json` only fires on unparseable JSON, not
on a 400, so the failure surfaced to the candidate as "the AI reviewer is
unavailable" and the schema path had no test at all.

These tests are deliberately offline — they assert on the request body the
provider *would* send. The live counterpart is
``metrics/m9_provider_contract.py``, which makes the call for real, because a
local test can only prove we build what we meant to build, not that the vendor
still accepts it.
"""

from __future__ import annotations

from llm.gemini import sanitize_schema


def test_property_names_survive_the_keyword_filter():
    # The regression. "safe" and "reason" are field names, not schema keywords,
    # so the allow-list must not be applied to them.
    schema = {
        "type": "object",
        "properties": {"safe": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["safe", "reason"],
    }
    cleaned = sanitize_schema(schema)
    assert set(cleaned["properties"]) == {"safe", "reason"}
    assert cleaned["properties"]["safe"] == {"type": "boolean"}
    assert cleaned["required"] == ["safe", "reason"]


def test_every_required_field_is_actually_defined():
    """The invariant the 400 was complaining about, stated directly."""
    from services.application_service import _EVAL_SCHEMA
    from services.guardrail_service import _ANSWER_SCHEMA

    for schema in (_EVAL_SCHEMA, _ANSWER_SCHEMA):
        cleaned = sanitize_schema(schema)
        defined = set(cleaned.get("properties", {}))
        for name in cleaned.get("required", []):
            assert name in defined, f"{name} is required but was filtered out"


def test_unknown_keywords_are_still_stripped():
    # The filter's actual job, which must keep working.
    schema = {
        "type": "object",
        "additionalProperties": False,          # Gemini rejects this
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 5}},
    }
    cleaned = sanitize_schema(schema)
    assert "additionalProperties" not in cleaned
    assert "$schema" not in cleaned
    # …including inside a property's own sub-schema.
    assert cleaned["properties"]["n"] == {"type": "integer"}


def test_a_property_named_like_a_keyword_is_not_confused_for_one():
    # "items" and "required" are legitimate field names in a hiring schema.
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "string"},
            "required": {"type": "boolean"},
            "description": {"type": "string"},
        },
        "required": ["items"],
    }
    cleaned = sanitize_schema(schema)
    assert set(cleaned["properties"]) == {"items", "required", "description"}


def test_nested_objects_and_arrays_keep_their_fields():
    schema = {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "properties": {"skill_match": {"type": "integer"}},
                "required": ["skill_match"],
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["criteria"],
    }
    cleaned = sanitize_schema(schema)
    assert set(cleaned["properties"]["criteria"]["properties"]) == {"skill_match"}
    assert cleaned["properties"]["strengths"]["items"] == {"type": "string"}


def test_sanitize_is_idempotent():
    from services.application_service import _EVAL_SCHEMA

    once = sanitize_schema(_EVAL_SCHEMA)
    assert sanitize_schema(once) == once


def test_the_caller_s_schema_is_not_mutated():
    schema = {"type": "object", "properties": {"a": {"type": "string", "minimum": 1}}}
    sanitize_schema(schema)
    assert schema["properties"]["a"]["minimum"] == 1
