from dataclasses import dataclass

from .client import SageError


@dataclass(frozen=True)
class Change:
    resource: object
    action: str
    before: dict | None


def plan(client, resources):
    client.verify_tenant()
    # Preflight ALL objects before making any mutations, including dependent ones.
    for resource in resources:
        resource.preflight(client)
    changes = []
    for resource in resources:
        remote = resource.read(client)
        action = (
            "create"
            if remote is None
            else ("noop" if resource.project(remote) == resource.data else "update")
        )
        changes.append(Change(resource, action, remote))
    return changes


def apply(client, changes, *, allow_update=False):
    client.verify_tenant()
    if not allow_update and any(c.action == "update" for c in changes):
        raise SageError(
            "Existing object drift detected; review plan and explicitly pass --allow-update"
        )
    for change in changes:
        change.resource.preflight(client)
    for change in changes:
        resource = change.resource
        current = resource.read(client)
        # Compare full snapshots, including server identity, before any write.
        if current != change.before:
            raise SageError(
                f"Concurrent change to {resource.key}; re-plan (no automatic overwrite)"
            )
        if change.action == "create":
            resource.create(client)
        elif change.action == "update":
            resource.update(client, current)
        if change.action != "noop":
            remote = resource.read(client)
            if remote is None or resource.project(remote) != resource.data:
                raise SageError(
                    f"Read-after-write verification failed for {resource.key}; re-plan"
                )
