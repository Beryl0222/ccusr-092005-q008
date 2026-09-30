"""协作后端主服务：创意 → 审批 → 生产 → 退市的全流程用例。

所有写操作都翻译成只追加事件；规则分三层：
1. 角色（access.py）：谁能做、谁能看；
2. 变更影响矩阵（model.FACET_IMPACT）：改什么、重启哪几道门；
3. 处置策略表（本模块 _WITHDRAWAL_POLICY / _RESCHEDULE_POLICY）：
   权利人撤回或开幕改期后，按实例状态分别处置，旧决定只追加、不覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .access import (
    CONTRACT_BODY_ROLES,
    AccessDenied,
    can_access_proposal,
    project_proposal,
)
from .codec import content_hash
from .model import (
    GATE_ROLE,
    ChangeFacet,
    DesignContent,
    DispositionAction,
    FacetImpact,
    Gate,
    InstanceState,
    LicenseScope,
    Purpose,
    ReservationMode,
    Trigger,
)
from .repository import (
    CATALOG_STREAM,
    Catalog,
    ElementLedger,
    Proposal,
    apply_proposal,
)
from .store import EventStore, utcnow


class DomainError(Exception):
    """业务规则不满足。"""


class ElementConflict(DomainError):
    """同一艺术元素被竞争占用：排他占用或已被他人抢先量产。"""


class ReleaseBlocked(DomainError):
    """放行生产条件不满足（审核未完、许可不覆盖、被撤回/改期冻结等）。"""


@dataclass(frozen=True)
class Actor:
    id: str
    role: str  # Role 的值，字符串以便序列化
    partner_id: str | None = None
    name: str = ""

    @property
    def role_enum(self):
        from .model import Role

        return Role(self.role)


# ---------------------------------------------------------------------------
# 处置策略表：触发事件 × 实例状态 → 具体动作
# ---------------------------------------------------------------------------
_WITHDRAWAL_POLICY: dict[str, tuple[DispositionAction, str, str | None]] = {
    # 未生产：立即取消，绝不让错误版本流入生产
    InstanceState.NOT_PRODUCED.value: (
        DispositionAction.CANCEL_BEFORE_PRODUCTION,
        "权利已撤回，尚未投产的批次立即取消",
        "cancelled",
    ),
    # 在途：召回/暂扣，停止继续铺货
    InstanceState.IN_TRANSIT.value: (
        DispositionAction.RECALL_OR_HOLD,
        "权利已撤回，在途货物召回或暂扣，渠道停止继续发货",
        "recalled",
    ),
    # 已售：不追溯已售个案，但禁止再售与补货
    InstanceState.SOLD.value: (
        DispositionAction.HONOR_SOLD_COPIES,
        "已售成品不追溯个人买家，但立即停止销售与补货",
        None,
    ),
    # 仅展览宣传：撤回后宣传同样停用
    InstanceState.PROMO_ONLY.value: (
        DispositionAction.PROMO_HALT,
        "权利已撤回，停止使用该元素的一切展览宣传物料",
        "promo_halted",
    ),
}

_PROMO_RESCHEDULE_UNTIL = "reschedule_revalidate"
_PROMO_RESCHEDULE_CONTINUE = "promo_continue"


class System:
    def __init__(self, store: EventStore | None = None, clock: Callable[[], str] = utcnow) -> None:
        self.store = store or EventStore(clock=clock)
        self._clock = clock
        self._contracts: dict[str, dict[str, Any]] = {}

    # ======================================================================
    # 0. 基础资料入库：作品、权利人、合作方、展览
    # ======================================================================
    def register_artwork(
        self,
        actor: Actor,
        *,
        artwork_id: str,
        title: str,
        holder_ids: list[str],
        elements: list[dict[str, Any]],
        baseline_allowed_uses: list[str] | None = None,
    ) -> None:
        self._require_staff(actor)
        payload = {
            "artwork_id": artwork_id,
            "title": title,
            "holder_ids": holder_ids,
            "elements": elements,
            "baseline_allowed_uses": baseline_allowed_uses or [],
        }
        self.store.append(CATALOG_STREAM, "artwork_registered", payload,
                          actor=actor.id, expected_version=self.store.stream_version(CATALOG_STREAM))

    def register_partner(self, actor: Actor, *, partner_id: str, name: str, channels: list[str]) -> None:
        self._require_staff(actor)
        payload = {"partner_id": partner_id, "name": name, "channels": channels}
        self.store.append(CATALOG_STREAM, "partner_registered", payload,
                          actor=actor.id, expected_version=self.store.stream_version(CATALOG_STREAM))

    def schedule_exhibition(self, actor: Actor, *, exhibition_id: str, opens_on: str, closes_on: str) -> None:
        self._require_staff(actor)
        payload = {"exhibition_id": exhibition_id, "opens_on": opens_on, "closes_on": closes_on}
        self.store.append(CATALOG_STREAM, "exhibition_scheduled", payload,
                          actor=actor.id, expected_version=self.store.stream_version(CATALOG_STREAM))

    def bootstrap_from_seed(self, actor: Actor, seed: dict[str, Any]) -> None:
        """从 fixtures/seed.json 批量导入基础资料（已存在的记录跳过）。"""
        catalog = self.catalog()
        for rec in seed.get("records", []):
            kind = rec["kind"]
            if kind == "exhibition" and rec["id"] not in catalog.exhibitions:
                self.schedule_exhibition(
                    actor, exhibition_id=rec["id"],
                    opens_on=rec["opens_on"], closes_on=rec["closes_on"],
                )
            elif kind == "artwork" and rec["id"] not in catalog.artworks:
                elements = []
                for e in rec.get("elements", [{"element_id": "whole-work", "name": rec.get("title", "整器形象")}]):
                    elements.append({
                        "element_id": e["element_id"],
                        "name": e["name"],
                        "rights": e.get("rights", {
                            "reproduction": "pending",
                            "commercial_adaptation": rec.get("commercial_clearance", "pending"),
                        }),
                    })
                self.register_artwork(
                    actor, artwork_id=rec["id"], title=rec.get("title", rec["id"]),
                    holder_ids=list(rec.get("rights_holders", [])),
                    elements=elements,
                    baseline_allowed_uses=list(rec.get("allowed_uses", [])),
                )
            elif kind == "partner" and rec["id"] not in catalog.partners:
                self.register_partner(
                    actor, partner_id=rec["id"], name=rec.get("name", rec["id"]),
                    channels=list(rec.get("channels", [])),
                )

    # ======================================================================
    # 1. 创意：创建提案、提交修订（只重启必要的审核）
    # ======================================================================
    def create_proposal(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        exhibition_id: str,
        partner_id: str,
        design: DesignContent,
    ) -> str:
        catalog = self.catalog()
        if exhibition_id not in catalog.exhibitions:
            raise DomainError("展览不存在，无法立项")
        if partner_id not in catalog.partners:
            raise DomainError("合作方未入库，无法立项")
        # 合作方只能为自己立项；馆方可以代为发起
        if actor.role_enum.value == "partner" and actor.partner_id != partner_id:
            raise AccessDenied("合作方只能以本机构名义发起提案")
        self._validate_refs(catalog, design)
        stream = self._stream(proposal_id)
        if self.store.read_stream(stream):
            raise DomainError("提案已存在")
        design_view = design.facet_view()
        payload = {
            "version_id": f"{proposal_id}-v1",
            "parent_version_id": None,
            "changed_facets": [],
            "exhibition_id": exhibition_id,
            "partner_id": partner_id,
            "design": design_view,
            "design_hash": content_hash(design_view),
            "carried": [],
        }
        self.store.append(stream, "proposal_created", payload,
                          actor=actor.id, expected_version=0)
        return payload["version_id"]

    def revise_proposal(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        facets: list[ChangeFacet],
        design: DesignContent,
        expected_version: int | None = None,
    ) -> str:
        proposal = self._proposal(proposal_id)
        self._require_participant(actor, proposal.partner_id)
        if proposal.stage == "delisted":
            raise DomainError("已退市提案不可修改")
        catalog = self.catalog()
        self._validate_refs(catalog, design)

        parent = proposal.current
        stream = self._stream(proposal_id)
        ev = expected_version if expected_version is not None else self.store.stream_version(stream)

        # 1) 影响矩阵：求并集，决定重启哪些门
        reopen: set[Gate] = set()
        invalidate_license = False
        for f in facets:
            impact: FacetImpact = self._facet_impact(f)
            reopen |= set(impact.reopen)
            invalidate_license = invalidate_license or impact.license_invalidated

        # 2) 沿用未受影响的旧批准（携带其哈希与来源版本）
        carried: list[dict[str, Any]] = []
        for gate, decision in parent.gates.items():
            if gate not in reopen and decision.status in ("approved", "carried"):
                carried.append({
                    "gate": gate.value,
                    "by": decision.by,
                    "at": decision.at,
                    "source_version_id": decision.source_version_id or parent.version_id,
                    "reason": "变更面不触及本门，沿用上一版本批准",
                })

        new_no = parent.version_no + 1
        version_id = f"{proposal_id}-v{new_no}"
        design_view = design.facet_view()
        # 未被变更面失效的生效许可沿用到新版本（哈希与合同引用保持不变）
        license_carried_from = None
        if (
            not invalidate_license
            and parent.license is not None
            and parent.license["status"] == "active"
        ):
            license_carried_from = parent.version_id
        payload = {
            "version_id": version_id,
            "parent_version_id": parent.version_id,
            "changed_facets": [f.value for f in facets],
            "reopen_gates": sorted(g.value for g in reopen),
            "design": design_view,
            "design_hash": content_hash(design_view),
            "carried": carried,
            "license_carried_from": license_carried_from,
        }
        self.store.append(stream, "design_submitted", payload,
                          actor=actor.id, expected_version=ev)

        # 3) 旧许可只在必要时失效：旧记录保留并标记 superseded，绝不删除
        if invalidate_license and parent.license is not None and parent.license["status"] == "active":
            self.store.append(
                stream, "license_superseded",
                {"version_id": parent.version_id,
                 "reason": "变更面触及权利边界，须重新确认许可"},
                actor=actor.id, expected_version=self.store.stream_version(stream),
            )
        return version_id

    # ======================================================================
    # 2. 审批：四道门分别由对应角色独立完成
    # ======================================================================
    def decide_gate(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        version_id: str,
        gate: Gate,
        approved: bool,
        reason: str = "",
    ) -> None:
        proposal = self._proposal(proposal_id)
        self._require_gate_role(actor, gate)
        v = proposal.version(version_id)
        if v.gates.get(gate) is not None and v.gates[gate].status in ("approved", "carried"):
            raise DomainError(f"{gate.value} 已批准，不可重复批准（如需变更请提交新版本）")

        if approved:
            self._check_gate_prerequisites(gate, proposal, v)

        payload = {
            "version_id": version_id,
            "gate": gate.value,
            "decision": "approved" if approved else "rejected",
            "reason": reason,
            "design_hash": v.design_hash,  # 批准的内容哈希当场固定
            "decided_content": {
                "gate": gate.value,
                "version_id": version_id,
                "design_hash": v.design_hash,
            },
        }
        stream = self._stream(proposal_id)
        self.store.append(stream, "gate_decision", payload,
                          actor=actor.id, expected_version=self.store.stream_version(stream))

        # 重新读取判定是否四门齐备（含沿用批准）
        proposal = self._proposal(proposal_id)
        v = proposal.version(version_id)
        if approved and proposal.current is v and proposal.gates_complete(v):
            self.store.append(
                stream, "all_gates_approved",
                {"version_id": version_id, "design_hash": v.design_hash},
                actor=actor.id, expected_version=self.store.stream_version(stream),
            )

    def _check_gate_prerequisites(self, gate: Gate, proposal: Proposal, v: Any) -> None:
        design = v.design
        if gate == Gate.BUSINESS:
            # 经营门：新增渠道必须落在合作方已入库的渠道资质内
            partner = self.catalog().partners.get(proposal.partner_id)
            if partner is not None:
                for ch in design["channels"]:
                    if ch not in partner.channels:
                        raise DomainError(f"合作方 {proposal.partner_id} 无渠道 {ch} 的经营资质")
        if gate == Gate.RIGHTS:
            purpose = Purpose(design["purpose"])
            # 纯展览宣传：入藏时若已取得宣传授权即可
            if purpose == Purpose.EXHIBITION_PROMO:
                artwork = self.catalog().artworks
                refs_ok = all(
                    "展览宣传" in self.catalog().artworks[r["artwork_id"]].baseline_allowed_uses
                    for r in design["artwork_refs"]
                )
                if refs_ok:
                    return
            # 其余用途必须有覆盖四维+用途的有效许可
            lic = v.license
            if lic is None or lic["status"] != "active":
                raise ReleaseBlocked("权利确认要求一份生效许可（数量/地区/渠道/期限/用途）")
            scope = self._scope_from(lic["scope"])
            for channel in design["channels"]:
                for region in design["regions"]:
                    if not scope.covers(
                        quantity=design["quantity"], region=region, channel=channel,
                        on_date=self._today(), purpose=purpose,
                    ):
                        raise ReleaseBlocked("许可范围不覆盖设计方案的渠道/地区/数量/期限/用途")

    # ======================================================================
    # 3. 权利：元素占用（并行打样 / 排他意向）与许可授予
    # ======================================================================
    def reserve_elements(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        version_id: str,
        mode: ReservationMode,
        valid_until: str,
    ) -> None:
        proposal = self._proposal(proposal_id)
        self._require_gate_role(actor, Gate.RIGHTS)
        v = proposal.version(version_id)
        elements = [(r["artwork_id"], e) for r in v.design["artwork_refs"] for e in r["element_ids"]]
        ledger = self.ledger(self._today())

        # 竞争裁决
        for key in (f"{a}/{e}" for a, e in elements):
            blocker = ledger.exclusive_holder(key, self._today())
            released = ledger.released_claim(key, self._today())
            if blocker is not None and blocker.partner_id != proposal.partner_id:
                raise ElementConflict(
                    f"元素 {key} 已被 {blocker.partner_id} 排他占用至 {blocker.valid_until}"
                )
            if mode == ReservationMode.EXCLUSIVE_OPTION:
                active_others = [
                    c for c in ledger.active_claims(key, self._today())
                    if c.partner_id != proposal.partner_id
                ]
                released_by_other = (
                    released is not None and released.partner_id != proposal.partner_id
                )
                if active_others or released_by_other:
                    raise ElementConflict(f"元素 {key} 已有并行占用或已量产，无法再授予排他意向")
            if released is not None and released.partner_id != proposal.partner_id:
                raise ElementConflict(f"元素 {key} 已被 {released.partner_id} 抢先量产")

        payload = {
            "version_id": version_id,
            "partner_id": proposal.partner_id,
            "mode": mode.value,
            "granted": True,
            "elements": elements,
            "valid_until": valid_until,
        }
        self.store.append(self._stream(proposal_id), "reservation_decided", payload,
                          actor=actor.id,
                          expected_version=self.store.stream_version(self._stream(proposal_id)))

    def grant_license(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        version_id: str,
        holder_id: str,
        scope: LicenseScope,
        contract_ref: str,
        contract_body: dict[str, Any],
    ) -> None:
        self._require_gate_role(actor, Gate.RIGHTS)
        proposal = self._proposal(proposal_id)
        v = proposal.version(version_id)
        if holder_id not in self.catalog().artworks[self._artwork_id(v)].holder_ids:
            raise DomainError("许可授予方不在该作品权利人名录中，须先补录入藏权利资料")
        body_hash = content_hash(contract_body)
        self._contracts[contract_ref] = {"body": contract_body, "hash": body_hash,
                                         "holder_id": holder_id}
        payload = {
            "version_id": version_id,
            "holder_id": holder_id,
            "scope": self._scope_dict(scope),
            "contract_ref": contract_ref,
            "contract_hash": body_hash,  # 事件里只有编号+哈希，没有正文
        }
        self.store.append(self._stream(proposal_id), "license_granted", payload,
                          actor=actor.id,
                          expected_version=self.store.stream_version(self._stream(proposal_id)))

    def read_contract_body(self, actor: Actor, contract_ref: str) -> dict[str, Any]:
        """合同正文仅权利与经营两条线可读；合作方与普通运营被拒绝。"""
        if actor.role_enum not in CONTRACT_BODY_ROLES:
            raise AccessDenied("合同正文不对该角色开放")
        if contract_ref not in self._contracts:
            raise DomainError("合同不存在")
        return self._contracts[contract_ref]["body"]

    # ======================================================================
    # 4. 生产：并行打样 / 放行量产（许可四维 + 元素竞争双重校验）
    # ======================================================================
    def start_sampling(
        self, actor: Actor, *, proposal_id: str, version_id: str, quantity: int = 5
    ) -> None:
        proposal = self._proposal(proposal_id)
        self._require_participant_or_staff(actor, proposal.partner_id)
        v = proposal.version(version_id)
        # 打样的红线：学术与权利两门必须已过——这正是事故中缺失的环节
        for g in (Gate.ACADEMIC, Gate.RIGHTS):
            d = v.gates.get(g)
            if d is None or d.status not in ("approved", "carried"):
                raise ReleaseBlocked(f"未完成 {g.value} 审核前禁止打样")
        if proposal.withdrawal is not None:
            raise ReleaseBlocked("权利处于撤回状态，禁止继续打样")
        self._assert_no_element_blocker(proposal, v, allow_parallel=True)
        self.store.append(
            self._stream(proposal_id), "sampling_started",
            {"version_id": version_id, "quantity": quantity},
            actor=actor.id,
            expected_version=self.store.stream_version(self._stream(proposal_id)),
        )

    def release_production(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        version_id: str,
        quantity: int,
        channel: str,
        region: str,
        on_date: str | None = None,
    ) -> None:
        proposal = self._proposal(proposal_id)
        if actor.role_enum.value not in ("business", "producer"):
            raise AccessDenied("仅经营/生产角色可放行量产")
        v = proposal.version(version_id)
        on_date = on_date or self._today()

        # 1) 四门齐备且为当前版本
        if proposal.current is not v:
            raise ReleaseBlocked("只能放行当前版本，历史版本冻结")
        if not proposal.gates_complete(v) or proposal.any_rejected(v):
            raise ReleaseBlocked("四道审核门未全部通过")
        if channel not in v.design["channels"] or region not in v.design["regions"]:
            raise ReleaseBlocked("放行渠道/地区不在已批准设计方案内")
        # 2) 撤回冻结
        if proposal.withdrawal is not None:
            raise ReleaseBlocked("权利已撤回，禁止新放行")
        # 3) 许可四维 + 用途 + 累计数量
        purpose = Purpose(v.design["purpose"])
        if purpose != Purpose.EXHIBITION_PROMO:
            lic = v.license
            if lic is None or lic["status"] != "active":
                raise ReleaseBlocked("缺少生效许可")
            scope = self._scope_from(lic["scope"])
            produced = sum(r["quantity"] for r in v.releases)
            if not scope.covers(quantity=produced + quantity, region=region,
                                channel=channel, on_date=on_date, purpose=purpose):
                raise ReleaseBlocked(
                    f"放行超出许可边界（累计 {produced + quantity}，地区 {region}，"
                    f"渠道 {channel}，日期 {on_date}，用途 {purpose.value}）"
                )
            # 改期后新档期落在许可期限外：必须先复核权利门
            exhibition = self.catalog().exhibitions[proposal.exhibition_id]
            if exhibition.opens_on > scope.valid_until or exhibition.closes_on > scope.valid_until:
                raise ReleaseBlocked(
                    "展览改期后的档期超出许可期限，须先重启权利/经营门复核"
                )
        # 4) 元素竞争：排他占用或被他人抢先量产即阻断
        self._assert_no_element_blocker(proposal, v, allow_parallel=False)

        self.store.append(
            self._stream(proposal_id), "production_released",
            {"version_id": version_id, "quantity": quantity,
             "channel": channel, "region": region, "on_date": on_date,
             "partner_id": proposal.partner_id,
             "elements": [(r["artwork_id"], e)
                          for r in v.design["artwork_refs"]
                          for e in r["element_ids"]]},
            actor=actor.id,
            expected_version=self.store.stream_version(self._stream(proposal_id)),
        )

    def register_instances(
        self, actor: Actor, *, proposal_id: str, instances: list[dict[str, Any]]
    ) -> None:
        proposal = self._proposal(proposal_id)
        if actor.role_enum.value not in ("producer", "business"):
            raise AccessDenied("仅生产/经营角色可登记实例")
        self.store.append(
            self._stream(proposal_id), "instances_registered",
            {"instances": instances},
            actor=actor.id,
            expected_version=self.store.stream_version(self._stream(proposal_id)),
        )

    # ======================================================================
    # 5. 退市触发：权利人撤回 / 开幕改期 —— 按实例状态分别处置
    # ======================================================================
    def rights_withdrawn(
        self,
        actor: Actor,
        *,
        proposal_id: str | None = None,
        holder_id: str,
        effective_on: str,
        reason: str = "",
        proposal_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self._require_gate_role(actor, Gate.RIGHTS)
        targets = proposal_ids or ([proposal_id] if proposal_id else [p for p in self.list_proposal_ids()])
        results: list[dict[str, Any]] = []
        for pid in targets:
            proposal = self._proposal(pid)
            if proposal.withdrawal is not None:
                continue  # 已在撤回处置中，不重复覆盖既有处置决定
            if holder_id not in self._holder_ids(proposal):
                continue
            stream = self._stream(pid)
            self.store.append(
                stream, "rights_withdrawn",
                {"holder_id": holder_id, "effective_on": effective_on,
                 "scope": {"halts_promo": True}, "reason": reason},
                actor=actor.id, expected_version=self.store.stream_version(stream),
            )
            for disposition in self._emit_dispositions(
                pid, Trigger.RIGHTS_WITHDRAWN, actor, policy=_WITHDRAWAL_POLICY,
            ):
                results.append(disposition)
        return results

    def reschedule_exhibition(
        self, actor: Actor, *, exhibition_id: str, new_opens_on: str, new_closes_on: str
    ) -> list[dict[str, Any]]:
        from .model import Role
        if actor.role_enum not in (Role.CURATOR, Role.BUSINESS_OFFICER):
            raise AccessDenied("仅策展/经营可调整展期")
        catalog = self.catalog()
        ex = catalog.exhibitions.get(exhibition_id)
        if ex is None:
            raise DomainError("展览不存在")
        old_opens = ex.opens_on
        self.store.append(
            CATALOG_STREAM, "exhibition_rescheduled",
            {"exhibition_id": exhibition_id, "old_opens_on": old_opens,
             "new_opens_on": new_opens_on, "new_closes_on": new_closes_on},
            actor=actor.id, expected_version=self.store.stream_version(CATALOG_STREAM),
        )
        results: list[dict[str, Any]] = []
        for pid in self.list_proposal_ids():
            proposal = self._proposal(pid)
            if proposal.exhibition_id != exhibition_id or proposal.stage == "delisted":
                continue
            for disposition in self._emit_reschedule_dispositions(pid, actor, new_opens_on):
                results.append(disposition)
        return results

    def delist(self, actor: Actor, *, proposal_id: str, reason: str = "") -> None:
        from .model import Role
        if actor.role_enum not in (Role.BUSINESS_OFFICER, Role.RIGHTS_OFFICER):
            raise AccessDenied("仅经营/权利角色可执行退市")
        self.store.append(
            self._stream(proposal_id), "proposal_delisted", {"reason": reason},
            actor=actor.id,
            expected_version=self.store.stream_version(self._stream(proposal_id)),
        )

    # ======================================================================
    # 6. 沟通留痕（替代群聊）
    # ======================================================================
    def log_communication(
        self,
        actor: Actor,
        *,
        proposal_id: str,
        thread: str,
        participants: list[str],
        visibility: str,  # internal / partner
        body: str,
    ) -> str:
        proposal = self._proposal(proposal_id)
        if actor.role_enum.value == "partner":
            if actor.partner_id != proposal.partner_id or visibility != "partner":
                raise AccessDenied("合作方只能在本提案对外线程留言")
        communication_id = f"comm-{len(proposal.communications) + 1}-{self.store.stream_version(self._stream(proposal_id))}"
        payload = {
            "communication_id": communication_id,
            "thread": thread,
            "participants": participants,
            "visibility": visibility,
            "body": body,
            "body_hash": content_hash(body),
        }
        self.store.append(
            self._stream(proposal_id), "communication_logged", payload,
            actor=actor.id,
            expected_version=self.store.stream_version(self._stream(proposal_id)),
        )
        return communication_id

    # ======================================================================
    # 7. 查询与溯源
    # ======================================================================
    def view_proposal(self, actor: Actor, proposal_id: str) -> dict[str, Any]:
        proposal = self._proposal(proposal_id)
        if not can_access_proposal(actor, proposal.partner_id):
            raise AccessDenied("无权查看该提案")
        bundle = self._bundle(proposal)
        return project_proposal(bundle, actor)

    def curator_trace(self, actor: Actor, *, proposal_id: str) -> dict[str, Any]:
        """策展人拿到任一上市产品后的完整回查：
        原作 → 沟通过程 → 每个批准版本（含哈希）→ 当前权利边界 → 实例处置。
        """
        from .model import Role
        if actor.role_enum != Role.CURATOR_AUDIT and actor.role_enum != Role.CURATOR:
            raise AccessDenied("仅策展溯源角色可取完整回查视图")
        proposal = self._proposal(proposal_id)
        bundle = self._bundle(proposal)
        bundle["trace_order"] = [
            "artwork", "communications", "versions", "license_boundary",
            "instances", "dispositions", "event_chain",
        ]
        return bundle

    def verify_integrity(self) -> None:
        self.store.verify_chain()

    # ---- 投影读取 -------------------------------------------------------
    def catalog(self) -> Catalog:
        cat = Catalog()
        for e in self.store.read_stream(CATALOG_STREAM):
            Catalog.apply(cat, e)
        return cat

    def ledger(self, today: str) -> ElementLedger:
        ledger = ElementLedger()
        for e in self.store.read_all():
            if e.stream.startswith("proposal:"):
                ElementLedger.apply(ledger, e, today)
        return ledger

    def list_proposal_ids(self) -> list[str]:
        return sorted({e.stream.split(":", 1)[1] for e in self.store.read_all()
                       if e.stream.startswith("proposal:")})

    # ======================================================================
    # 内部辅助
    # ======================================================================
    def _emit_dispositions(self, pid: str, trigger: Trigger, actor: Actor, *, policy) -> list[dict]:
        proposal = self._proposal(pid)
        out = []
        for inst_id, inst in sorted(proposal.instances.items()):
            if inst["state"] in ("cancelled", "recalled", "promo_halted"):
                continue  # 已处置过的实例不重复处置
            action, rationale, new_state = policy[inst["state"]]
            payload = {
                "trigger": trigger.value,
                "instance_id": inst_id,
                "state": inst["state"],
                "action": action.value,
                "rationale": rationale,
                "new_state": new_state,
            }
            self.store.append(
                self._stream(pid), "instance_disposition", payload,
                actor=actor.id,
                expected_version=self.store.stream_version(self._stream(pid)),
            )
            out.append(payload)
        return out

    def _emit_reschedule_dispositions(self, pid: str, actor: Actor, new_opens_on: str) -> list[dict]:
        proposal = self._proposal(pid)
        policy = {
            InstanceState.NOT_PRODUCED.value: (
                DispositionAction.RESCHEDULE_HOLD,
                "开幕改期：未生产批次暂停排产，按新档期重排", None),
            InstanceState.IN_TRANSIT.value: (
                DispositionAction.RESCHEDULE_HOLD,
                "开幕改期：在途货物转暂存，不召回不销毁，待新档期铺货", None),
            InstanceState.SOLD.value: (
                DispositionAction.HONOR_SOLD_COPIES,
                "已售成品不受改期影响，停止在旧档期继续销售", None),
        }
        # 展宣品单独判断：新开幕日仍在许可/宣传期内则继续，否则须复核
        cur = proposal.current
        promo_action = DispositionAction.PROMO_CONTINUE
        promo_reason = "开幕改期：展览宣传用途可在新档期继续"
        if cur.license is not None and cur.license["status"] == "active":
            if self._scope_from(cur.license["scope"]).valid_until < new_opens_on:
                promo_action = DispositionAction.RESCHEDULE_REVALIDATE
                promo_reason = "新开幕日晚于许可到期日，宣传物料须先复核权利期限"
        policy[InstanceState.PROMO_ONLY.value] = (promo_action, promo_reason, None)
        return self._emit_dispositions(pid, Trigger.OPENING_RESCHEDULED, actor, policy=policy)

    def _assert_no_element_blocker(self, proposal: Proposal, v: Any, *, allow_parallel: bool) -> None:
        for r in v.design["artwork_refs"]:
            for eid in r["element_ids"]:
                key = f"{r['artwork_id']}/{eid}"
                exclusive = self.ledger(self._today()).exclusive_holder(key, self._today())
                released = self.ledger(self._today()).released_claim(key, self._today())
                if exclusive is not None and exclusive.partner_id != proposal.partner_id:
                    raise ElementConflict(f"元素 {key} 被 {exclusive.partner_id} 排他占用")
                if released is not None and released.partner_id != proposal.partner_id:
                    raise ElementConflict(f"元素 {key} 已被 {released.partner_id} 量产，先到先得")
                if not allow_parallel:
                    active = [c for c in self.ledger(self._today()).active_claims(key, self._today())
                              if c.partner_id != proposal.partner_id and c.mode == "exclusive_option"]
                    if active:
                        raise ElementConflict(f"元素 {key} 存在他方排他意向")

    def _bundle(self, proposal: Proposal) -> dict[str, Any]:
        catalog = self.catalog()
        artwork_ids = sorted({
            r["artwork_id"] for v in proposal.versions for r in v.design["artwork_refs"]
        })
        artworks = []
        for aid in artwork_ids:
            aw = catalog.artworks.get(aid)
            artworks.append({
                "artwork_id": aid,
                "title": aw.title if aw else None,
                "holder_ids": aw.holder_ids if aw else [],
                "elements": [
                    {"element_id": e.element_id, "name": e.name, "rights": e.rights}
                    for e in (aw.elements.values() if aw else [])
                ],
                "baseline_allowed_uses": aw.baseline_allowed_uses if aw else [],
            })
        exhibition = catalog.exhibitions.get(proposal.exhibition_id)
        versions = [
            {
                "version_id": v.version_id,
                "version_no": v.version_no,
                "parent_version_id": v.parent_version_id,
                "changed_facets": v.changed_facets,
                "design": v.design,
                "design_hash": v.design_hash,
                "submitted_at": v.submitted_at,
                "submitted_by": v.submitted_by,
                "gates": [
                    {
                        "gate": d.gate.value,
                        "status": d.status,
                        "by": d.by,
                        "at": d.at,
                        "reason": d.reason,
                        "design_hash": d.design_hash,
                        "source_version_id": d.source_version_id,
                    }
                    for d in v.gates.values()
                ],
                "license": v.license,
                "reservation": v.reservation,
                "releases": v.releases,
                "samples": v.samples,
            }
            for v in proposal.versions
        ]
        cur = proposal.current
        return {
            "proposal_id": proposal.proposal_id,
            "exhibition_id": proposal.exhibition_id,
            "exhibition": {
                "opens_on": exhibition.opens_on if exhibition else None,
                "closes_on": exhibition.closes_on if exhibition else None,
                "history": exhibition.history if exhibition else [],
            },
            "partner_id": proposal.partner_id,
            "stage": proposal.stage,
            "artwork": artworks,
            "versions": versions,
            "current_version_id": cur.version_id,
            "license_boundary": self._license_boundary(proposal),
            "instances": [
                {"instance_id": iid, **body} for iid, body in sorted(proposal.instances.items())
            ],
            "dispositions": proposal.dispositions,
            "communications": proposal.communications,
            "withdrawal": proposal.withdrawal,
            "event_chain": [e.to_dict() for e in self.store.read_stream(self._stream(proposal.proposal_id))],
        }

    def _license_boundary(self, proposal: Proposal) -> dict[str, Any] | None:
        """当前权利边界：有效许可四维；撤回/超期则显式标注。"""
        cur = proposal.current
        if proposal.withdrawal is not None:
            return {"status": "withdrawn", "as_of": proposal.withdrawal["effective_on"],
                    "holder_id": proposal.withdrawal["holder_id"], "scope": None}
        if cur.license is None:
            return {"status": "none", "scope": None}
        return {"status": cur.license["status"], "scope": cur.license["scope"],
                "contract_ref": cur.license["contract_ref"],
                "contract_hash": cur.license["contract_hash"]}

    def _proposal(self, proposal_id: str) -> Proposal:
        stream = self._stream(proposal_id)
        events = self.store.read_stream(stream)
        if not events:
            raise DomainError("提案不存在")
        first = events[0].payload
        p = Proposal(
            proposal_id=proposal_id,
            exhibition_id=first["exhibition_id"],
            partner_id=first["partner_id"],
            created_at=events[0].at,
        )
        for e in events:
            apply_proposal(p, e)
        return p

    def _holder_ids(self, proposal: Proposal) -> list[str]:
        ids: set[str] = set()
        catalog = self.catalog()
        for r in proposal.current.design["artwork_refs"]:
            aw = catalog.artworks.get(r["artwork_id"])
            if aw:
                ids.update(aw.holder_ids)
        return sorted(ids)

    def _artwork_id(self, v: Any) -> str:
        return v.design["artwork_refs"][0]["artwork_id"]

    @staticmethod
    def _stream(proposal_id: str) -> str:
        return f"proposal:{proposal_id}"

    def _today(self) -> str:
        return self._clock()[:10]

    @staticmethod
    def _scope_dict(scope: LicenseScope) -> dict[str, Any]:
        return {
            "max_quantity": scope.max_quantity,
            "regions": sorted(scope.regions),
            "channels": sorted(scope.channels),
            "valid_from": scope.valid_from,
            "valid_until": scope.valid_until,
            "purpose": scope.purpose.value,
        }

    def _scope_from(self, d: dict[str, Any]) -> LicenseScope:
        return LicenseScope(
            max_quantity=d["max_quantity"],
            regions=frozenset(d["regions"]),
            channels=frozenset(d["channels"]),
            valid_from=d["valid_from"],
            valid_until=d["valid_until"],
            purpose=Purpose(d["purpose"]),
        )

    def _facet_impact(self, f: ChangeFacet) -> FacetImpact:
        from .model import FACET_IMPACT
        return FACET_IMPACT[f]

    def _validate_refs(self, catalog: Catalog, design: DesignContent) -> None:
        if not design.artwork_refs:
            raise DomainError("设计提案必须标明使用哪件作品的哪些元素")
        for ref in design.artwork_refs:
            aw = catalog.artworks.get(ref.artwork_id)
            if aw is None:
                raise DomainError(f"作品 {ref.artwork_id} 未入库，先完成资料建档")
            missing = [e for e in ref.element_ids if e not in aw.elements]
            if missing:
                raise DomainError(f"作品 {ref.artwork_id} 上不存在元素 {missing}")
            if not ref.mode:
                raise DomainError("必须标明元素使用方式（复制 / 商业改编）")

    # ---- 角色校验 -------------------------------------------------------
    @staticmethod
    def _require_staff(actor: Actor) -> None:
        from .access import STAFF_ROLES
        if actor.role_enum not in STAFF_ROLES:
            raise AccessDenied("仅馆内角色可操作")

    @staticmethod
    def _require_gate_role(actor: Actor, gate: Gate) -> None:
        required = GATE_ROLE[gate]
        if actor.role_enum != required:
            raise AccessDenied(f"{gate.value} 门只能由 {required.value} 角色审批")

    @staticmethod
    def _require_participant(actor: Actor, partner_id: str) -> None:
        from .model import Role
        if actor.role_enum == Role.PARTNER and actor.partner_id != partner_id:
            raise AccessDenied("合作方只能操作本机构提案")
        if actor.role_enum not in (Role.PARTNER, Role.CURATOR, Role.BUSINESS_OFFICER,
                                   Role.RIGHTS_OFFICER, Role.PUBLIC_BENEFIT_OFFICER):
            raise AccessDenied("该角色不能修改设计")

    @staticmethod
    def _require_participant_or_staff(actor: Actor, partner_id: str) -> None:
        from .access import STAFF_ROLES
        if actor.role_enum in STAFF_ROLES:
            return
        if actor.role_enum.value == "partner" and actor.partner_id == partner_id:
            return
        raise AccessDenied("无权操作该提案")
