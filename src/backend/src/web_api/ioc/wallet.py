from dishka import Provider, Scope, provide

from src.adapters.data_gateways.wallet_reader import DefaultWalletReader
from src.adapters.wallet.persistence import SqlAlchemyWalletTransaction
from src.application.wallet.ports import WalletTransaction
from src.application.wallet.queries import WalletQueries, WalletReader


class WalletProvider(Provider):
    transaction = provide(SqlAlchemyWalletTransaction, provides=WalletTransaction, scope=Scope.REQUEST)
    reader = provide(DefaultWalletReader, provides=WalletReader, scope=Scope.REQUEST)
    queries = provide(WalletQueries, scope=Scope.REQUEST)
