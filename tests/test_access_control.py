"""信息隔离：合作方只见本机构环节、合同正文不对普通运营开放、哈希固定可见。"""
from __future__ import annotations

import unittest

from museum_collab import AccessDenied

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design


class AccessControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = build_system()
        self.pid = "p-scarf"
        self.v1 = self.sys.create_proposal(
            partner("partner-a"), proposal_id=self.pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )
        approve_all_gates(self.sys, self.pid, self.v1)
        self.sys.log_communication(
            STAFF["rights"], proposal_id=self.pid, thread="rights-negotiation",
            participants=["u-rights", "artist-chen"], visibility="internal",
            body="权利人要求改编稿需再确认一次（内部记录）",
        )
        self.sys.log_communication(
            STAFF["business"], proposal_id=self.pid, thread="external",
            participants=["p-partner-a-1", "partner-a"], visibility="partner",
            body="请按 v1 规格推进打样",
        )

    def test_partner_cannot_see_other_partner_proposal(self) -> None:
        with self.assertRaises(AccessDenied):
            self.sys.view_proposal(partner("partner-b"), self.pid)

    def test_partner_view_hides_contract_and_internal_thread(self) -> None:
        view = self.sys.view_proposal(partner("partner-a"), self.pid)
        # 许可边界仍在（能知道能做多少、卖到哪、卖到何时），但合同/权利人标识隐藏
        self.assertIn("scope", view["license_boundary"])
        self.assertNotIn("contract_ref", view["license_boundary"])
        self.assertNotIn("holder_id", view["license_boundary"])
        for v in view["versions"]:
            self.assertNotIn("contract_ref", v["license"])
        # 只见对外沟通，不见内部线程
        threads = {c["thread"] for c in view["communications"]}
        self.assertEqual(threads, {"external"})

    def test_producer_cannot_read_contract_body(self) -> None:
        with self.assertRaises(AccessDenied):
            self.sys.read_contract_body(STAFF["producer"], f"CT-{self.pid}-001")
        with self.assertRaises(AccessDenied):
            self.sys.read_contract_body(partner("partner-a"), f"CT-{self.pid}-001")

    def test_rights_and_business_can_read_contract_body(self) -> None:
        body = self.sys.read_contract_body(STAFF["rights"], f"CT-{self.pid}-001")
        self.assertTrue(body["signed"])
        self.assertEqual(body["royalty"], "8%")

    def test_producer_view_is_limited_to_production_slice(self) -> None:
        view = self.sys.view_proposal(STAFF["producer"], self.pid)
        self.assertEqual(view["communications"], [])
        # 但生产所需的设计哈希、批准状态、放行记录都在
        self.assertTrue(view["versions"][0]["design_hash"])
        gate_names = {g["gate"] for g in view["versions"][0]["gates"]}
        self.assertEqual(len(gate_names), 4)

    def test_approval_content_is_pinned_by_hash(self) -> None:
        view = self.sys.view_proposal(STAFF["curator"], self.pid)
        v = view["versions"][0]
        for gate in v["gates"]:
            self.assertEqual(gate["design_hash"], v["design_hash"])
        # 许可记录只挂合同哈希与编号
        self.assertTrue(view["versions"][0]["license"]["contract_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
