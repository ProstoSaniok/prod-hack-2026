import json
from pathlib import Path

from .client import SageError, TENANT
from .resources import KINDS


def load(filename):
    path = Path(filename).resolve()
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict) or set(manifest) != {
        "version",
        "tenant",
        "resources",
    }:
        raise SageError("Manifest must contain only version, tenant, and resources")
    if manifest.get("tenant") != TENANT or manifest.get("version") != 1:
        raise SageError("Manifest must have version 1 and tenant hack-led-03")
    resources = {}
    identities = set()
    if not isinstance(manifest["resources"], list):
        raise SageError("Manifest resources must be a list")
    for entry in manifest["resources"]:
        if not isinstance(entry, dict) or set(entry) - {
            "id",
            "kind",
            "definition",
            "depends_on",
        }:
            raise SageError("Invalid manifest resource entry")
        if any(
            not isinstance(entry.get(k), str) or not entry[k]
            for k in ("id", "kind", "definition")
        ):
            raise SageError(
                "Resource id, kind, and definition must be nonempty strings"
            )
        dependencies = entry.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(d, str) for d in dependencies
        ):
            raise SageError("Resource dependencies must be a list of IDs")
        key = entry["id"]
        if key in resources or entry["kind"] not in KINDS:
            raise SageError("Duplicate resource ID or unsupported kind")
        data_path = (path.parent / entry["definition"]).resolve()
        if not data_path.is_relative_to(path.parent):
            raise SageError(
                "Object definitions must stay inside the manifest directory"
            )
        data = json.loads(data_path.read_text())
        if not isinstance(data, dict):
            raise SageError("Object definition must be a JSON object")
        resource = KINDS[entry["kind"]](key, data, tuple(entry.get("depends_on", [])))
        resource.validate()
        fields = getattr(resource, "identity_fields", ("tenant", "name"))
        identity = (entry["kind"], *(data[field] for field in fields))
        if identity in identities:
            raise SageError("Two resource IDs must not manage the same remote object")
        identities.add(identity)
        resources[key] = resource
    ordered = []
    pending = dict(resources)
    while pending:
        ready = [
            r for r in pending.values() if set(r.depends_on) <= {o.key for o in ordered}
        ]
        if not ready:
            raise SageError("Missing or cyclic resource dependency")
        for resource in ready:
            ordered.append(resource)
            del pending[resource.key]
    return ordered
