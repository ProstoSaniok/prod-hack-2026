"""Run with: src/backend/.venv/bin/python -m pytest utils/seeddata/test_seeddata.py."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from utils.seeddata.load import read_bundle, validate_bundle
from utils.seeddata.server import UserInput

DATA = Path(__file__).parent / "data"


def test_fixture_relationships_and_product_roles():
    data = read_bundle(DATA)
    assert len(data["users"]) == 10
    assert {r["kind"] for r in data["users"]} == {
        "organizer",
        "performer",
        "review_minus",
        "review_plus",
        "expert",
    }
    assert len(data["competencies"]) == 2000
    assert len(data["cases"]) == 10
    assert len(data["submissions"]) == len(data["reviews"]) == 3


@pytest.mark.parametrize("kind", ["admin", "superuser", "reviewer"])
def test_seed_endpoint_rejects_unsupported_roles(kind):
    with pytest.raises(ValidationError):
        UserInput(
            id="00000000-0000-0000-0000-000000000001",
            email="test.seed@example.com",
            key="test",
            display_name="Test",
            kind=kind,
            initial_value=0,
        )


@pytest.mark.parametrize(
    "problem",
    [
        "admin",
        "wrong_email",
        "insufficient_reviewer_value",
        "wrong_owner",
        "duplicate_skill",
        "zero_weight",
        "nan_weight",
        "unpublished_submission",
        "duplicate_submission",
        "self_review",
        "duplicate_review",
        "bad_score",
        "bad_verdict",
        "bad_comment",
    ],
)
def test_bad_fixtures_fail_before_api_mutations(problem):
    data = copy.deepcopy(read_bundle(DATA))
    if problem == "admin":
        data["users"][0]["kind"] = "admin"
    elif problem == "wrong_email":
        data["users"][0]["email"] = "real@example.com"
    elif problem == "insufficient_reviewer_value":
        next(u for u in data["users"] if u["kind"] == "expert")["initial_value"] = "20"
    elif problem == "wrong_owner":
        data["cases"][0]["owner_key"] = "performer_1"
    elif problem == "duplicate_skill":
        data["cases"][0]["competencies_json"] = json.dumps(
            [{"skill_slug": "api", "required_value": 20, "weight": 0.1}] * 2
        )
    elif problem in {"zero_weight", "nan_weight"}:
        data["cases"][0]["competencies_json"] = (
            '[{"skill_slug":"api","required_value":20,"weight":'
            + ("0" if problem == "zero_weight" else '"NaN"')
            + "}]"
        )
    elif problem == "unpublished_submission":
        data["cases"][0]["status"] = "draft"
    elif problem == "duplicate_submission":
        data["submissions"].append(dict(data["submissions"][0], key="duplicate"))
    elif problem == "self_review":
        data["reviews"][0]["reviewer_key"] = "performer_1"
    elif problem == "duplicate_review":
        data["reviews"].append(dict(data["reviews"][0], key="duplicate"))
    elif problem == "bad_score":
        data["reviews"][0]["score"] = "101"
    elif problem == "bad_verdict":
        data["reviews"][0]["verdict"] = "passed"
    elif problem == "bad_comment":
        data["reviews"][0]["comments_json"] = (
            '[{"body":"Check","path":"solution.md","line":999,"side":"new"}]'
        )
    with pytest.raises(ValueError):
        validate_bundle(data)
