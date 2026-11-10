import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from dishka import FromDishka
from fastapi import APIRouter, Query, Request, Response, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

from src.application.catalog.entity import CaseId, CaseStatus, CompetencyId
from src.application.catalog.management import (
    CaseChanges,
    CaseDifficulty,
    CaseMetadata,
    CaseQueries,
    CaseSkill,
    CaseValidationError,
    ManagedCase,
)
from src.application.catalog.usecases import CreateCase, DeleteCase, UpdateCase
from src.application.operations.idempotency import (
    CachedResponse,
)
from src.application.project.learning import Learning, LearningAction, LearningCommand
from src.web_api.case_requests import CaseRequests
from src.web_api.http.errors import CatalogErrorResponse
from src.web_api.http.parameters import Key
from src.web_api.http.responses import present
from src.web_api.http.routing import CaseRoute


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competency_id: UUID
    required_value: Annotated[
        Decimal,
        Field(ge=0, le=100, allow_inf_nan=False),
        WithJsonSchema({"type": "number", "minimum": 0, "maximum": 100}),
    ]
    weight: Annotated[
        Decimal,
        Field(gt=0, le=Decimal("0.25"), allow_inf_nan=False),
        WithJsonSchema({"type": "number", "exclusiveMinimum": 0, "maximum": 0.25}),
    ]

    def to_skill(self) -> CaseSkill:
        return CaseSkill(
            competency_id=CompetencyId(self.competency_id), required_value=self.required_value, weight=self.weight
        )


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    title: str = Field(max_length=500)
    description: str = Field(max_length=16_384)
    expected_result: str = Field(max_length=16_384)
    competencies: list[SkillInput] = Field(max_length=100)
    difficulty: CaseDifficulty | None = None
    status: Literal["draft"] = "draft"

    def metadata(self) -> CaseMetadata:
        return CaseMetadata(
            title=self.title,
            description=self.description,
            expected_result=self.expected_result,
            competencies=tuple(skill.to_skill() for skill in self.competencies),
        )


class UpdateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=16_384)
    expected_result: str | None = Field(default=None, max_length=16_384)
    competencies: list[SkillInput] | None = Field(default=None, max_length=100)
    difficulty: CaseDifficulty | None = None
    status: CaseStatus | None = None

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if self.model_fields_set == {"expected_version"} or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Supply at least one non-null change")
        return self

    def changes(self) -> CaseChanges:
        return CaseChanges(
            expected_version=self.expected_version,
            title=self.title,
            description=self.description,
            expected_result=self.expected_result,
            difficulty=self.difficulty,
            status=self.status,
            competencies=tuple(item.to_skill() for item in self.competencies)
            if self.competencies is not None
            else None,
        )


class CaseMetadataResponse(BaseModel):
    id: UUID
    version: int
    title: str
    description: str
    expected_result: str
    competencies: list[SkillInput]
    difficulty: CaseDifficulty
    status: CaseStatus
    frozen: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_case(cls, value: ManagedCase) -> Self:
        case, metadata = value.case, value.metadata
        return cls(
            id=case.id,
            version=case.version,
            title=metadata.title,
            description=metadata.description,
            expected_result=metadata.expected_result,
            competencies=[
                SkillInput(
                    competency_id=item.competency_id,
                    required_value=item.required_value,
                    weight=item.weight,
                )
                for item in metadata.competencies
            ],
            difficulty=metadata.difficulty,
            status=case.status,
            frozen=case.frozen_at is not None,
            created_at=case.created_at,
            updated_at=case.updated_at or case.created_at,
        )

    def exact_json(self) -> bytes:
        # Preserve Decimal skill facts as JSON numbers, not rounded binary floats.
        skills = ",".join(
            '{"competency_id":'
            + json.dumps(str(item.competency_id))
            + ',"required_value":'
            + str(item.required_value)
            + ',"weight":'
            + str(item.weight)
            + "}"
            for item in self.competencies
        )
        return (self.model_dump_json(exclude={"competencies"})[:-1] + ',"competencies":[' + skills + "]}").encode()


router = APIRouter(
    route_class=CaseRoute,
    prefix="/api/v1/admin/cases",
    tags=["Организатор"],
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="OpaqueBearer"))],
    responses={code: {"model": CatalogErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)


@router.get("", response_model=list[CaseMetadataResponse])
async def list_cases(
    cases: FromDishka[CaseQueries],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: CaseStatus | None = None,
) -> Response:
    values = await cases.list_cases(limit=limit, offset=offset, status=status)
    return Response(
        content=b"[" + b",".join(CaseMetadataResponse.from_case(item).exact_json() for item in values) + b"]",
        media_type="application/json",
    )


@router.get("/{case_id}", response_model=CaseMetadataResponse)
async def get_case(case_id: UUID, cases: FromDishka[CaseQueries]) -> Response:
    return Response(
        content=CaseMetadataResponse.from_case(await cases.get_case(CaseId(case_id))).exact_json(),
        media_type="application/json",
    )


@router.post("", response_model=CaseMetadataResponse, status_code=201, responses={200: {"model": CaseMetadataResponse}})
async def create_case(
    body: CreateCaseRequest,
    request: Request,
    key: Key,
    interactor: FromDishka[CreateCase],
    requests: FromDishka[CaseRequests],
) -> Response:
    handler, record = await requests.begin(request, key)
    if isinstance(record, CachedResponse):
        return present(record, replay=True)
    value, created = await interactor.execute(body.metadata(), body.difficulty, CaseId(body.id) if body.id else None)
    response = CachedResponse(
        status_code=201 if created else 200,
        body=CaseMetadataResponse.from_case(value).exact_json(),
        location=f"/api/v1/admin/cases/{value.case.id}",
    )
    return await requests.finish(handler, record, response)


@router.patch("/{case_id}", response_model=CaseMetadataResponse)
async def update_case(
    case_id: UUID,
    body: UpdateCaseRequest,
    request: Request,
    key: Key,
    interactor: FromDishka[UpdateCase],
    requests: FromDishka[CaseRequests],
    learning: FromDishka[Learning],
) -> Response:
    if body.status == CaseStatus.PUBLISHED:
        if body.model_fields_set != {"expected_version", "status"}:
            raise CaseValidationError("Publish separately from metadata edits")
        return present(
            await learning.execute(
                LearningCommand(
                    action=LearningAction.PUBLISH, target_id=case_id, expected_version=body.expected_version
                ),
                key,
            )
        )
    handler, record = await requests.begin(request, key, CaseId(case_id))
    if isinstance(record, CachedResponse):
        return present(record, replay=True)
    value = await interactor.execute(CaseId(case_id), body.changes())
    return await requests.finish(
        handler, record, CachedResponse(status_code=200, body=CaseMetadataResponse.from_case(value).exact_json())
    )


@router.delete("/{case_id}", status_code=204)
async def delete_case(
    case_id: UUID,
    expected_version: Annotated[int, Query(ge=1)],
    request: Request,
    key: Key,
    interactor: FromDishka[DeleteCase],
    requests: FromDishka[CaseRequests],
) -> Response:
    handler, record = await requests.begin(request, key, CaseId(case_id))
    if isinstance(record, CachedResponse):
        return present(record, replay=True)
    await interactor.execute(CaseId(case_id), expected_version)
    return await requests.finish(handler, record, CachedResponse(status_code=204, body=b""))
