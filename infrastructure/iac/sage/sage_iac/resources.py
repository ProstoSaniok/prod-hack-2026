from dataclasses import dataclass
import math
from urllib.parse import quote

from .client import SageError, TENANT


@dataclass(frozen=True)
class Resource:
    key: str
    data: dict
    depends_on: tuple = ()

    def project(self, remote):
        return {key: remote.get(key) for key in self.data}

    def preflight(self, client):
        pass


class Dashboard(Resource):
    """Sage service-topology dashboard; not a Grafana metrics dashboard."""

    def validate(self):
        if set(self.data) - {"name", "tenant", "zoom", "offsetX", "offsetY"}:
            raise SageError(
                "Unsupported dashboard field; manage services/layout through a separate resource"
            )
        if (
            self.data.get("tenant") != TENANT
            or not isinstance(self.data.get("name"), str)
            or not self.data["name"].startswith("prodguild-")
        ):
            raise SageError(
                "Dashboards must belong to hack-led-03 and use the prodguild- name prefix"
            )
        for field in ("zoom", "offsetX", "offsetY"):
            if field in self.data and (
                type(self.data[field]) not in (int, float)
                or not math.isfinite(self.data[field])
            ):
                raise SageError("Dashboard coordinates and zoom must be finite numbers")

    def read(self, client):
        matches = []
        for page in range(1, 1001):
            response = client.request(
                "GET",
                "/dtracing-graph-api/v2/dashboards",
                query={
                    "tenant": TENANT,
                    "query": self.data["name"],
                    "page": page,
                    "perPage": 50,
                    "myOnly": "false",
                },
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("items"), list
            ):
                raise SageError("Unexpected Sage dashboard response schema")
            items = response["items"]
            if any(not isinstance(item, dict) for item in items):
                raise SageError("Unexpected Sage dashboard item schema")
            matches.extend(
                item
                for item in items
                if item.get("tenant") == TENANT
                and item.get("name") == self.data["name"]
            )
            if page * 50 >= response["pagination"]["total"]:
                break
        else:
            raise SageError("Dashboard pagination exceeded safety limit")
        if len(matches) > 1:
            raise SageError("Ambiguous dashboard identity; refusing mutation")
        return matches[0] if matches else None

    def create(self, client):
        client.request("POST", "/dtracing-graph-api/dashboards", body=self.data)

    def update(self, client, remote):
        identity = quote(remote["uuid"], safe="")
        client.request(
            "PATCH",
            f"/dtracing-graph-api/dashboards/{identity}",
            body={k: v for k, v in self.data.items() if k != "tenant"},
        )


class MetricsResource(Resource):
    endpoint = ""
    read_endpoint = None
    identity_fields = ()
    required = ()
    allowed = ()

    def validate(self):
        if set(self.data) - set(self.allowed) or any(
            key not in self.data for key in self.required
        ):
            raise SageError(f"Invalid fields in {self.key}")
        if any(
            not isinstance(self.data[k], str) or not self.data[k]
            for k in self.identity_fields
        ):
            raise SageError("Metrics identity fields must be nonempty strings")
        name = self.data.get(self.identity_fields[-1], "")
        if not isinstance(name, str) or not name.startswith("prodguild-"):
            raise SageError(
                "Managed metrics object names must use the prodguild- prefix"
            )
        if (
            not isinstance(self.data.get("groupName"), str)
            or not self.data["groupName"]
        ):
            raise SageError(
                "Metrics resources require an explicit authorized groupName"
            )

    def preflight(self, client):
        client.verify_group(self.data["groupName"])

    def read(self, client):
        items = client.request(
            "GET",
            self.read_endpoint or self.endpoint,
            query={"groupName": self.data["groupName"]},
        )
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise SageError(f"Unexpected metrics response schema for {self.key}")
        matches = [
            item
            for item in items
            if all(item.get(k) == self.data[k] for k in self.identity_fields)
        ]
        if len(matches) > 1:
            raise SageError(f"Ambiguous identity for {self.key}")
        return matches[0] if matches else None

    def create(self, client):
        client.request("POST", self.endpoint, body=self.data)

    def update(self, client, remote):
        client.request("PUT", self.endpoint, body=self.data)


class Environment(MetricsResource):
    endpoint = "/mcc/api/Environment"
    identity_fields = ("groupName", "envName")
    required = allowed = identity_fields


class System(MetricsResource):
    endpoint = "/mcc/api/System"
    identity_fields = ("groupName", "systemName")
    required = (*identity_fields, "port")
    allowed = (
        *required,
        "staticLabels",
        "relabelConfigs",
        "scrapeInterval",
        "scrapeTimeout",
        "metricsPath",
        "perTargetLimit",
    )

    def validate(self):
        super().validate()
        if type(self.data["port"]) is not int or not 1 <= self.data["port"] <= 65535:
            raise SageError("System port must be between 1 and 65535")
        interval = self.data.get("scrapeInterval", 30000)
        timeout = self.data.get("scrapeTimeout", 10000)
        if (
            type(interval) is not int
            or type(timeout) is not int
            or not 0 < timeout <= interval
        ):
            raise SageError(
                "Scrape timeout must be positive and not exceed the interval (milliseconds)"
            )


class ScrapeCollection(MetricsResource):
    endpoint = "/mcc/api/ScraperTarget/collection"
    read_endpoint = "/mcc/api/v2/ScraperTarget/collection"
    identity_fields = ("groupName", "systemName", "envName", "targetCollectionName")
    required = (*identity_fields, "enableFullHostName", "source")
    allowed = (
        *required,
        "metricsPath",
        "staticLabels",
        "port",
        "scheme",
        "perTargetLimit",
        "params",
        "relabelConfigs",
        "targetRelabelConfigs",
    )

    def validate(self):
        super().validate()
        if (
            self.data["source"] != "Manual"
            or type(self.data["enableFullHostName"]) is not bool
        ):
            raise SageError("Only explicitly manual scrape collections are supported")
        if "scheme" in self.data and self.data["scheme"] not in ("http", "https"):
            raise SageError("Invalid scrape scheme")


KINDS = {
    "dashboard": Dashboard,
    "environment": Environment,
    "system": System,
    "scrape_collection": ScrapeCollection,
}
