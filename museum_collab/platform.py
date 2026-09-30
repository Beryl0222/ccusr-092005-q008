"""平台门面：把档案、提案、审批、许可、打样、生产、事件串成一条线。

所有写操作都：
1. 做角色/归属校验（信息隔离）；
2. 执行业务规则（不满足即抛领域错误，明确阻断）；
3. 向只追加哈希台账落账。
"""
from __future__ import annotations

from itertools import count
from typing import Any

from . import dossier as dossier_mod
from . import permissions as perms
from .actors import Actor, Gate, Role
from .errors import (
    ApprovalRequiredError,
    AuthorizationError,
    ConflictError,
    LicenseError,
    StateError,
    ValidationError,
)
from .hashing import canonical_hash, text_hash
from .ledger import Clock, Ledger
from .licensing import Channel, License, LicenseScope, Right, covers_use
from .production import (
    InstanceState,
    ProductInstance,
    ProductionOrder,
    RightSnapshot,
    required_rights_for,
)
from .proposals import (
    Proposal,
    ProposalContent,
    ProposalVersion,
    diff_versions,
    impacted_gates,
)
from .records import (
    Artwork,
    Contract,
    Element,
    Exhibition,
    Message,
    Partner,
    RightsHolder,
)
from .sampling import COMMERCIAL, EXCLUSIVE, SAMPLE, ElementLockTable
from .events import Disposition, Trigger, plan_for_instance


class MuseumPlatform:
    def __init__(self, clock: Clock | None = None) -> None:
        self.ledger = Ledger(clock)
        self.artworks: dict[str, Artwork] = {}
        self.holders: dict[str, RightsHolder] = {}
        self.partners: dict[str, Partner] = {}
        self.exhibitions: dict[str, Exhibition] = {}
        self.contracts: dict[str, Contract] = {}
        self.proposals: dict[str, Proposal] = {}
        self.licenses: dict[str, License] = {}
        self.orders: dict[str, ProductionOrder] = {}
        self.instances: dict[str, ProductInstance] = {}
        self.messages: list[Message] = []
        self.dispositions: list[Disposition] = []
        self.locks = ElementLockTable()
        self._committed: dict[str, int] = {}
        self._seq = count(1)

    # ------------------------------------------------------------------ #
    # 档案入库
    # ------------------------------------------------------------------ #
    def register_rights_holder(self, actor: Actor, holder: RightsHolder) -> None:
        self._require_role(actor, Role.ADMIN)
        self.holders[holder.holder_id] = holder
        self.ledger.append("partner_registered", actor.actor_id, {
            "kind": "rights_holder", "holder_id": holder.holder_id})

    def register_artwork(self, actor: Actor, artwork: Artwork,
                         elements: list[Element]) -> None:
        self._require_role(actor, Role.ADMIN)
        for el in elements:
            artwork.add_element(el)
        self.artworks[artwork.artwork_id] = artwork
        self.ledger.append("artwork_registered", actor.actor_id, {
            "artwork_id": artwork.artwork_id,
            "title": artwork.title,
            "rights_holder_ids": artwork.rights_holder_ids,
            "acquired": artwork.acquired,
            "commercial_clearance": artwork.commercial_clearance,
            "elements": [el.ref() for el in artwork.elements.values()],
        })

    def register_partner(self, actor: Actor, partner: Partner) -> None:
        self._require_role(actor, Role.ADMIN)
        self.partners[partner.partner_id] = partner
        self.ledger.append("partner_registered", actor.actor_id, {
            "kind": "partner", "partner_id": partner.partner_id,
            "stages": list(partner.stages)})

    def register_exhibition(self, actor: Actor, exhibition: Exhibition) -> None:
        self._require_role(actor, Role.ADMIN)
        self.exhibitions[exhibition.exhibition_id] = exhibition
        self.ledger.append("exhibition_registered", actor.actor_id, {
            "exhibition_id": exhibition.exhibition_id,
            "opens_on": exhibition.opens_on,
            "closes_on": exhibition.closes_on})

    def record_contract(self, actor: Actor, partner_id: str, title: str,
                        body: str, body_ref: str) -> Contract:
        """合同正文不进系统：只登记正文哈希与受控存储引用。"""
        self._require_role(actor, Role.ADMIN)
        if partner_id not in self.partners:
            raise ValidationError("合作方尚未入库")
        contract = Contract(
            contract_id=f"contract-{next(self._seq)}",
            partner_id=partner_id,
            title=title,
            body_sha256=text_hash(body),
            body_ref=body_ref,
        )
        self.contracts[contract.contract_id] = contract
        self.ledger.append("contract_recorded", actor.actor_id, {
            "contract_id": contract.contract_id,
            "partner_id": partner_id,
            "body_sha256": contract.body_sha256,
            "body_ref": body_ref})
        return contract

    # ------------------------------------------------------------------ #
    # 沟通（取代群聊追授权）
    # ------------------------------------------------------------------ #
    def post_message(self, actor: Actor, proposal_id: str, content: str,
                     audience: tuple[str, ...]) -> Message:
        proposal = self._get_proposal(proposal_id)
        # 合作方只能在自己的提案里发言
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, proposal.versions[1].content.partner_id)
        entry = self.ledger.append("message_posted", actor.actor_id, {
            "proposal_id": proposal_id,
            "content_sha256": canonical_hash(content),
            "audience": list(audience)})
        msg = Message(
            message_id=f"msg-{next(self._seq)}",
            proposal_id=proposal_id,
            author_actor_id=actor.actor_id,
            audience=audience,
            content=content,
            timestamp=entry.timestamp,
            entry_seq=entry.seq,
        )
        self.messages.append(msg)
        return msg

    def messages_for(self, proposal_id: str) -> list[Message]:
        return [m for m in self.messages if m.proposal_id == proposal_id]

    # ------------------------------------------------------------------ #
    # 提案与版本
    # ------------------------------------------------------------------ #
    def create_proposal(self, actor: Actor, *, partner_id: str,
                        exhibition_id: str, title: str,
                        element_uses: list[dict[str, Any]], material: str,
                        regions: list[str], channels: list[str],
                        quantity: int, unit_price: int,
                        public_benefit: bool, benefit_statement: str,
                        design_file_ref: str, design_file_sha256: str,
                        notes: str = "") -> str:
        if actor.role not in (Role.DESIGNER, Role.PARTNER, Role.CURATOR):
            raise AuthorizationError("仅创意/设计角色可以发起提案")
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, partner_id)
        if partner_id not in self.partners:
            raise ValidationError("合作方尚未入库")
        if exhibition_id not in self.exhibitions:
            raise ValidationError("展览尚未入库")
        self._validate_element_uses(element_uses)
        for ch in channels:
            if ch not in Channel.ALL:
                raise ValidationError(f"未知渠道 {ch}")

        proposal_id = f"proposal-{next(self._seq)}"
        content = ProposalContent(
            proposal_id=proposal_id, version=1, partner_id=partner_id,
            exhibition_id=exhibition_id, title=title,
            element_uses=tuple(element_uses), material=material,
            regions=tuple(regions), channels=tuple(channels),
            quantity=quantity, unit_price=unit_price,
            public_benefit=public_benefit,
            benefit_statement=benefit_statement,
            design_file_ref=design_file_ref,
            design_file_sha256=design_file_sha256,
            based_on_version=None, notes=notes,
        )
        version = ProposalVersion(
            content=content,
            content_sha256=canonical_hash(content.to_hash_payload()),
            changes_from_prev=(),
        )
        proposal = Proposal(proposal_id=proposal_id, versions={1: version},
                            current_version=1)
        entry = self.ledger.append("proposal_created", actor.actor_id, {
            "proposal_id": proposal_id,
            "version": 1,
            "content_sha256": version.content_sha256,
            "element_uses": element_uses})
        proposal.created_entry_seq = entry.seq
        self.proposals[proposal_id] = proposal
        return proposal_id

    def revise_proposal(self, actor: Actor, proposal_id: str,
                        changes: dict[str, Any]) -> int:
        """按字段补丁生成新版本，并只重开受影响的审批门。"""
        proposal = self._get_proposal(proposal_id)
        old = proposal.latest().content
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, old.partner_id)
        elif actor.role not in (Role.DESIGNER, Role.CURATOR):
            raise AuthorizationError("无权修订提案")

        allowed = {
            "title", "element_uses", "material", "regions", "channels",
            "quantity", "unit_price", "public_benefit", "benefit_statement",
            "design_file_ref", "design_file_sha256", "notes",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"不可修订字段: {sorted(unknown)}")

        new_no = old.version + 1
        data = {
            "title": old.title, "element_uses": list(old.element_uses),
            "material": old.material, "regions": list(old.regions),
            "channels": list(old.channels), "quantity": old.quantity,
            "unit_price": old.unit_price,
            "public_benefit": old.public_benefit,
            "benefit_statement": old.benefit_statement,
            "design_file_ref": old.design_file_ref,
            "design_file_sha256": old.design_file_sha256,
            "notes": old.notes,
        }
        data.update(changes)
        if "element_uses" in changes:
            self._validate_element_uses(data["element_uses"])

        new_content = ProposalContent(
            proposal_id=proposal_id, version=new_no,
            partner_id=old.partner_id, exhibition_id=old.exhibition_id,
            based_on_version=old.version,
            element_uses=tuple(data["element_uses"]),
            title=data["title"], material=data["material"],
            regions=tuple(data["regions"]), channels=tuple(data["channels"]),
            quantity=data["quantity"], unit_price=data["unit_price"],
            public_benefit=data["public_benefit"],
            benefit_statement=data["benefit_statement"],
            design_file_ref=data["design_file_ref"],
            design_file_sha256=data["design_file_sha256"],
            notes=data["notes"],
        )
        change_types = diff_versions(old, new_content)
        gates_to_reopen = impacted_gates(change_types)

        # 旧版本立即标记被取代：未生产的新工单再也不能引用它
        proposal.latest().invalidated_at_revision = new_no

        new_version = ProposalVersion(
            content=new_content,
            content_sha256=canonical_hash(new_content.to_hash_payload()),
            changes_from_prev=tuple(change_types),
        )

        # 未受影响且旧版已通过的门：显式沿用，不要求重审
        old_version = proposal.versions[old.version]
        carried: list[str] = []
        for gate, rec in old_version.approvals.items():
            if gate not in gates_to_reopen:
                from .proposals import ApprovalRecord
                new_version.approvals[gate] = ApprovalRecord(
                    gate=gate, version=new_no,
                    content_sha256=new_version.content_sha256,
                    decider_actor_id=rec.decider_actor_id,
                    note=f"沿用 v{old.version} 的批准（本次变更不影响该门）",
                    entry_seq=0,
                    boundary_sha256=rec.boundary_sha256,
                    carried_from=old.version,
                )
                carried.append(gate)

        proposal.versions[new_no] = new_version
        proposal.current_version = new_no
        self.ledger.append("proposal_revised", actor.actor_id, {
            "proposal_id": proposal_id,
            "version": new_no,
            "based_on_version": old.version,
            "content_sha256": new_version.content_sha256,
            "changes": change_types,
            "reopened_gates": sorted(gates_to_reopen),
            "carried_gates": carried})
        return new_no

    def approve(self, actor: Actor, proposal_id: str, gate: str,
                note: str = "") -> None:
        proposal = self._get_proposal(proposal_id)
        owner_role = Role.GATE_OWNERS.get(gate)
        if owner_role is None:
            raise ValidationError(f"未知审批门 {gate}")
        if actor.role != owner_role:
            raise AuthorizationError(
                f"{gate} 审批门只能由 {owner_role} 完成")
        version = proposal.latest()
        if gate in version.approvals and version.approvals[gate].carried_from is None:
            raise ValidationError("该门已批准，重复批准无效")

        boundary_sha = ""
        if gate == Gate.RIGHTS:
            # 权利确认：系统先校验许可是否覆盖提案的完整商业使用
            self._assert_license_covers_proposal(version.content)
            boundary_sha = canonical_hash(
                self.active_boundary_for(proposal_id))

        entry = self.ledger.append("approval_granted", actor.actor_id, {
            "proposal_id": proposal_id,
            "version": version.content.version,
            "gate": gate,
            "content_sha256": version.content_sha256,
            "boundary_sha256": boundary_sha,
            "note": note})
        from .proposals import ApprovalRecord
        version.approvals[gate] = ApprovalRecord(
            gate=gate, version=version.content.version,
            content_sha256=version.content_sha256,
            decider_actor_id=actor.actor_id, note=note,
            entry_seq=entry.seq, boundary_sha256=boundary_sha)

    def approve_all(self, actors: dict[str, Actor], proposal_id: str) -> None:
        """测试/造数辅助：四个角色依次审批。"""
        for gate in Gate.ALL:
            self.approve(actors[gate], proposal_id, gate)

    # ------------------------------------------------------------------ #
    # 打样（并行放行 / 独占阻断）
    # ------------------------------------------------------------------ #
    def start_sampling(self, actor: Actor, proposal_id: str) -> None:
        proposal = self._get_proposal(proposal_id)
        partner_id = proposal.latest().content.partner_id
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, partner_id)
        elif actor.role not in (Role.DESIGNER, Role.CURATOR, Role.OPERATIONS):
            raise AuthorizationError("无权发起打样")

        version = proposal.latest()
        acquired: list[str] = []
        try:
            for use in version.content.element_uses:
                handle = self.locks.try_acquire(
                    partner_id=partner_id, proposal_id=proposal_id,
                    artwork_id=use["artwork_id"],
                    element_id=use["element_id"], mode=SAMPLE)
                acquired.append(handle.element_key)
        except ConflictError:
            # 整体阻断：回滚本次已取得的锁
            self.locks.release_proposal(proposal_id)
            raise
        self.ledger.append("sample_started", actor.actor_id, {
            "proposal_id": proposal_id,
            "version": version.content.version,
            "elements": acquired})

    def finish_sampling(self, actor: Actor, proposal_id: str, *,
                        won: bool) -> None:
        """并行打样选型结束：落选方释放元素锁，独占决定此后才能作出。"""
        proposal = self._get_proposal(proposal_id)
        partner_id = proposal.latest().content.partner_id
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, partner_id)
        elif actor.role not in (Role.CURATOR, Role.DESIGNER, Role.OPERATIONS,
                                Role.BUSINESS):
            raise AuthorizationError("无权结束打样")
        released = self.locks.release_proposal(proposal_id)
        self.ledger.append("element_lock_released", actor.actor_id, {
            "proposal_id": proposal_id,
            "won": won,
            "released_elements": [h.element_key for h in released]})

    # ------------------------------------------------------------------ #
    # 许可授予（数量/地区/渠道/期限）
    # ------------------------------------------------------------------ #
    def grant_license(self, actor: Actor, *, holder_id: str, partner_id: str,
                      artwork_id: str, element_ids: list[str] | str,
                      rights: list[str], quantity: int | None,
                      regions: list[str], channels: list[str],
                      starts_on: str, ends_on: str,
                      exclusive: bool = False) -> str:
        if actor.role not in (Role.RIGHTS_OFFICER, Role.RIGHTS_HOLDER):
            raise AuthorizationError("只有权利人/权利专员可以授予许可")
        if holder_id not in self.holders:
            raise ValidationError("权利人尚未入库")
        if partner_id not in self.partners:
            raise ValidationError("合作方尚未入库")
        artwork = self.artworks.get(artwork_id)
        if artwork is None:
            raise ValidationError("作品尚未入库")
        if element_ids != "*":
            for eid in element_ids:
                if eid not in artwork.elements:
                    raise ValidationError(f"元素 {eid} 未在作品档案中登记")

        license_id = f"license-{next(self._seq)}"
        lic = License(
            license_id=license_id, artwork_id=artwork_id,
            element_ids=frozenset(element_ids if element_ids == "*" else element_ids),
            holder_id=holder_id, partner_id=partner_id,
            rights=frozenset(rights),
            scope=LicenseScope(
                quantity=quantity, regions=frozenset(regions),
                channels=frozenset(channels),
                starts_on=starts_on, ends_on=ends_on),
            exclusive=exclusive)

        # 先抢元素锁：独占与任何竞争方冲突，非独占与打样/非独占兼容。
        # 抢锁失败直接抛冲突，许可不落账，避免"已授予但未锁定"。
        targets = ["*"] if element_ids == "*" else list(element_ids)
        if "*" in targets:
            targets = list(artwork.elements)
        mode = EXCLUSIVE if exclusive else COMMERCIAL
        locked: list[str] = []
        for eid in targets:
            handle = self.locks.try_acquire(
                partner_id=partner_id, proposal_id=f"license:{license_id}",
                artwork_id=artwork_id, element_id=eid, mode=mode)
            locked.append(handle.element_key)

        self.licenses[license_id] = lic
        entry = self.ledger.append("license_granted", actor.actor_id, {
            "license_id": license_id,
            "holder_id": holder_id, "partner_id": partner_id,
            "artwork_id": artwork_id, "element_ids": targets,
            "rights": rights, "scope": lic.scope.to_data(),
            "exclusive": exclusive, "locked_elements": locked})
        lic.granted_entry_seq = entry.seq
        return license_id

    def active_licenses_for(self, proposal_id: str) -> list[License]:
        content = self._get_proposal(proposal_id).latest().content
        result: list[License] = []
        for lic in self.licenses.values():
            if lic.status != "active" or lic.partner_id != content.partner_id:
                continue
            if any(lic.covers_element(u["artwork_id"], u["element_id"])
                   for u in content.element_uses):
                result.append(lic)
        return result

    def active_boundary_for(self, proposal_id: str) -> list[dict[str, Any]]:
        return [lic.boundary_view()
                for lic in self.active_licenses_for(proposal_id)]

    def current_boundary_for(self, proposal_id: str) -> list[dict[str, Any]]:
        """当前权利边界，含已撤回许可（status 标明），供回溯。"""
        content = self._get_proposal(proposal_id).latest().content
        views = []
        for lic in self.licenses.values():
            if lic.partner_id != content.partner_id:
                continue
            if any(lic.covers_element_ever(u["artwork_id"], u["element_id"])
                   for u in content.element_uses):
                views.append(lic.boundary_view())
        return views

    # ------------------------------------------------------------------ #
    # 生产工单（钉住批准版本哈希 + 权利快照 + 合同哈希）
    # ------------------------------------------------------------------ #
    def create_production_order(self, actor: Actor, *, proposal_id: str,
                                region: str, channel: str, quantity: int,
                                on_date: str, contract_id: str) -> str:
        proposal = self._get_proposal(proposal_id)
        partner_id = proposal.latest().content.partner_id
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, partner_id)
        elif actor.role not in (Role.OPERATIONS, Role.BUSINESS):
            raise AuthorizationError("无权创建生产工单")

        version = proposal.assert_production_ready()  # 四门全通否则阻断
        content = version.content
        if quantity > content.quantity:
            raise LicenseError(
                f"工单数量 {quantity} 超过提案批准计划量 {content.quantity}")
        if channel not in content.channels:
            raise LicenseError(
                f"渠道 {channel} 不在提案批准范围 {list(content.channels)}")
        if region not in content.regions and "*" not in content.regions:
            raise LicenseError(f"地区 {region} 不在提案批准范围")
        contract = self.contracts.get(contract_id)
        if contract is None or contract.partner_id != partner_id:
            raise ValidationError("合同与合作方不匹配或未登记")

        # 逐元素校验许可覆盖并归集被钉住的许可
        required = required_rights_for(content.channels)
        pinned: dict[str, License] = {}
        for use in content.element_uses:
            ok, ref = covers_use(
                list(self.licenses.values()),
                artwork_id=use["artwork_id"], element_id=use["element_id"],
                partner_id=partner_id, required_rights=required,
                quantity=quantity, region=region, channel=channel,
                on_date=on_date, committed_by_license=self._committed)
            if not ok:
                raise LicenseError(ref)
            pinned[ref] = self.licenses[ref]

        for lic_id in pinned:
            self._committed[lic_id] = self._committed.get(lic_id, 0) + quantity

        snapshot = RightSnapshot.of(
            [lic.boundary_view() for lic in pinned.values()])
        order_id = f"order-{next(self._seq)}"
        entry = self.ledger.append("production_order_created", actor.actor_id, {
            "order_id": order_id, "proposal_id": proposal_id,
            "version": content.version,
            "version_sha256": version.content_sha256,
            "contract_sha256": contract.body_sha256,
            "rights_snapshot_sha256": snapshot.snapshot_sha256,
            "license_ids": sorted(pinned),
            "region": region, "channel": channel, "quantity": quantity})
        order = ProductionOrder(
            order_id=order_id, proposal_id=proposal_id,
            version=content.version, version_sha256=version.content_sha256,
            partner_id=partner_id, exhibition_id=content.exhibition_id,
            region=region, channel=channel, quantity=quantity,
            contract_sha256=contract.body_sha256,
            rights_snapshot=snapshot,
            license_ids=tuple(sorted(pinned)),
            created_on=on_date, entry_seq=entry.seq)
        self.orders[order_id] = order

        for i in range(quantity):
            inst = ProductInstance(
                instance_id=f"{order_id}-item-{i + 1:04d}",
                order_id=order_id, proposal_id=proposal_id,
                version=content.version, partner_id=partner_id)
            self.instances[inst.instance_id] = inst
        return order_id

    def transition_instance(self, actor: Actor, instance_id: str,
                            new_state: str, *, on_date: str,
                            note: str = "") -> None:
        inst = self._get_instance(instance_id)
        if actor.role == Role.PARTNER:
            perms.assert_partner_owns(actor, inst.partner_id)
        elif actor.role not in (Role.OPERATIONS, Role.BUSINESS):
            raise AuthorizationError("无权变更实例状态")
        order = self.orders[inst.order_id]
        if order.frozen:
            raise StateError("工单已被撤回/改期事件冻结，暂停一切状态迁移；"
                             "续权或新档期确认后须先解冻")
        inst.transition(new_state, on_date=on_date, note=note)
        self.ledger.append("instance_status_changed", actor.actor_id, {
            "instance_id": instance_id,
            "from": inst.history[-1]["from"],
            "to": new_state, "on_date": on_date})

    # ------------------------------------------------------------------ #
    # 事件：撤回 / 改期 —— 逐实例分类处置，不覆盖旧决定
    # ------------------------------------------------------------------ #
    def revoke_license(self, actor: Actor, license_id: str, *,
                       on_date: str, reason: str) -> list[str]:
        if actor.role not in (Role.RIGHTS_OFFICER, Role.RIGHTS_HOLDER):
            raise AuthorizationError("只有权利人/权利专员可以撤回许可")
        lic = self.licenses.get(license_id)
        if lic is None or lic.status != "active":
            raise ValidationError("许可不存在或已撤回")
        lic.status = "revoked"
        lic.revoked_reason = reason
        entry = self.ledger.append("license_revoked", actor.actor_id, {
            "license_id": license_id, "on_date": on_date, "reason": reason,
            "artwork_id": lic.artwork_id, "element_ids": sorted(lic.element_ids),
            "partner_id": lic.partner_id})
        lic.revoked_entry_seq = entry.seq

        # 释放该许可占用的元素锁，竞争方此后可获放行
        if "*" in lic.element_ids:
            target_eids = list(self.artworks[lic.artwork_id].elements)
        else:
            target_eids = list(lic.element_ids)
        if target_eids:
            self.locks.release(
                lic.partner_id,
                [f"{lic.artwork_id}/{eid}" for eid in target_eids],
                (COMMERCIAL, EXCLUSIVE),
                proposal_hint=f"license:{license_id}")

        disposition_ids: list[str] = []
        for order in self._orders_using_license(license_id):
            order.frozen = True
            for inst in self._instances_of(order.order_id):
                disposition_ids.append(self._issue_disposition(
                    trigger=Trigger.LICENSE_REVOKED, inst=inst,
                    order=order, on_date=on_date,
                    context={"license_id": license_id, "reason": reason}))
        return disposition_ids

    def reschedule_exhibition(self, actor: Actor, exhibition_id: str, *,
                              new_opens_on: str, new_closes_on: str | None,
                              on_date: str) -> list[str]:
        if actor.role not in (Role.CURATOR, Role.ADMIN):
            raise AuthorizationError("只有策展人/管理员可以调整展期")
        old = self.exhibitions[exhibition_id]
        updated = Exhibition(exhibition_id, old.name, new_opens_on,
                             new_closes_on or old.closes_on)
        self.exhibitions[exhibition_id] = updated
        self.ledger.append("schedule_changed", actor.actor_id, {
            "exhibition_id": exhibition_id,
            "old_opens_on": old.opens_on, "new_opens_on": new_opens_on,
            "old_closes_on": old.closes_on,
            "new_closes_on": updated.closes_on})

        disposition_ids: list[str] = []
        for order in self._orders_of_exhibition(exhibition_id):
            covers_new_window = self._licenses_cover_date(order, new_opens_on)
            for inst in self._instances_of(order.order_id):
                action, rationale = plan_for_instance(
                    Trigger.SCHEDULE_CHANGED, inst.state,
                    license_covers_new_window=covers_new_window)
                # 未生产/在途在新档期明确前冻结后续动作；续权升级同样冻结
                if inst.state in (InstanceState.UNPRODUCED,
                                  InstanceState.IN_TRANSIT):
                    order.frozen = True
                disposition_ids.append(self._issue_disposition(
                    trigger=Trigger.SCHEDULE_CHANGED, inst=inst,
                    order=order, on_date=on_date,
                    context={"new_opens_on": new_opens_on,
                             "license_covers_new_window": covers_new_window},
                    action_override=(action, rationale)))
        return disposition_ids

    def _issue_disposition(self, *, trigger: str, inst: ProductInstance,
                           order: ProductionOrder, on_date: str,
                           context: dict[str, Any],
                           action_override: tuple[str, str] | None = None) -> str:
        if action_override is None:
            action, rationale = plan_for_instance(trigger, inst.state)
        else:
            action, rationale = action_override
        disposition_id = f"disposition-{next(self._seq)}"
        entry = self.ledger.append("disposition_issued", "system", {
            "disposition_id": disposition_id,
            "trigger": trigger, "instance_id": inst.instance_id,
            "order_id": order.order_id,
            "state_at_event": inst.state, "action": action,
            "rationale": rationale, "context": context})
        d = Disposition(
            disposition_id=disposition_id, trigger=trigger,
            instance_id=inst.instance_id, order_id=order.order_id,
            state_at_event=inst.state, action=action, rationale=rationale,
            issued_on=on_date, entry_seq=entry.seq, context=context)
        self.dispositions.append(d)
        inst.disposition_ids.append(disposition_id)
        return disposition_id

    def dispositions_for_instance(self, instance_id: str) -> list[Disposition]:
        ids = set(self.instances[instance_id].disposition_ids)
        return [d for d in self.dispositions if d.disposition_id in ids]

    def resume_order(self, actor: Actor, order_id: str, *, on_date: str,
                    reason: str) -> None:
        """续权完成或新档期确认后解冻工单。

        无论以何种理由恢复，都必须重新验证许可覆盖该日期/渠道/地区，
        避免"先解冻再说"让错误版本或过期许可流入生产。
        """
        if actor.role not in (Role.BUSINESS, Role.RIGHTS_OFFICER):
            raise AuthorizationError("仅经营/权利角色可以恢复工单")
        order = self.orders[order_id]
        if not order.frozen:
            raise ValidationError("工单未被冻结，无需恢复")
        content = self.proposals[order.proposal_id].versions[order.version].content
        required = required_rights_for((order.channel,))
        for use in content.element_uses:
            # 本工单创建时已占用数量额度，恢复校验时要扣除自身，
            # 检查的是"其他承诺 + 本单 ≤ 上限"
            committed_excluding_self = {
                lic_id: max(0, qty - order.quantity)
                for lic_id, qty in self._committed.items()
            }
            ok, why = covers_use(
                list(self.licenses.values()),
                artwork_id=use["artwork_id"], element_id=use["element_id"],
                partner_id=order.partner_id, required_rights=required,
                quantity=order.quantity, region=order.region,
                channel=order.channel, on_date=on_date,
                committed_by_license=committed_excluding_self)
            if not ok:
                raise LicenseError(f"恢复被阻断：{why}")
        order.frozen = False
        self.ledger.append("order_resumed", actor.actor_id, {
            "order_id": order_id, "on_date": on_date, "reason": reason})

    # ------------------------------------------------------------------ #
    # 信息隔离视图
    # ------------------------------------------------------------------ #
    def partner_view(self, actor: Actor, proposal_id: str) -> dict[str, Any]:
        proposal = self._get_proposal(proposal_id)
        version = proposal.latest()
        partner_id = version.content.partner_id
        perms.assert_partner_owns(actor, partner_id)
        partner = self.partners[partner_id]
        order = next((o for o in self.orders.values()
                      if o.proposal_id == proposal_id
                      and o.version == version.content.version), None)
        instances = [i for i in self.instances.values()
                     if order and i.order_id == order.order_id]
        visible = []
        for m in self.messages_for(proposal_id):
            if perms.message_visible(m.audience, actor):
                visible.append({
                    "message_id": m.message_id,
                    "author": m.author_actor_id,
                    "content": m.content,
                    "timestamp": m.timestamp})
        return perms.partner_briefing(
            actor=actor, partner=partner, version=version, order=order,
            instances=instances, visible_messages=visible)

    def contract_meta(self, actor: Actor, contract_id: str) -> dict[str, Any]:
        self._require_role(actor, (Role.ADMIN, Role.RIGHTS_OFFICER,
                                   Role.BUSINESS, Role.OPERATIONS,
                                   Role.CURATOR))
        return perms.contract_meta_view(self.contracts[contract_id])

    def contract_body(self, actor: Actor, contract_id: str,
                      sealed_storage: dict[str, str]) -> str:
        """取正文必须通过受控存储，且只有授权角色；普通运营被拒。"""
        perms.assert_contract_body_allowed(actor.role)
        contract = self.contracts[contract_id]
        body = sealed_storage.get(contract.body_ref)
        if body is None or text_hash(body) != contract.body_sha256:
            raise ValidationError("受控存储中的合同正文与登记哈希不符")
        return body

    # ------------------------------------------------------------------ #
    # 策展人回溯
    # ------------------------------------------------------------------ #
    def curator_dossier(self, actor: Actor, instance_id: str) -> dict[str, Any]:
        self._require_role(actor, (Role.CURATOR, Role.ADMIN,
                                   Role.RIGHTS_OFFICER))
        return dossier_mod.build_dossier(self, instance_id=instance_id)

    def artwork_view(self, artwork_id: str) -> dict[str, Any]:
        artwork = self.artworks[artwork_id]
        return {
            "artwork_id": artwork.artwork_id,
            "title": artwork.title,
            "acquired": artwork.acquired,
            "commercial_clearance": artwork.commercial_clearance,
            "rights_holder_ids": artwork.rights_holder_ids,
            "elements": [el.ref() for el in artwork.elements.values()],
        }

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #
    def _validate_element_uses(self, uses: list[dict[str, Any]]) -> None:
        if not uses:
            raise ValidationError("提案必须标明至少一处元素取用")
        for use in uses:
            artwork = self.artworks.get(use["artwork_id"])
            if artwork is None:
                raise ValidationError(
                    f"作品 {use['artwork_id']} 尚未入库，无法取用元素")
            if use["element_id"] not in artwork.elements:
                raise ValidationError(
                    f"元素 {use['element_id']} 未登记于作品 "
                    f"{use['artwork_id']}，取用无法回溯")
            use.setdefault("adaptation", "direct_reproduction")

    def _assert_license_covers_proposal(self, content: ProposalContent) -> None:
        required = required_rights_for(content.channels)
        # 提案每个渠道/地区组合都要被覆盖；数量按提案计划量
        for channel in content.channels:
            for region in content.regions:
                for use in content.element_uses:
                    ok, reason = covers_use(
                        list(self.licenses.values()),
                        artwork_id=use["artwork_id"],
                        element_id=use["element_id"],
                        partner_id=content.partner_id,
                        required_rights=required,
                        quantity=content.quantity, region=region,
                        channel=channel,
                        on_date=self.exhibitions[content.exhibition_id].opens_on,
                        committed_by_license=self._committed)
                    if not ok:
                        raise LicenseError(f"权利确认被阻断：{reason}")

    def _licenses_cover_date(self, order: ProductionOrder, date: str) -> bool:
        for lic_id in order.license_ids:
            lic = self.licenses[lic_id]
            if lic.status == "active" and lic.scope.starts_on <= date <= lic.scope.ends_on:
                return True
        return False

    def _orders_using_license(self, license_id: str) -> list[ProductionOrder]:
        return [o for o in self.orders.values() if license_id in o.license_ids]

    def _orders_of_exhibition(self, exhibition_id: str) -> list[ProductionOrder]:
        return [o for o in self.orders.values()
                if o.exhibition_id == exhibition_id]

    def _instances_of(self, order_id: str) -> list[ProductInstance]:
        return [i for i in self.instances.values() if i.order_id == order_id]

    def _get_proposal(self, proposal_id: str) -> Proposal:
        if proposal_id not in self.proposals:
            raise ValidationError(f"提案 {proposal_id} 不存在")
        return self.proposals[proposal_id]

    def _get_instance(self, instance_id: str) -> ProductInstance:
        if instance_id not in self.instances:
            raise ValidationError(f"实例 {instance_id} 不存在")
        return self.instances[instance_id]

    @staticmethod
    def _require_role(actor: Actor, roles: str | tuple[str, ...]) -> None:
        roles = (roles,) if isinstance(roles, str) else roles
        if actor.role not in roles:
            raise AuthorizationError(
                f"{actor.role} 无权执行该操作，允许角色：{list(roles)}")
