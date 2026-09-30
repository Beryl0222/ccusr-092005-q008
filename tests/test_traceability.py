"""策展人溯源：原作 → 沟通 → 批准版本（哈希链）→ 当前权利边界 → 实例处置。
另含日志防篡改校验。"""
from __future__ import annotations

import unittest

from museum_collab import DispositionAction, Trigger
from museum_collab.model import ReservationMode

from helpers import STAFF, approve_all_gates, build_system, partner, scarf_design


class CuratorTraceTest(unittest.TestCase):
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
            body="艺术家同意主山体轮廓用于丝巾，限定 CN 地区与馆店渠道",
        )
        self.sys.reserve_elements(
            STAFF["rights"], proposal_id=self.pid, version_id=self.v1,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2027-12-31",
        )
        self.sys.release_production(
            STAFF["business"], proposal_id=self.pid, version_id=self.v1,
            quantity=300, channel="museum-shop", region="CN", on_date="2026-09-01",
        )
        self.sys.register_instances(
            STAFF["producer"], proposal_id=self.pid,
            instances=[{"instance_id": "i-2", "state": "in_transit", "quantity": 100}],
        )
        self.sys.rights_withdrawn(
            STAFF["rights"], proposal_id=self.pid,
            holder_id="artist-chen", effective_on="2026-09-15",
        )

    def test_full_trace_bundle_covers_every_link(self) -> None:
        trace = self.sys.curator_trace(STAFF["audit"], proposal_id=self.pid)
        # 1) 原作与元素权利基线
        art = trace["artwork"][0]
        self.assertEqual(art["artwork_id"], "artwork-river-17")
        element_ids = {e["element_id"] for e in art["elements"]}
        self.assertIn("main-figure", element_ids)
        # 2) 沟通过程（含内部线程）
        self.assertEqual(trace["communications"][0]["thread"], "rights-negotiation")
        self.assertTrue(trace["communications"][0]["body_hash"].startswith("sha256:"))
        # 3) 批准版本：四门、批准人与被批准内容哈希
        v = trace["versions"][0]
        self.assertEqual({g["gate"] for g in v["gates"]},
                         {"academic", "rights", "benefit", "business"})
        self.assertTrue(v["design_hash"])
        # 4) 当前权利边界：已撤回
        self.assertEqual(trace["license_boundary"]["status"], "withdrawn")
        # 5) 实例与处置
        self.assertEqual(trace["instances"][0]["state"], "recalled")
        self.assertEqual(trace["dispositions"][0]["action"],
                         DispositionAction.RECALL_OR_HOLD.value)
        self.assertEqual(trace["dispositions"][0]["trigger"],
                         Trigger.RIGHTS_WITHDRAWN.value)
        # 6) 完整事件链
        types = [e["type"] for e in trace["event_chain"]]
        for required in ("proposal_created", "license_granted", "gate_decision",
                         "all_gates_approved", "production_released",
                         "rights_withdrawn", "instance_disposition"):
            self.assertIn(required, types)
        # 回查顺序明确
        self.assertEqual(
            trace["trace_order"],
            ["artwork", "communications", "versions", "license_boundary",
             "instances", "dispositions", "event_chain"],
        )

    def test_partner_cannot_take_audit_view(self) -> None:
        from museum_collab import AccessDenied
        with self.assertRaises(AccessDenied):
            self.sys.curator_trace(partner("partner-a"), proposal_id=self.pid)

    def test_event_chain_detects_tampering(self) -> None:
        self.sys.verify_integrity()  # 原始日志校验通过
        # 模拟有人事后改写某条事件 payload（例如抹掉撤回理由、改动许可上限）
        target = next(
            e for e in self.sys.store.read_stream(f"proposal:{self.pid}")
            if e.type == "rights_withdrawn"
        )
        target.payload["reason"] = "被事后篡改的内容"
        from museum_collab.store import IntegrityError
        with self.assertRaises(IntegrityError):
            self.sys.verify_integrity()


if __name__ == "__main__":
    unittest.main()
