from decimal import Decimal
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.application.project.snapshot_reading import SubmissionSide
from src.application.repository_storage.git import MAX_PATH_BYTES, validate_path
from src.application.review.entity import DiffSide, ReviewVerdict
from src.application.review.usecases import (
    CancelReviewAssignment,
    ClaimReviewAssignment,
    SaveReviewDraft,
    SetReviewMode,
)
from src.application.review.workspace import DraftComment, DraftContent, ReviewQueries
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key, LimitQuery, OffsetQuery, PathQuery
from src.web_api.http.responses import json_response, review_response
from src.web_api.http.routing import ReviewRoute
from src.web_api.http.validation import utf8_text


class ReviewModeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strengths: str = Field(max_length=6000)
    problems: str = Field(max_length=6000)
    next_steps: str = Field(max_length=6000)

    @field_validator("strengths", "problems", "next_steps")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return utf8_text(value)


class DraftCommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1000)
    path: str | None = Field(default=None, min_length=1, max_length=MAX_PATH_BYTES)
    line: int | None = Field(default=None, ge=1, strict=True)
    end_line: int | None = Field(default=None, ge=1, strict=True)
    side: DiffSide | None = None

    @field_validator("body")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Comment body cannot be blank")
        return utf8_text(value)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str | None) -> str | None:
        if value is not None:
            validate_path(value)
        return value


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    score: Decimal | None = Field(ge=0, le=100, allow_inf_nan=False)
    verdict: ReviewVerdict | None
    feedback: FeedbackInput
    comments: list[DraftCommentInput] = Field(max_length=20)

    @field_validator("score", mode="before")
    @classmethod
    def numeric_score(cls, value: object) -> object:
        if value is not None and type(value) not in {int, Decimal}:
            raise ValueError("Score must be a JSON number or null")
        return value

    def content(self) -> DraftContent:
        return DraftContent(
            expected_version=self.expected_version,
            score=self.score,
            verdict=self.verdict,
            strengths=self.feedback.strengths,
            problems=self.feedback.problems,
            next_steps=self.feedback.next_steps,
            comments=tuple(
                DraftComment(
                    body=comment.body,
                    path=comment.path,
                    line=comment.line,
                    side=comment.side,
                    end_line=comment.end_line,
                )
                for comment in self.comments
            ),
        )


router = APIRouter(
    route_class=ReviewRoute,
    prefix="/api/v1",
    tags=["Рецензирование"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.get("/me/review-mode")
async def mode(workspace: FromDishka[ReviewQueries]) -> Response:
    return json_response(await workspace.mode())


@router.put("/me/review-mode")
async def set_mode(body: ReviewModeInput, key: Key, interactor: FromDishka[SetReviewMode]) -> Response:
    return review_response(await interactor.execute(body.enabled, key))


@router.post(
    "/me/review-assignments",
    status_code=201,
    responses={200: {"description": "Existing assignment or replay"}, 204: {"description": "No eligible assignment"}},
)
async def claim(key: Key, interactor: FromDishka[ClaimReviewAssignment]) -> Response:
    return review_response(await interactor.execute(key))


@router.get("/me/review-assignments")
async def assignments(
    workspace: FromDishka[ReviewQueries], limit: LimitQuery = 20, offset: OffsetQuery = 0
) -> Response:
    return json_response(await workspace.assignments(limit=limit, offset=offset))


@router.get("/review-assignments/{assignment_id}")
async def assignment(assignment_id: UUID, workspace: FromDishka[ReviewQueries]) -> Response:
    return json_response(await workspace.assignment(assignment_id))


@router.delete("/review-assignments/{assignment_id}", status_code=204)
async def cancel_assignment(assignment_id: UUID, key: Key, interactor: FromDishka[CancelReviewAssignment]) -> Response:
    return review_response(await interactor.execute(assignment_id, key))


@router.get("/review-assignments/{assignment_id}/workspace")
async def review_workspace(assignment_id: UUID, workspace: FromDishka[ReviewQueries]) -> Response:
    return json_response(await workspace.workspace(assignment_id))


@router.get("/review-assignments/{assignment_id}/draft")
async def draft(assignment_id: UUID, workspace: FromDishka[ReviewQueries]) -> Response:
    return json_response(await workspace.draft(assignment_id))


@router.put("/review-assignments/{assignment_id}/draft")
async def save_draft(
    assignment_id: UUID, body: DraftInput, key: Key, interactor: FromDishka[SaveReviewDraft]
) -> Response:
    return review_response(await interactor.execute(assignment_id, body.content(), key))


@router.get("/review-assignments/{assignment_id}/snapshot")
async def snapshot(assignment_id: UUID, workspace: FromDishka[ReviewQueries]) -> Response:
    return json_response(await workspace.snapshot(assignment_id), files=True)


@router.get("/review-assignments/{assignment_id}/files")
async def files(
    assignment_id: UUID,
    workspace: FromDishka[ReviewQueries],
    side: SubmissionSide = "head",
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await workspace.files(assignment_id, side=side, limit=limit, offset=offset), files=True)


@router.get("/review-assignments/{assignment_id}/files/content")
async def file_content(
    assignment_id: UUID, path: PathQuery, workspace: FromDishka[ReviewQueries], side: SubmissionSide = "head"
) -> Response:
    return json_response(await workspace.files(assignment_id, side=side, path=path), files=True)


@router.get("/review-assignments/{assignment_id}/diff")
async def diff(
    assignment_id: UUID,
    workspace: FromDishka[ReviewQueries],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await workspace.diff(assignment_id, limit, offset), files=True)


@router.get("/review-assignments/{assignment_id}/diff/lines")
async def diff_lines(
    assignment_id: UUID,
    path: PathQuery,
    workspace: FromDishka[ReviewQueries],
    limit: LimitQuery = 20,
    offset: OffsetQuery = 0,
) -> Response:
    return json_response(await workspace.diff(assignment_id, limit, offset, path), files=True)
