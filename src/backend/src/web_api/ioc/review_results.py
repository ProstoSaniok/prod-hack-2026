from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.review_completion import DefaultReviewCompletion
from src.adapters.data_gateways.review_effects import SqlReviewPublicationEffects
from src.adapters.data_gateways.review_results import DefaultReviewResultsReader
from src.application.review.finalization import PublicationEffects
from src.application.review.results import ReviewCompletionStore, ReviewResultsQueries, ReviewResultsReader
from src.application.review.usecases import CompleteReview


class ReviewResultsProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def reader(self, session: AsyncSession) -> DefaultReviewResultsReader:
        return DefaultReviewResultsReader(session)

    reader_port = alias(DefaultReviewResultsReader, provides=ReviewResultsReader)
    effects = provide(SqlReviewPublicationEffects, scope=Scope.REQUEST)
    effects_port = alias(SqlReviewPublicationEffects, provides=PublicationEffects)
    completion = provide(DefaultReviewCompletion, scope=Scope.REQUEST)
    completion_port = alias(DefaultReviewCompletion, provides=ReviewCompletionStore)
    results = provide(ReviewResultsQueries, scope=Scope.REQUEST)

    complete_review = provide(CompleteReview, scope=Scope.REQUEST)
