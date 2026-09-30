"""测试夹具：固定时钟 + 已入库的一件作品、两个合作方、一个展览。"""
from __future__ import annotations

from museum_collab.model import (
    DesignContent,
    ElementRef,
    Gate,
    LicenseScope,
    Purpose,
    RefMode,
)
from museum_collab.system import Actor, System
from museum_collab.store import EventStore

TODAY = "2026-08-01T09:00:00+00:00"

STAFF = {
    "curator": Actor("u-curator", "curator", name="策展人"),
    "rights": Actor("u-rights", "rights", name="权利专员"),
    "benefit": Actor("u-benefit", "benefit", name="公益专员"),
    "business": Actor("u-business", "business", name="经营专员"),
    "producer": Actor("u-producer", "producer", name="运营生产"),
    "audit": Actor("u-audit", "curator_audit", name="策展溯源"),
}


def build_system(clock: list[str] | None = None) -> System:
    store = EventStore(clock=lambda: clock[0] if clock else TODAY)
    sys = System(store, clock=lambda: clock[0] if clock else TODAY)
    staff = STAFF["curator"]
    sys.register_artwork(
        staff,
        artwork_id="artwork-river-17",
        title="江行图第十七开",
        holder_ids=["artist-chen", "heir-agent"],
        elements=[
            {"element_id": "main-figure", "name": "主山体轮廓",
             "rights": {"reproduction": "granted", "commercial_adaptation": "pending"}},
            {"element_id": "wave-pattern", "name": "水纹母题",
             "rights": {"reproduction": "pending", "commercial_adaptation": "pending"}},
        ],
        baseline_allowed_uses=["展览宣传"],
    )
    sys.register_partner(staff, partner_id="partner-a", name="甲文创设计",
                         channels=["museum-shop", "online-mall"])
    sys.register_partner(staff, partner_id="partner-b", name="乙文创工坊",
                         channels=["museum-shop", "campus-pop-up"])
    sys.schedule_exhibition(staff, exhibition_id="exh-2026-modern",
                            opens_on="2026-10-01", closes_on="2026-12-20")
    return sys


def partner(partner_id: str, n: int = 1) -> Actor:
    return Actor(f"p-{partner_id}-{n}", "partner", partner_id=partner_id, name=f"{partner_id} 设计师")


def scarf_design(**over) -> DesignContent:
    d = DesignContent(
        artwork_refs=(ElementRef(
            artwork_id="artwork-river-17",
            element_ids=("main-figure",),
            mode=RefMode.COMMERCIAL_ADAPTATION,
            note="主山体轮廓用于丝巾图案二创",
        ),),
        material="silk",
        channels=("museum-shop",),
        regions=("CN",),
        quantity=500,
        purpose=Purpose.COMMERCIAL,
    )
    if over:
        from dataclasses import replace
        d = replace(d, **over)
    return d


def wave_design() -> DesignContent:
    return scarf_design(artwork_refs=(ElementRef(
        artwork_id="artwork-river-17",
        element_ids=("wave-pattern",),
        mode=RefMode.COMMERCIAL_ADAPTATION,
    ),))


def standard_license(valid_until: str = "2027-06-30") -> LicenseScope:
    return LicenseScope(
        max_quantity=2000,
        regions=frozenset({"CN"}),
        channels=frozenset({"museum-shop", "online-mall"}),
        valid_from="2026-08-01",
        valid_until=valid_until,
        purpose=Purpose.COMMERCIAL,
    )


def approve_all_gates(sys: System, proposal_id: str, version_id: str,
                      license_scope: LicenseScope | None = None) -> None:
    """按真实顺序完成四门：权利门先落许可。"""
    if license_scope is None:
        license_scope = standard_license()
    sys.grant_license(
        STAFF["rights"], proposal_id=proposal_id, version_id=version_id,
        holder_id="artist-chen", scope=license_scope,
        contract_ref=f"CT-{proposal_id}-001",
        contract_body={"title": "著作权商业改编许可合同", "royalty": "8%", "signed": True},
    )
    sys.decide_gate(STAFF["curator"], proposal_id=proposal_id,
                    version_id=version_id, gate=Gate.ACADEMIC,
                    approved=True, reason="元素引用忠实")
    sys.decide_gate(STAFF["rights"], proposal_id=proposal_id,
                    version_id=version_id, gate=Gate.RIGHTS,
                    approved=True, reason="复制+商业改编已获艺术家书面许可")
    sys.decide_gate(STAFF["benefit"], proposal_id=proposal_id,
                    version_id=version_id, gate=Gate.BENEFIT,
                    approved=True, reason="商业产品，不适用公益例外")
    sys.decide_gate(STAFF["business"], proposal_id=proposal_id,
                    version_id=version_id, gate=Gate.BUSINESS,
                    approved=True, reason="定价与渠道合规")
