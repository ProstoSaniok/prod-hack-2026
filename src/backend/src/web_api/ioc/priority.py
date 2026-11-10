from dishka import Provider, Scope, alias, provide

from src.adapters.data_gateways.priority import DefaultPriorityStore
from src.application.review.priority import PriorityQueries, PriorityStore
from src.application.review.usecases import CancelReviewPriority, PurchaseReviewPriority


class PriorityProvider(Provider):
    store = provide(DefaultPriorityStore, scope=Scope.REQUEST)
    store_port = alias(DefaultPriorityStore, provides=PriorityStore)
    usecase = provide(PriorityQueries, scope=Scope.REQUEST)

    purchase_review_priority = provide(PurchaseReviewPriority, scope=Scope.REQUEST)
    cancel_review_priority = provide(CancelReviewPriority, scope=Scope.REQUEST)
