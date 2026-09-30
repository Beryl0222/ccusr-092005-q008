"""策展人拿到任一上市产品，完整回查原作、沟通、批准版本与当前权利边界。"""
import unittest

from museum_collab.actors import Gate
from museum_collab.licensing import Channel
from museum_collab.production import InstanceState

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class DossierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()
        self.pid = make_proposal(self.p, self.a["designer"], quantity=2)
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        self.p.post_message(
            self.a["pA"], self.pid, "申请将水波纹用于丝巾",
            audience=("partner-A",))
        self.p.post_message(
            self.a["holder"], self.pid,
            "同意复制及商业改编，数量5000，限中国大陆，至2027-06-30",
            audience=(self.a["pA"].partner_id,))
        approve_all(self.p, self.pid, self.a)
        contract = self.p.record_contract(
            self.a["admin"], "partner-A", "丝巾采购合同",
            body="合同正文：……", body_ref="sealed://contracts/c1")
        self.order_id = self.p.create_production_order(
            self.a["ops"], proposal_id=self.pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=2,
            on_date="2026-09-20", contract_id=contract.contract_id)
        self.sold_item = f"{self.order_id}-item-0001"
        self.p.transition_instance(
            self.a["ops"], self.sold_item, InstanceState.IN_TRANSIT,
            on_date="2026-09-22")
        self.p.transition_instance(
            self.a["ops"], self.sold_item, InstanceState.SOLD,
            on_date="2026-09-28")

    def test_dossier_covers_artwork_communication_approvals_boundary(self) -> None:
        d = self.p.curator_dossier(self.a["curator"], self.sold_item)
        # 原作与元素
        self.assertEqual(d["artworks"][0]["artwork_id"],
                         "artwork-river-17")
        element_ids = {e["element_id"] for e in d["artworks"][0]["elements"]}
        self.assertIn("el-wave-motif", element_ids)
        # 沟通过程完整
        self.assertEqual(len(d["communication"]), 2)
        self.assertTrue(all(m["content_digest"]["content_sha256"]
                            for m in d["communication"]))
        # 四个门的批准记录、批准的版本哈希
        v1 = next(t for t in d["version_trace"] if t["version"] == 1)
        gates = {r["gate"] for r in v1["approvals"]}
        self.assertEqual(gates, set(Gate.ALL))
        # 工单钉住的版本哈希等于回溯到的批准版本哈希
        self.assertEqual(d["product"]["version_sha256_pinned_by_order"],
                         v1["content_sha256"])
        # 当前权利边界：数量/地区/渠道/期限可见
        boundary = d["rights_boundary"][0]["scope"]
        self.assertEqual(boundary["regions"], ["CN"])
        self.assertEqual(boundary["quantity"], 5000)
        self.assertIn(Channel.MUSEUM_SHOP, boundary["channels"])
        self.assertEqual(boundary["ends_on"], "2027-06-30")

    def test_dossier_shows_carried_approvals_after_channel_only_revision(
            self) -> None:
        # 上市前改渠道（权利门重审，学术沿用），回溯要能看到版本关系
        self.p.grant_license(self.a["rights"], **standard_license_kwargs(
            channels=["museum_shop", "online_store", "offline_event"]))
        v2 = self.p.revise_proposal(self.a["designer"], self.pid, {
            "channels": ["museum_shop", "online_store", "offline_event"]})
        self.p.approve(self.a[Gate.RIGHTS], self.pid, Gate.RIGHTS)
        self.p.approve(self.a[Gate.BUSINESS], self.pid, Gate.BUSINESS)
        d = self.p.curator_dossier(self.a["curator"], self.sold_item)
        trace = {t["version"]: t for t in d["version_trace"]}
        self.assertEqual(trace[1]["invalidated_at_revision"], 2)
        carried = [r for r in trace[2]["approvals"]
                   if r["carried_from"] == 1]
        carried_gates = {r["gate"] for r in carried}
        self.assertIn(Gate.ACADEMIC, carried_gates)
        self.assertIn(Gate.PUBLIC_BENEFIT, carried_gates)
        self.assertNotIn(Gate.RIGHTS, carried_gates)

    def test_dossier_records_revocation_among_dispositions(self) -> None:
        license_id = self.p.active_licenses_for(self.pid)[0].license_id
        self.p.revoke_license(self.a["holder"], license_id,
                              on_date="2026-10-02", reason="权利人撤回")
        d = self.p.curator_dossier(self.a["curator"], self.sold_item)
        self.assertTrue(d["dispositions"])
        # 当前权利边界仍能看到这条许可，但状态是 revoked
        revoked = [b for b in d["rights_boundary"] if b["status"] == "revoked"]
        self.assertEqual(len(revoked), 1)
        self.assertEqual(revoked[0]["revoked_reason"], "权利人撤回")
        # 旧的批准记录没有被覆盖删除
        v1 = next(t for t in d["version_trace"] if t["version"] == 1)
        self.assertEqual(len(v1["approvals"]), 4)

    def test_non_curator_roles_are_gated(self) -> None:
        from museum_collab.errors import AuthorizationError
        with self.assertRaises(AuthorizationError):
            self.p.curator_dossier(self.a["ops"], self.sold_item)
        with self.assertRaises(AuthorizationError):
            self.p.curator_dossier(self.a["pA"], self.sold_item)


if __name__ == "__main__":
    unittest.main()
