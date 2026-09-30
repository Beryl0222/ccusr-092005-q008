"""变更影响矩阵：图案/材质/渠道/文案变化只重启必要的门，许可按需失效或沿用。"""
from __future__ import annotations

import unittest

from museum_collab import ChangeFacet, Gate
from museum_collab.store import ConcurrencyError

from helpers import approve_all_gates, build_system, partner, scarf_design


def gate_status(version, gate: Gate) -> str:
    decisions = version.gates
    if isinstance(decisions, dict):
        d = decisions.get(gate)
        return "absent" if d is None else d.status
    return next((g["status"] for g in decisions if g["gate"] == gate.value), "absent")


class RevisionImpactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = build_system()
        self.pid = "p-scarf"
        self.v1 = self.sys.create_proposal(
            partner("partner-a"), proposal_id=self.pid,
            exhibition_id="exh-2026-modern", partner_id="partner-a",
            design=scarf_design(),
        )
        approve_all_gates(self.sys, self.pid, self.v1)

    def _revise(self, facets, **design_over):
        self.sys.revise_proposal(
            partner("partner-a"), proposal_id=self.pid,
            facets=facets, design=scarf_design(**design_over),
        )
        return self.sys._proposal(self.pid).current

    def test_material_change_reopens_only_academic_and_rights(self) -> None:
        v2 = self._revise([ChangeFacet.MATERIAL], material="cotton-linen")
        # 学术、权利两门待重审
        self.assertNotIn(Gate.ACADEMIC, v2.gates)
        self.assertNotIn(Gate.RIGHTS, v2.gates)
        # 公益、经营两门的批准自 v1 沿用，不重复打扰
        self.assertEqual(gate_status(v2, Gate.BENEFIT), "carried")
        self.assertEqual(gate_status(v2, Gate.BUSINESS), "carried")
        # 许可不触及边界：沿用到新版本（同一合同）
        self.assertEqual(v2.license["status"], "active")
        self.assertEqual(v2.license["carried_from_version_id"], self.v1)

    def test_adaptation_change_invalidates_license_and_old_one_kept(self) -> None:
        v2 = self._revise([ChangeFacet.ADAPTATION],
                          spec={"pattern": "主山体旋转 90 度"})
        # 学术、权利、经营三门重审
        self.assertNotIn(Gate.ACADEMIC, v2.gates)
        self.assertNotIn(Gate.RIGHTS, v2.gates)
        self.assertNotIn(Gate.BUSINESS, v2.gates)
        self.assertEqual(gate_status(v2, Gate.BENEFIT), "carried")
        # 新图案需要新许可：v2 无生效许可
        self.assertIsNone(v2.license)
        # 旧决定不被覆盖：v1 许可仍可查，只是被标记 superseded
        old = self.sys._proposal(self.pid).versions[0]
        self.assertEqual(old.license["status"], "superseded")
        self.assertTrue(old.gates[Gate.RIGHTS].status in ("approved", "carried"))

    def test_channel_change_reopens_rights_and_business_only(self) -> None:
        v2 = self._revise(
            [ChangeFacet.CHANNEL], channels=("museum-shop", "online-mall"),
        )
        self.assertNotIn(Gate.RIGHTS, v2.gates)
        self.assertNotIn(Gate.BUSINESS, v2.gates)
        self.assertEqual(gate_status(v2, Gate.ACADEMIC), "carried")
        self.assertEqual(gate_status(v2, Gate.BENEFIT), "carried")

    def test_minor_text_reopens_nothing_and_stays_approved(self) -> None:
        v2 = self._revise([ChangeFacet.MINOR_TEXT],
                          spec={"tagline": "江行千里"})
        for gate in Gate:
            self.assertEqual(gate_status(v2, gate), "carried")
        self.assertEqual(self.sys._proposal(self.pid).stage, "approved")

    def test_concurrent_revisions_second_commit_is_rejected(self) -> None:
        stream_version = self.sys.store.stream_version(f"proposal:{self.pid}")
        self.sys.revise_proposal(
            partner("partner-a"), proposal_id=self.pid,
            facets=[ChangeFacet.MINOR_TEXT],
            design=scarf_design(spec={"tagline": "甲的订正"}),
            expected_version=stream_version,
        )
        # 同一基准版本的另一次并发提交必须失败，而不是悄悄覆盖
        with self.assertRaises(ConcurrencyError):
            self.sys.revise_proposal(
                partner("partner-a", 2), proposal_id=self.pid,
                facets=[ChangeFacet.MINOR_TEXT],
                design=scarf_design(spec={"tagline": "并发的订正"}),
                expected_version=stream_version,
            )


if __name__ == "__main__":
    unittest.main()
