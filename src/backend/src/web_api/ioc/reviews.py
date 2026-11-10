from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.review_queue import DefaultReviewStore
from src.application.review.usecases import (
    CancelReviewAssignment,
    ClaimReviewAssignment,
    SaveReviewDraft,
    SetReviewMode,
)
from src.application.review.workspace import ReviewQueries, ReviewStore


class ReviewProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def store(self, session: AsyncSession) -> DefaultReviewStore:
        return DefaultReviewStore(session)

    store_port = alias(DefaultReviewStore, provides=ReviewStore)
    workspace = provide(ReviewQueries, scope=Scope.REQUEST)

    set_review_mode = provide(SetReviewMode, scope=Scope.REQUEST)
    claim_review_assignment = provide(ClaimReviewAssignment, scope=Scope.REQUEST)
    cancel_review_assignment = provide(CancelReviewAssignment, scope=Scope.REQUEST)
    save_review_draft = provide(SaveReviewDraft, scope=Scope.REQUEST)
