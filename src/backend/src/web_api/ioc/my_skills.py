from dishka import Provider, Scope, provide

from src.adapters.data_gateways.my_skills import DefaultMySkillsReader
from src.application.development.list_my_skills import ListMySkills, MySkillsReader


class MySkillsProvider(Provider):
    reader = provide(DefaultMySkillsReader, provides=MySkillsReader, scope=Scope.REQUEST)
    interactor = provide(ListMySkills, scope=Scope.REQUEST)
