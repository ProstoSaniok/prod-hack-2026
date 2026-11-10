"""Bounded text-only HTTP boundary for catalogue, references and projects."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Depends, Query, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.application.catalog.management import CaseValidationError
from src.application.project.learning import Learning, LearningAction, LearningCommand
from src.application.project.reading import CatalogDifficulty, LearningReads, SubmissionSide
from src.application.project.usecases import SetReferenceReady
from src.application.repository_storage.git import (
    MAX_PATH_BYTES,
    MAX_TEXT_BYTES,
    validate_path,
)
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery, PathQuery, VersionQuery
from src.web_api.http.responses import json_response, present
from src.web_api.http.routing import LearningRoute


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=MAX_TEXT_BYTES)

    @field_validator("content")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        try:
            valid = len(value.encode("utf-8")) <= MAX_TEXT_BYTES and "\x00" not in value
        except UnicodeError:
            valid = False
        if not valid:
            raise ValueError("Expected UTF-8 text of at most 1 MiB without NUL")
        return value


class CreateFileInput(TextInput):
    path: str = Field(min_length=1, max_length=MAX_PATH_BYTES)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        validate_path(value)
        return value


Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
FileKind = Literal["materials", "reference", "published", "project"]


class ReplaceFileInput(TextInput):
    sha: Sha


class StartProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID


class SubmitProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_commit_sha: Sha
    summary: str | None = Field(default=None, max_length=10_000)

    @field_validator("summary", mode="before", json_schema_input_type=str)
    @classmethod
    def nonnull_summary(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit summary instead of sending null")
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Expected UTF-8 summary") from None
            if "\x00" in value:
                raise ValueError("Summary cannot contain NUL")
        return value


class ReferenceReadyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    expected_commit_sha: Sha
    ready: bool = Field(strict=True)


router = APIRouter(
    route_class=LearningRoute,
    prefix="/api/v1",
    tags=["Проекты"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.get("/cases")
async def catalogue(
    reads: FromDishka[LearningReads],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
    q: Annotated[str, Query(max_length=500)] = "",
    competency_ids: Annotated[str, Query(max_length=3699)] = "",
    difficulty: CatalogDifficulty | None = None,
) -> Response:
    try:
        ids = tuple(dict.fromkeys(UUID(item) for item in competency_ids.split(","))) if competency_ids else ()
    except ValueError:
        raise CaseValidationError("Invalid catalogue filters") from None
    if len(ids) > 100 or "\x00" in q:
        raise CaseValidationError("Invalid catalogue filters")
    return json_response(
        await reads.catalogue(limit=limit, offset=offset, q=q, competency_ids=ids, difficulty=difficulty)
    )


@router.get("/cases/{case_id}")
async def case(case_id: UUID, reads: FromDishka[LearningReads]) -> Response:
    return json_response(await reads.case(case_id))


@router.get("/me/projects")
async def projects(reads: FromDishka[LearningReads], limit: LimitQuery = 20, offset: OffsetQuery = 0) -> Response:
    return json_response(await reads.projects(limit=limit, offset=offset))


@router.get("/projects/{project_id}")
async def project(project_id: UUID, reads: FromDishka[LearningReads]) -> Response:
    return json_response(await reads.project(project_id))


@router.post("/me/projects", status_code=201, responses={200: {"description": "Existing project or replay"}})
async def start_project(body: StartProjectInput, key: Key, learning: FromDishka[Learning]) -> Response:
    return present(await learning.execute(LearningCommand(action=LearningAction.START, target_id=body.case_id), key))


@router.post(
    "/projects/{project_id}/submissions",
    status_code=201,
    responses={200: {"description": "Existing submission or replay"}},
)
async def submit_project(
    project_id: UUID,
    body: SubmitProjectInput,
    key: Key,
    learning: FromDishka[Learning],
) -> Response:
    return present(
        await learning.execute(
            LearningCommand(
                action=LearningAction.SUBMIT,
                target_id=project_id,
                expected_commit_sha=body.expected_commit_sha,
                summary=body.summary,
            ),
            key,
        )
    )


@router.get("/me/submissions")
async def submissions(reads: FromDishka[LearningReads], limit: LimitQuery = 20, offset: OffsetQuery = 0) -> Response:
    return json_response(await reads.submissions(limit, offset))


@router.get("/submissions/{submission_id}")
async def submission(submission_id: UUID, reads: FromDishka[LearningReads]) -> Response:
    return json_response(await reads.submission(submission_id))


@router.get("/submissions/{submission_id}/snapshot")
async def submission_snapshot(submission_id: UUID, reads: FromDishka[LearningReads]) -> Response:
    return json_response(await reads.submission_snapshot(submission_id), files=True)


@router.get("/submissions/{submission_id}/files")
async def submission_files(
    submission_id: UUID,
    reads: FromDishka[LearningReads],
    side: SubmissionSide = "head",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await reads.submission_files(submission_id, side=side, limit=limit, offset=offset), files=True)


@router.get("/submissions/{submission_id}/files/content")
async def submission_content(
    submission_id: UUID,
    path: PathQuery,
    reads: FromDishka[LearningReads],
    side: SubmissionSide = "head",
) -> Response:
    return json_response(await reads.submission_files(submission_id, side=side, path=path), files=True)


@router.get("/submissions/{submission_id}/diff")
async def submission_diff(
    submission_id: UUID,
    reads: FromDishka[LearningReads],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await reads.submission_diff(submission_id, limit, offset), files=True)


@router.get("/submissions/{submission_id}/diff/lines")
async def submission_diff_lines(
    submission_id: UUID,
    path: PathQuery,
    reads: FromDishka[LearningReads],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await reads.submission_diff(submission_id, limit, offset, path), files=True)


@router.get("/admin/cases/{case_id}/reference-solution")
async def reference(case_id: UUID, reads: FromDishka[LearningReads]) -> Response:
    return json_response(await reads.reference(case_id))


@router.put("/admin/cases/{case_id}/reference-solution")
async def initialize_reference(
    case_id: UUID, expected_case_version: VersionQuery, key: Key, learning: FromDishka[Learning]
) -> Response:
    return present(
        await learning.execute(
            LearningCommand(
                action=LearningAction.REFERENCE_INIT, target_id=case_id, expected_version=expected_case_version
            ),
            key,
        )
    )


@router.patch("/admin/cases/{case_id}/reference-solution")
async def reference_ready(
    case_id: UUID,
    body: ReferenceReadyInput,
    expected_case_version: VersionQuery,
    key: Key,
    interactor: FromDishka[SetReferenceReady],
) -> Response:
    return json_response(
        await interactor.execute(
            case_id=case_id,
            expected_case_version=expected_case_version,
            expected_version=body.expected_version,
            expected_commit_sha=body.expected_commit_sha,
            ready=body.ready,
            key=key,
        )
    )


@dataclass(frozen=True)
class FileVersions:
    case: int | None = None
    reference: int | None = None


async def materials_versions(expected_case_version: VersionQuery) -> FileVersions:
    return FileVersions(case=expected_case_version)


async def reference_versions(
    expected_case_version: VersionQuery, expected_reference_version: VersionQuery
) -> FileVersions:
    return FileVersions(case=expected_case_version, reference=expected_reference_version)


async def project_versions() -> FileVersions:
    return FileVersions()


def file_routes(prefix: str, kind: FileKind) -> None:
    """Share file handlers while keeping version requirements explicit in OpenAPI."""

    async def listing(target_id: UUID, reads: FromDishka[LearningReads]) -> Response:
        return json_response(await reads.files(target_id, kind), files=True)

    async def content(target_id: UUID, path: PathQuery, reads: FromDishka[LearningReads]) -> Response:
        validate_path(path)
        return json_response(await reads.files(target_id, kind, path), files=True)

    router.add_api_route(prefix + "/files", listing, methods=["GET"], name=f"{kind}_files")
    router.add_api_route(prefix + "/files/content", content, methods=["GET"], name=f"{kind}_file_content")
    if kind == "published":
        return

    versions_dependency = {
        "materials": materials_versions,
        "reference": reference_versions,
        "project": project_versions,
    }[kind]
    actions = {
        "materials": (LearningAction.MATERIAL_CREATE, LearningAction.MATERIAL_REPLACE, LearningAction.MATERIAL_DELETE),
        "reference": (
            LearningAction.REFERENCE_CREATE,
            LearningAction.REFERENCE_REPLACE,
            LearningAction.REFERENCE_DELETE,
        ),
        "project": (LearningAction.FILE_CREATE, LearningAction.FILE_REPLACE, LearningAction.FILE_DELETE),
    }[kind]

    async def create(
        target_id: UUID,
        body: CreateFileInput,
        key: Key,
        learning: FromDishka[Learning],
        versions: Annotated[FileVersions, Depends(versions_dependency)],
    ) -> Response:
        return present(
            await learning.execute(
                LearningCommand(
                    action=actions[0],
                    target_id=target_id,
                    path=body.path,
                    content=body.content,
                    expected_version=versions.case,
                    expected_reference_version=versions.reference,
                ),
                key,
            )
        )

    async def replace(
        target_id: UUID,
        body: ReplaceFileInput,
        path: PathQuery,
        key: Key,
        learning: FromDishka[Learning],
        versions: Annotated[FileVersions, Depends(versions_dependency)],
    ) -> Response:
        return present(
            await learning.execute(
                LearningCommand(
                    action=actions[1],
                    target_id=target_id,
                    path=path,
                    content=body.content,
                    sha=body.sha,
                    expected_version=versions.case,
                    expected_reference_version=versions.reference,
                ),
                key,
            )
        )

    async def delete(
        target_id: UUID,
        path: PathQuery,
        sha: Annotated[str, Query(pattern=r"^[0-9a-f]{40}$")],
        key: Key,
        learning: FromDishka[Learning],
        versions: Annotated[FileVersions, Depends(versions_dependency)],
    ) -> Response:
        return present(
            await learning.execute(
                LearningCommand(
                    action=actions[2],
                    target_id=target_id,
                    path=path,
                    sha=sha,
                    expected_version=versions.case,
                    expected_reference_version=versions.reference,
                ),
                key,
            )
        )

    router.add_api_route(
        prefix + "/files",
        create,
        methods=["POST"],
        name=f"{kind}_file_create",
        status_code=201,
        description="Create a UTF-8 text file (JSON only, at most 1 MiB; multipart/binary uploads are not supported).",
    )
    router.add_api_route(prefix + "/files/content", replace, methods=["PUT"], name=f"{kind}_file_replace")
    router.add_api_route(prefix + "/files/content", delete, methods=["DELETE"], name=f"{kind}_file_delete")


file_routes("/cases/{target_id}", "published")
file_routes("/admin/cases/{target_id}", "materials")
file_routes("/admin/cases/{target_id}/reference-solution", "reference")
file_routes("/projects/{target_id}", "project")
