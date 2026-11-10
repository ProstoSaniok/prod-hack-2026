from dishka import Provider, Scope, provide

from src.adapters.data_gateways.profile import SqlProfileReader
from src.application.profile.queries import ProfileQueries, ProfileReader


class ProfileProvider(Provider):
    reader = provide(SqlProfileReader, provides=ProfileReader, scope=Scope.REQUEST)
    queries = provide(ProfileQueries, scope=Scope.REQUEST)
