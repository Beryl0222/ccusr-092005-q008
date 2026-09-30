"""事件投影：把只追加日志重放成可读的目录、提案聚合与元素占用台账。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .model import Gate
from .store import Event

CATALOG_STREAM = "catalog"


@dataclass
class ArtworkElement:
    element_id: str
    name: str
    rights: dict[str, str]  # reproduction / commercial_adaptation -> granted/pending/denied


@dataclass
class Artwork:
    artwork_id: str
    title: str
    holder_ids: list[str]
    elements: dict[str, ArtworkElement]
    baseline_allowed_uses: list[str]


@dataclass
class Partner:
    partner_id: str
    name: str
    channels: list[str]


@dataclass
class Exhibition:
    exhibition_id: str
    opens_on: str
    closes_on: str
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Catalog:
    artworks: dict[str, Artwork] = field(default_factory=dict)
    partners: dict[str, Partner] = field(default_factory=dict)
    exhibitions: dict[str, Exhibition] = field(default_factory=dict)

    @staticmethod
    def apply(catalog: "Catalog", event: Event) -> "Catalog":
        p = event.payload
        if event.type == "artwork_registered":
            catalog.artworks[p["artwork_id"]] = Artwork(
                artwork_id=p["artwork_id"],
                title=p["title"],
                holder_ids=list(p["holder_ids"]),
                elements={
                    e["element_id"]: ArtworkElement(
                        element_id=e["element_id"],
                        name=e["name"],
                        rights=dict(e["rights"]),
                    )
                    for e in p["elements"]
                },
                baseline_allowed_uses=list(p.get("baseline_allowed_uses", [])),
            )
        elif event.type == "partner_registered":
            catalog.partners[p["partner_id"]] = Partner(
                partner_id=p["partner_id"], name=p["name"], channels=list(p["channels"])
            )
        elif event.type == "exhibition_scheduled":
            catalog.exhibitions[p["exhibition_id"]] = Exhibition(
                exhibition_id=p["exhibition_id"],
                opens_on=p["opens_on"],
                closes_on=p["closes_on"],
            )
        elif event.type == "exhibition_rescheduled":
            ex = catalog.exhibitions[p["exhibition_id"]]
            ex.history.append(
                {"old_opens_on": p["old_opens_on"], "new_opens_on": p["new_opens_on"],
                 "new_closes_on": p["new_closes_on"], "at": event.at}
            )
            ex.opens_on = p["new_opens_on"]
            ex.closes_on = p["new_closes_on"]
        return catalog


@dataclass
class GateDecision:
    gate: Gate
    status: str  # approved / rejected / carried / pending
    by: str | None = None
    at: str | None = None
    reason: str = ""
    design_hash: str = ""
    source_version_id: str | None = None  # carried 决定来自哪个旧版本


@dataclass
class VersionState:
    version_id: str
    version_no: int
    parent_version_id: str | None
    changed_facets: list[str]
    design: dict[str, Any]
    design_hash: str
    submitted_at: str
    submitted_by: str
    gates: dict[Gate, GateDecision] = field(default_factory=dict)
    license: dict[str, Any] | None = None
    reservation: dict[str, Any] | None = None
    releases: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Proposal:
    proposal_id: str
    exhibition_id: str
    partner_id: str
    created_at: str
    versions: list[VersionState] = field(default_factory=list)
    stage: str = "draft"
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispositions: list[dict[str, Any]] = field(default_factory=list)
    communications: list[dict[str, Any]] = field(default_factory=list)
    withdrawal: dict[str, Any] | None = None

    # ---- 便捷访问 ------------------------------------------------------
    @property
    def current(self) -> VersionState:
        return self.versions[-1]

    def version(self, version_id: str) -> VersionState:
        for v in self.versions:
            if v.version_id == version_id:
                return v
        raise KeyError(version_id)

    def gates_complete(self, v: VersionState) -> bool:
        return all(
            v.gates.get(g) is not None
            and v.gates[g].status in ("approved", "carried")
            for g in Gate
        )

    def any_rejected(self, v: VersionState) -> bool:
        return any(d.status == "rejected" for d in v.gates.values())

    def released_elements(self) -> set[str]:
        keys: set[str] = set()
        for v in self.versions:
            if v.releases:
                for ref in v.design["artwork_refs"]:
                    for eid in ref["element_ids"]:
                        keys.add(f"{ref['artwork_id']}/{eid}")
        return keys


def _new_version(p: Proposal, event: Event) -> VersionState:
    payload = event.payload
    v = VersionState(
        version_id=payload["version_id"],
        version_no=len(p.versions) + 1,
        parent_version_id=payload.get("parent_version_id"),
        changed_facets=list(payload.get("changed_facets", [])),
        design=payload["design"],
        design_hash=payload["design_hash"],
        submitted_at=event.at,
        submitted_by=event.actor,
    )
    p.versions.append(v)
    return v


def apply_proposal(p: Proposal, event: Event) -> Proposal:
    payload = event.payload
    if event.type == "proposal_created":
        v = _new_version(p, event)
        p.stage = "in_review"
    elif event.type == "design_submitted":
        v = _new_version(p, event)
        p.stage = "in_review"
        # 未被变更面失效的生效许可沿用到新版本（同一合同哈希与边界）
        src_id = payload.get("license_carried_from")
        if src_id is not None:
            src = p.version(src_id)
            if src.license is not None:
                v.license = deepcopy(src.license)
                v.license["carried_from_version_id"] = src_id
        # 沿用上一版本未受变更影响的批准
        for carried in payload.get("carried", []):
            g = Gate(carried["gate"])
            v.gates[g] = GateDecision(
                gate=g,
                status="carried",
                by=carried["by"],
                at=carried["at"],
                reason=carried.get("reason", "变更面不触及本门，沿用上一版本批准"),
                design_hash=v.design_hash,
                source_version_id=carried["source_version_id"],
            )
        if p.withdrawal is not None:
            p.stage = "suspended"
        elif p.gates_complete(v) and not p.any_rejected(v):
            # 所有门都以“沿用”方式通过（如纯文案订正）：无需重审即可继续
            p.stage = "approved"
    elif event.type == "gate_decision":
        v = p.version(payload["version_id"])
        g = Gate(payload["gate"])
        v.gates[g] = GateDecision(
            gate=g,
            status=payload["decision"],
            by=event.actor,
            at=event.at,
            reason=payload.get("reason", ""),
            design_hash=payload["design_hash"],
        )
        if p.current is v and p.any_rejected(v):
            p.stage = "in_review"
    elif event.type == "all_gates_approved":
        v = p.version(payload["version_id"])
        # 权利撤回是提案级冻结：即使补审通过也保持 suspended，由权利线显式解除
        if p.current is v and p.gates_complete(v) and p.withdrawal is None:
            p.stage = "approved"
    elif event.type == "reservation_decided":
        v = p.version(payload["version_id"])
        v.reservation = {
            "mode": payload["mode"],
            "granted": payload["granted"],
            "elements": [tuple(e) for e in payload["elements"]],
            "valid_until": payload["valid_until"],
            "at": event.at,
            "reason": payload.get("reason", ""),
        }
    elif event.type == "license_granted":
        v = p.version(payload["version_id"])
        v.license = {
            "holder_id": payload["holder_id"],
            "scope": payload["scope"],
            "contract_ref": payload["contract_ref"],
            "contract_hash": payload["contract_hash"],
            "granted_at": event.at,
            "status": "active",
        }
    elif event.type == "license_superseded":
        # 旧版本许可不删除，只标记被新版本取代——历史决定保持可查。
        v = p.version(payload["version_id"])
        if v.license is not None:
            v.license["status"] = "superseded"
    elif event.type == "sampling_started":
        v = p.version(payload["version_id"])
        v.samples.append({"quantity": payload["quantity"], "at": event.at})
    elif event.type == "production_released":
        v = p.version(payload["version_id"])
        v.releases.append(
            {
                "quantity": payload["quantity"],
                "channel": payload["channel"],
                "region": payload["region"],
                "at": event.at,
                "by": event.actor,
            }
        )
        if p.stage != "suspended":
            p.stage = "approved"
    elif event.type == "instances_registered":
        for inst in payload["instances"]:
            p.instances[inst["instance_id"]] = {
                "state": inst["state"],
                "quantity": inst.get("quantity", 1),
                "detail": inst.get("detail", ""),
            }
    elif event.type == "instance_disposition":
        p.dispositions.append(
            {
                "trigger": payload["trigger"],
                "instance_id": payload["instance_id"],
                "state": payload["state"],
                "action": payload["action"],
                "rationale": payload["rationale"],
                "at": event.at,
                "by": event.actor,
            }
        )
        if payload.get("new_state"):
            p.instances[payload["instance_id"]]["state"] = payload["new_state"]
    elif event.type == "rights_withdrawn":
        p.withdrawal = {
            "holder_id": payload["holder_id"],
            "scope": payload["scope"],
            "effective_on": payload["effective_on"],
            "reason": payload.get("reason", ""),
            "at": event.at,
        }
        cur = p.current
        if cur.license is not None:
            cur.license["status"] = "withdrawn"
        p.stage = "suspended"
    elif event.type == "proposal_suspended":
        p.stage = "suspended"
    elif event.type == "proposal_delisted":
        p.stage = "delisted"
    elif event.type == "communication_logged":
        p.communications.append(
            {
                "communication_id": payload["communication_id"],
                "thread": payload["thread"],
                "sender": event.actor,
                "participants": list(payload["participants"]),
                "visibility": payload["visibility"],
                "body": payload.get("body", ""),
                "body_hash": payload["body_hash"],
                "at": event.at,
            }
        )
    return p


@dataclass
class ElementClaim:
    element_key: str
    partner_id: str
    proposal_id: str
    version_id: str
    mode: str           # parallel_sample / exclusive_option
    released: bool
    valid_until: str


class ElementLedger:
    """按艺术元素汇总占用，用于竞争同一元素时的阻断/放行裁决。"""

    def __init__(self) -> None:
        self.claims: dict[str, list[ElementClaim]] = {}

    @staticmethod
    def apply(ledger: "ElementLedger", event: Event, today: str) -> "ElementLedger":
        p = event.payload
        if event.type == "reservation_decided" and p["granted"]:
            for artwork_id, element_id in p["elements"]:
                key = f"{artwork_id}/{element_id}"
                ledger.claims.setdefault(key, []).append(
                    ElementClaim(
                        element_key=key,
                        partner_id=event.payload["partner_id"],
                        proposal_id=event.stream.split(":", 1)[1],
                        version_id=p["version_id"],
                        mode=p["mode"],
                        released=False,
                        valid_until=p["valid_until"],
                    )
                )
        elif event.type == "production_released":
            pid = event.stream.split(":", 1)[1]
            # 已登记意向的占用标记为已量产；未登记直接量产也产生永久占用，
            # 从而兑现“两个合作方竞争同一元素时先量产者阻断后来者”。
            for artwork_id, element_id in p.get("elements", []):
                key = f"{artwork_id}/{element_id}"
                existing = next(
                    (c for c in ledger.claims.get(key, [])
                     if c.proposal_id == pid and c.version_id == p["version_id"]),
                    None,
                )
                if existing is not None:
                    existing.released = True
                else:
                    ledger.claims.setdefault(key, []).append(
                        ElementClaim(
                            element_key=key,
                            partner_id=p["partner_id"],
                            proposal_id=pid,
                            version_id=p["version_id"],
                            mode="parallel_sample",
                            released=True,
                            valid_until="9999-12-31",
                        )
                    )
        return ledger

    def active_claims(self, key: str, today: str) -> list[ElementClaim]:
        return [c for c in self.claims.get(key, []) if c.valid_until >= today]

    def exclusive_holder(self, key: str, today: str) -> ElementClaim | None:
        for c in self.active_claims(key, today):
            if c.mode == "exclusive_option":
                return c
        return None

    def released_claim(self, key: str, today: str) -> ElementClaim | None:
        for c in self.active_claims(key, today):
            if c.released:
                return c
        return None
