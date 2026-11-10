from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.application.common.entity import Entity, to_entity
from src.application.common.errors import EntityAlreadyDeletedError


@to_entity
class DummyEntity(Entity[UUID]):
    name: str


def test_entity_allows_not_deleted_entities() -> None:
    entity = DummyEntity(
        id=uuid4(),
        created_at=datetime.now(tz=UTC),
        name="Alice",
    )

    entity.ensure_not_deleted()


def test_entity_raise_for_deleted_entities() -> None:
    entity = DummyEntity(
        id=uuid4(),
        created_at=datetime.now(tz=UTC),
        deleted_at=datetime.now(tz=UTC),
        name="Bob",
    )

    with pytest.raises(EntityAlreadyDeletedError) as exc_info:
        entity.ensure_not_deleted()

    assert str(exc_info.value) == "Entity 'DummyEntity' already deleted"
