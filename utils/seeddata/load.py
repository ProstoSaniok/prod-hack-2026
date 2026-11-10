"""Load business CSV fixtures through the backend API; no database or Gitea access.

Start server.py with SEEDDATA_API_KEY, set the same key and SEEDDATA_PASSWORD:
  uv run --project src/backend python utils/seeddata/load.py http://127.0.0.1:8081
--validate-only checks CSV without services. Markdown files are base64 in CSV.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path


def decode(value: str) -> str:
    text = base64.b64decode(value, validate=True).decode("utf-8")
    if not text.strip():
        raise ValueError("Empty Markdown payload")
    return text


def read_bundle(path: Path) -> dict:
    required = {"competencies", "users", "cases", "submissions", "reviews"}
    available = {file.stem for file in path.glob("*.csv")}
    if missing := required - available:
        raise ValueError(f"Missing business fixtures: {sorted(missing)}")
    if unsupported := available - required:
        raise ValueError(
            f"No API import handler for {sorted(unsupported)}; add one before importing these fixtures"
        )
    csv.field_size_limit(16 * 1024 * 1024)
    data = {}
    for name in sorted(required):
        with (path / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
            data[name] = list(csv.DictReader(handle))
        keys = [row["slug" if name == "competencies" else "key"] for row in data[name]]
        if (
            len(keys) != len(set(keys))
            or not keys
            or any(not key.strip() for key in keys)
        ):
            raise ValueError(f"Missing/duplicate keys in {name}.csv")
    skills = {row["slug"] for row in data["competencies"]}
    users = {row["key"] for row in data["users"]}
    if len({row["email"] for row in data["users"]}) != len(data["users"]):
        raise ValueError("Duplicate user email")
    cases = {row["key"] for row in data["cases"]}
    submissions = {row["key"] for row in data["submissions"]}
    for row in data["cases"]:
        requirements = json.loads(row["competencies_json"])
        if row["owner_key"] not in users or any(
            r["skill_slug"] not in skills for r in requirements
        ):
            raise ValueError("Unknown case owner/skill")
        for field in (
            "condition_md_base64",
            "reference_solution_md_base64",
            "checks_md_base64",
        ):
            decode(row[field])
    for row in data["submissions"]:
        if row["case_key"] not in cases or row["user_key"] not in users:
            raise ValueError("Unknown submission case/user")
        decode(row["solution_md_base64"])
    for row in data["reviews"]:
        if row["submission_key"] not in submissions or row["reviewer_key"] not in users:
            raise ValueError("Unknown review submission/user")
        json.loads(row["comments_json"])
    validate_bundle(data)
    return data


def number(value, minimum, maximum):
    result = Decimal(str(value))
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(f"Expected a finite number between {minimum} and {maximum}")
    return result


def validate_bundle(data):
    """Reject broken fixture relationships before the first account is registered."""
    grades = {"performer": 0, "review_minus": 1, "review_plus": 2, "expert": 3}
    floors = {
        "organizer": 0,
        "performer": 0,
        "review_minus": 25,
        "review_plus": 50,
        "expert": 75,
    }
    users = {row["key"]: row for row in data["users"]}
    if len(users) > 12 or len(data["competencies"]) > 2000:
        raise ValueError("Fixture batch exceeds bootstrap limits")
    names = [row["name"] for row in data["competencies"]]
    if len(set(names)) != len(names) or any(not name.strip() for name in names):
        raise ValueError("Missing/duplicate competency name")
    for row in data["users"]:
        if row["kind"] not in floors:
            raise ValueError(
                "Unsupported seed account kind; admins are not demo participants"
            )
        if not re.fullmatch(r"[a-z0-9-]+\.seed@example\.com", row["email"]):
            raise ValueError("Expected an explicit .seed@example.com account")
        value = number(row["initial_value"], floors[row["kind"]], 100)
        if row["kind"] == "organizer" and value != 0:
            raise ValueError("Organizer must not carry a participant skill value")
        if not row["display_name"].strip() or not row["password_env"].strip():
            raise ValueError(
                "Missing account display name/password environment variable"
            )
    cases = {row["key"]: row for row in data["cases"]}
    positions = [int(row["position"]) for row in data["cases"]]
    if len(set(positions)) != len(positions) or min(positions) < 1:
        raise ValueError("Case positions must be unique positive integers")
    titles = [(row["owner_key"], row["title"]) for row in data["cases"]]
    if len(set(titles)) != len(titles):
        raise ValueError("Case titles must be unique per organizer for safe reruns")
    for row in data["cases"]:
        if users[row["owner_key"]]["kind"] != "organizer":
            raise ValueError("Case owner must be an organizer")
        if row["status"] not in {"draft", "published"}:
            raise ValueError("Seed cases must be draft or published")
        for field, limit in (
            ("title", 500),
            ("description", 16384),
            ("expected_result", 16384),
        ):
            if not row[field].strip() or len(row[field].encode()) > limit:
                raise ValueError(f"Invalid case {field}")
        requirements = json.loads(row["competencies_json"])
        if not 1 <= len(requirements) <= 100 or len(
            {r["skill_slug"] for r in requirements}
        ) != len(requirements):
            raise ValueError("Case competencies must be nonempty and unique")
        for requirement in requirements:
            number(requirement["required_value"], 0, 100)
            if number(requirement["weight"], 0, Decimal("0.25")) == 0:
                raise ValueError("Competency weight must be positive")
    submissions = {row["key"]: row for row in data["submissions"]}
    pairs = [(r["case_key"], r["user_key"]) for r in data["submissions"]]
    if len(set(pairs)) != len(pairs):
        raise ValueError("Only one seed submission per participant/case is supported")
    for row in data["submissions"]:
        if (
            cases[row["case_key"]]["status"] != "published"
            or users[row["user_key"]]["kind"] not in grades
        ):
            raise ValueError("Submission requires a participant and published case")
    reviewed = set()
    for row in data["reviews"]:
        submission = submissions[row["submission_key"]]
        if (
            row["submission_key"] in reviewed
            or submission["user_key"] == row["reviewer_key"]
        ):
            raise ValueError("Duplicate review or self-review")
        reviewed.add(row["submission_key"])
        reviewer = users[row["reviewer_key"]]
        author = users[submission["user_key"]]
        required = max(
            Decimal(str(r["required_value"]))
            for r in json.loads(cases[submission["case_key"]]["competencies_json"])
        )
        floor = max(
            3 if required >= 75 else 2 if required >= 50 else 1,
            min(grades[author["kind"]] + 1, 3),
        )
        if (
            grades.get(reviewer["kind"], -1) < floor
            or Decimal(reviewer["initial_value"]) < required
        ):
            raise ValueError(
                "Reviewer does not satisfy the case/author skill requirements"
            )
        if str(int(row["score"])) != row["score"] or not 0 <= int(row["score"]) <= 100:
            raise ValueError("Review score must be an integer in [0, 100]")
        if row["verdict"] not in {"approved", "changes_requested"}:
            raise ValueError("Unknown review verdict")
        if any(
            not row[field].strip() for field in ("strengths", "problems", "next_steps")
        ):
            raise ValueError("Review feedback must be actionable and nonempty")
        lines = decode(submission["solution_md_base64"]).splitlines()
        for comment in json.loads(row["comments_json"]):
            if not comment.get("body", "").strip():
                raise ValueError("Comment body must not be blank")
            if comment.get("path") is None:
                if comment.get("line") is not None or comment.get("side") is not None:
                    raise ValueError("General comment cannot have a diff position")
            elif (
                comment["path"] != "solution.md"
                or comment.get("side") != "new"
                or type(comment.get("line")) is not int
                or not 1 <= comment["line"] <= len(lines)
            ):
                raise ValueError(
                    "Inline comment must address a line added in solution.md"
                )


class Loader:
    def __init__(self, client, data: dict):
        self.client, self.data = client, data
        self.headers, self.case_ids, self.submission_ids = {}, {}, {}

    def request(
        self,
        user,
        method,
        path,
        *,
        body=None,
        params=None,
        key=None,
        statuses=(200, 201),
    ):
        headers = dict(self.headers.get(user, {}))
        if key is not None:
            headers["Idempotency-Key"] = (
                "seed-v3-" + hashlib.sha256(key.encode()).hexdigest()
            )
        response = self.client.request(
            method, path, json=body, params=params, headers=headers
        )
        if response.status_code not in statuses:
            # Responses may contain auth tokens; do not echo their bodies.
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code} ({user})")
        return response.json() if response.content else None

    def listing(self, user, path):
        rows = []
        while True:
            page = self.request(
                user, "GET", path, params={"limit": 100, "offset": len(rows)}
            )
            if isinstance(page, dict):
                page = page["items"]
            rows.extend(page)
            if len(page) < 100:
                return rows

    def accounts(self):
        # Validate all passwords before creating any accounts.
        for row in self.data["users"]:
            password = os.environ.get(row["password_env"], "")
            if not 8 <= len(password) <= 128:
                raise ValueError(
                    f"Set {row['password_env']} to a password of 8–128 characters"
                )
        users = []
        for row in self.data["users"]:
            password = os.environ.get(row["password_env"], "")
            if len(password) < 8:
                raise ValueError(
                    f"Set {row['password_env']} to a password of at least 8 characters"
                )
            body = {"email": row["email"], "password": password}
            response = self.client.post("/auth/sign_up/email", json=body)
            if response.status_code == 409:
                token = self.request("", "POST", "/auth/sign_in/email", body=body)
            elif response.status_code in {200, 201}:
                token = response.json()
            else:
                raise RuntimeError(
                    f"Cannot register {row['key']}: HTTP {response.status_code}"
                )
            self.headers[row["key"]] = {
                "Authorization": "Bearer " + token["access_token"]
            }
            current = self.request(row["key"], "GET", "/auth/me")
            users.append(
                {
                    **{k: row[k] for k in ("key", "email", "display_name", "kind")},
                    "id": current["id"],
                    "initial_value": row["initial_value"],
                }
            )
        slugs = sorted(
            {
                r["skill_slug"]
                for c in self.data["cases"]
                for r in json.loads(c["competencies_json"])
            }
        )
        response = self.client.post(
            "/seeddata/bootstrap",
            json={
                "users": users,
                "skills": self.data["competencies"],
                "active_skill_slugs": slugs,
            },
            headers={"X-Seeddata-Key": os.environ["SEEDDATA_API_KEY"]},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Seed bootstrap: HTTP {response.status_code}")
        self.skills = response.json()["skills"]
        print(f"Users: {len(users)}; skills: {len(self.skills)}")

    def file(self, user, root, filename, content, *, params=None, key):
        response = self.client.get(
            root + "/files/content",
            params={"path": filename},
            headers=self.headers[user],
        )
        if response.status_code == 200:
            if response.json()["content"] != content:
                raise ValueError(f"Existing seed file differs: {filename}")
            return response.json()
        if response.status_code != 404:
            raise RuntimeError(f"Cannot read {filename}: HTTP {response.status_code}")
        return self.request(
            user,
            "POST",
            root + "/files",
            body={"path": filename, "content": content},
            params=params,
            key=key,
        )

    def cases(self):
        for row in sorted(self.data["cases"], key=lambda item: int(item["position"])):
            user, key = row["owner_key"], row["key"]
            existing = [
                r
                for r in self.listing(user, "/api/v1/admin/cases")
                if r["title"] == row["title"]
            ]
            if len(existing) > 1:
                raise ValueError("Ambiguous seed case title")
            case = (
                existing[0]
                if existing
                else self.request(
                    user,
                    "POST",
                    "/api/v1/admin/cases",
                    body={
                        "title": row["title"],
                        "description": row["description"],
                        "expected_result": row["expected_result"],
                        "status": "draft",
                        "competencies": [
                            {
                                "competency_id": self.skills[r["skill_slug"]],
                                "required_value": r["required_value"],
                                "weight": r["weight"],
                            }
                            for r in json.loads(row["competencies_json"])
                        ],
                    },
                    key=key + "-create",
                )
            )
            self.case_ids[key] = case["id"]
            root = "/api/v1/admin/cases/" + case["id"]
            if case["status"] == "published":
                continue
            self.file(
                user,
                root,
                "task.md",
                decode(row["condition_md_base64"]),
                params={"expected_case_version": case["version"]},
                key=key + "-task",
            )
            case = self.request(user, "GET", root)
            reference_root = root + "/reference-solution"
            response = self.client.get(reference_root, headers=self.headers[user])
            if response.status_code == 404:
                self.request(
                    user,
                    "PUT",
                    reference_root,
                    params={"expected_case_version": case["version"]},
                    key=key + "-reference",
                )
            elif response.status_code != 200:
                raise RuntimeError("Cannot read reference solution")
            for filename, field in (
                ("solution.md", "reference_solution_md_base64"),
                ("checks.md", "checks_md_base64"),
            ):
                case = self.request(user, "GET", root)
                reference = self.request(user, "GET", reference_root)
                self.file(
                    user,
                    reference_root,
                    filename,
                    decode(row[field]),
                    params={
                        "expected_case_version": case["version"],
                        "expected_reference_version": reference["version"],
                    },
                    key=key + "-" + filename,
                )
            case = self.request(user, "GET", root)
            reference = self.request(user, "GET", reference_root)
            if not reference["ready"]:
                self.request(
                    user,
                    "PATCH",
                    reference_root,
                    body={
                        "expected_version": reference["version"],
                        "expected_commit_sha": reference["head_commit_sha"],
                        "ready": True,
                    },
                    params={"expected_case_version": case["version"]},
                    key=key + "-ready",
                )
            if row["status"] == "published":
                case = self.request(user, "GET", root)
                self.request(
                    user,
                    "PATCH",
                    root,
                    body={"expected_version": case["version"], "status": "published"},
                    key=key + "-publish",
                )
            print("Case:", row["title"])

    def submission(self, row):
        user, key = row["user_key"], row["key"]
        project = self.request(
            user,
            "POST",
            "/api/v1/me/projects",
            body={"case_id": self.case_ids[row["case_key"]]},
            key=key + "-project",
        )
        existing = [
            s
            for s in self.listing(user, "/api/v1/me/submissions")
            if s["project_id"] == project["id"]
        ]
        if existing:
            if len(existing) != 1:
                raise ValueError("Ambiguous seed submission")
            submission = existing[0]
        else:
            root = "/api/v1/projects/" + project["id"]
            self.file(
                user,
                root,
                "solution.md",
                decode(row["solution_md_base64"]),
                key=key + "-solution",
            )
            project = self.request(user, "GET", root)
            submission = self.request(
                user,
                "POST",
                root + "/submissions",
                body={
                    "expected_commit_sha": project["head_commit_sha"],
                    "summary": row["summary"],
                },
                key=key + "-submit",
            )
        self.submission_ids[key] = submission["id"]

    def review(self, row):
        user, key = row["reviewer_key"], row["key"]
        submission = self.submission_ids[row["submission_key"]]
        existing = [
            r
            for r in self.listing(user, "/api/v1/me/reviews")
            if r["submission_id"] == submission
        ]
        if existing:
            if len(existing) != 1:
                raise ValueError("Ambiguous seed review")
            return
        self.request(
            user,
            "PUT",
            "/api/v1/me/review-mode",
            body={"enabled": True},
            key=user + "-review-mode",
        )
        assignment = self.request(
            user, "POST", "/api/v1/me/review-assignments", key=key + "-claim"
        )
        if assignment["submission_id"] != submission or assignment["kind"] != "review":
            raise RuntimeError(
                "Review queue returned another submission; seed review was not written"
            )
        root = "/api/v1/review-assignments/" + assignment["id"]
        draft = self.request(user, "GET", root + "/draft")
        draft = self.request(
            user,
            "PUT",
            root + "/draft",
            body={
                "expected_version": draft["version"],
                "score": int(row["score"]),
                "verdict": row["verdict"],
                "feedback": {
                    field: row[field]
                    for field in ("strengths", "problems", "next_steps")
                },
                "comments": json.loads(row["comments_json"]),
            },
            key=key + f"-draft-{draft['version']}",
        )
        self.request(
            user,
            "POST",
            root + "/reviews",
            body={"expected_draft_version": draft["version"]},
            key=key + "-complete",
        )
        print("Review:", key)

    def run(self):
        self.accounts()
        self.cases()
        reviews = {row["submission_key"]: row for row in self.data["reviews"]}
        for row in self.data["submissions"]:
            self.submission(row)
            if row["key"] in reviews:
                self.review(reviews[row["key"]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8081")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    data = read_bundle(args.data_dir)
    if not args.validate_only:
        import httpx

        key = os.environ.get("SEEDDATA_API_KEY", "")
        if len(key) < 32:
            raise ValueError("Set SEEDDATA_API_KEY to match server.py")
        with httpx.Client(
            base_url=args.base_url, timeout=120, trust_env=False, follow_redirects=False
        ) as client:
            response = client.get("/seeddata/ready", headers={"X-Seeddata-Key": key})
            if response.status_code != 200:
                raise RuntimeError(
                    "Start server.py with the matching seed key before loading"
                )
            Loader(client, data).run()
    print(", ".join(f"{name}={len(rows)}" for name, rows in sorted(data.items())))


if __name__ == "__main__":
    main()
