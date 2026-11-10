from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.data_gateways.quality_views import DefaultQualityReader
from src.adapters.data_gateways.review_quality import DefaultReviewQuality
from src.application.review.quality import QualityReader, ReviewQualityQueries, ReviewQualityStore
from src.application.review.usecases import AssessReviewQuality, ReviseReview


class ReviewQualityProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def reader(self, session: AsyncSession) -> DefaultQualityReader:
        return DefaultQualityReader(session)

    reader_port = alias(DefaultQualityReader, provides=QualityReader)
    store = provide(DefaultReviewQuality, scope=Scope.REQUEST)
    store_port = alias(DefaultReviewQuality, provides=ReviewQualityStore)
    quality = provide(ReviewQualityQueries, scope=Scope.REQUEST)

    assess_review_quality = provide(AssessReviewQuality, scope=Scope.REQUEST)
    revise_review = provide(ReviseReview, scope=Scope.REQUEST)
