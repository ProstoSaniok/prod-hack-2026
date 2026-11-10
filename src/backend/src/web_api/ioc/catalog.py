from dishka import Provider, Scope, provide

from src.adapters.data_gateways.competency import DefaultCompetencyReader
from src.application.catalog.list_competencies import CompetencyReader, ListCompetencies


class CatalogProvider(Provider):
    reader = provide(DefaultCompetencyReader, provides=CompetencyReader, scope=Scope.REQUEST)
    interactor = provide(ListCompetencies, scope=Scope.REQUEST)
