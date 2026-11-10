from dishka import STRICT_VALIDATION, AsyncContainer, Provider, make_async_container
from dishka.integrations.fastapi import FastapiProvider

from src.adapters.gitea.config import GiteaConfiguration
from src.web_api.configuration import AccessTokenConfiguration, Configuration, DatabaseConfiguration
from src.web_api.ioc.auth import AuthProvider
from src.web_api.ioc.cases import CaseProvider
from src.web_api.ioc.catalog import CatalogProvider
from src.web_api.ioc.connection import ConnectionProvider
from src.web_api.ioc.development import DevelopmentProvider
from src.web_api.ioc.learning import LearningProvider
from src.web_api.ioc.my_skills import MySkillsProvider
from src.web_api.ioc.priority import PriorityProvider
from src.web_api.ioc.profile import ProfileProvider
from src.web_api.ioc.repository_storage import RepositoryStorageProvider
from src.web_api.ioc.review_projection import ReviewProjectionProvider
from src.web_api.ioc.review_quality import ReviewQualityProvider
from src.web_api.ioc.review_results import ReviewResultsProvider
from src.web_api.ioc.reviews import ReviewProvider
from src.web_api.ioc.wallet import WalletProvider


def make_ioc(configuration: Configuration, *providers: Provider) -> AsyncContainer:
    return make_async_container(
        FastapiProvider(),
        ConnectionProvider(),
        AuthProvider(),
        CaseProvider(),
        LearningProvider(),
        ReviewProvider(),
        ReviewResultsProvider(),
        ReviewQualityProvider(),
        ReviewProjectionProvider(),
        PriorityProvider(),
        ProfileProvider(),
        DevelopmentProvider(),
        CatalogProvider(),
        MySkillsProvider(),
        RepositoryStorageProvider(),
        WalletProvider(),
        *providers,
        validation_settings=STRICT_VALIDATION,
        context={
            Configuration: configuration,
            DatabaseConfiguration: configuration.database,
            AccessTokenConfiguration: configuration.access_token,
            GiteaConfiguration: configuration.gitea,
        },
    )
