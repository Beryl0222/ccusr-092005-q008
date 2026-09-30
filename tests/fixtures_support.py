"""测试公共造数：一个开幕在即的展览、一件仅授权展览宣传的作品、两家合作方。"""
from __future__ import annotations

from museum_collab.actors import Actor, Gate, Role
from museum_collab.licensing import Channel, Right
from museum_collab.platform import MuseumPlatform
from museum_collab.records import (
    Artwork, Element, Exhibition, Partner, RightsHolder,
)


def build_actors() -> dict[str, Actor]:
    return {
        "admin": Actor("u-admin", Role.ADMIN, name="档案管理员"),
        "curator": Actor("u-curator", Role.CURATOR, name="策展人"),
        "designer": Actor("u-designer", Role.DESIGNER, name="设计师"),
        Gate.ACADEMIC: Actor("u-academic", Role.ACADEMIC, name="学术委员"),
        Gate.RIGHTS: Actor("u-rights", Role.RIGHTS_OFFICER, name="权利专员"),
        Gate.PUBLIC_BENEFIT: Actor("u-benefit", Role.PUBLIC_BENEFIT_OFFICER,
                                   name="公益属性专员"),
        Gate.BUSINESS: Actor("u-business", Role.BUSINESS, name="经营负责人"),
        "holder": Actor("u-holder", Role.RIGHTS_HOLDER, name="艺术家本人"),
        "ops": Actor("u-ops", Role.OPERATIONS, name="普通运营"),
        "pA": Actor("u-pA", Role.PARTNER, partner_id="partner-A",
                    name="甲文创公司"),
        "pB": Actor("u-pB", Role.PARTNER, partner_id="partner-B",
                    name="乙制造公司"),
    }


def fresh_platform() -> MuseumPlatform:
    p = MuseumPlatform()
    a = build_actors()

    p.register_rights_holder(a["admin"], RightsHolder(
        "holder-artist", "艺术家林某"))
    p.register_rights_holder(a["admin"], RightsHolder(
        "holder-family", "家属代理"))

    river = Artwork(
        "artwork-river-17", "《河》系列第17号",
        rights_holder_ids=["holder-artist", "holder-family"],
        commercial_clearance="pending")
    p.register_artwork(a["admin"], river, [
        Element("el-whole", "artwork-river-17", "完整画面", "image"),
        Element("el-wave-motif", "artwork-river-17", "水波纹局部", "motif"),
        Element("el-palette", "artwork-river-17", "青灰色系", "color_palette"),
    ])

    bloom = Artwork(
        "artwork-bloom-03", "《绽》第3号",
        rights_holder_ids=["holder-artist"],
        commercial_clearance="pending")
    p.register_artwork(a["admin"], bloom, [
        Element("el-whole", "artwork-bloom-03", "完整画面", "image"),
    ])

    p.register_exhibition(a["admin"], Exhibition(
        "exhibition-modern-2026", "现代性特展",
        "2026-10-01", "2026-12-20"))

    p.register_partner(a["admin"], Partner(
        "partner-A", "甲文创公司",
        stages=("creative", "distribution")))
    p.register_partner(a["admin"], Partner(
        "partner-B", "乙制造公司",
        stages=("creative", "production")))

    return p


def approve_all(p: MuseumPlatform, proposal_id: str,
                actors: dict[str, Actor]) -> None:
    p.approve(actors[Gate.ACADEMIC], proposal_id, Gate.ACADEMIC)
    p.approve(actors[Gate.RIGHTS], proposal_id, Gate.RIGHTS)
    p.approve(actors[Gate.PUBLIC_BENEFIT], proposal_id, Gate.PUBLIC_BENEFIT)
    p.approve(actors[Gate.BUSINESS], proposal_id, Gate.BUSINESS)


def standard_license_kwargs(partner_id: str = "partner-A",
                            *, quantity: int | None = 5000,
                            channels: list[str] | None = None,
                            starts_on: str = "2026-08-01",
                            ends_on: str = "2027-06-30",
                            exclusive: bool = False,
                            rights: list[str] | None = None) -> dict:
    return dict(
        holder_id="holder-artist",
        partner_id=partner_id,
        artwork_id="artwork-river-17",
        element_ids=["el-wave-motif"],
        rights=rights or [Right.REPRODUCE, Right.ADAPT_COMMERCIAL],
        quantity=quantity,
        regions=["CN"],
        channels=channels or [Channel.MUSEUM_SHOP, Channel.ONLINE_STORE],
        starts_on=starts_on,
        ends_on=ends_on,
        exclusive=exclusive,
    )


def make_proposal(p: MuseumPlatform, actor: Actor, *,
                  partner_id: str = "partner-A",
                  channels: list[str] | None = None,
                  quantity: int = 1000,
                  title: str = "水波纹丝巾",
                  design_hash: str = "sha256:design-v1") -> str:
    return p.create_proposal(
        actor,
        partner_id=partner_id,
        exhibition_id="exhibition-modern-2026",
        title=title,
        element_uses=[{
            "artwork_id": "artwork-river-17",
            "element_id": "el-wave-motif",
            "adaptation": "cropped_pattern_on_fabric",
        }],
        material="桑蚕丝",
        regions=["CN"],
        channels=channels or [Channel.MUSEUM_SHOP, Channel.ONLINE_STORE],
        quantity=quantity,
        unit_price=268,
        public_benefit=True,
        benefit_statement="收益30%归入公共教育基金",
        design_file_ref="sealed://designs/scarf-v1",
        design_file_sha256=design_hash)
