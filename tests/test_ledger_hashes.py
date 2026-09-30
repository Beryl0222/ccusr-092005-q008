"""批准内容用哈希与版本关系固定；台账防篡改。"""
import unittest

from museum_collab.actors import Gate
from museum_collab.errors import LedgerError
from museum_collab.licensing import Channel
from museum_collab.proposals import Change, CHANGE_IMPACT

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class LedgerHashTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()

    def test_approval_pins_exact_version_hash(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        v1_hash = self.p.proposals[pid].versions[1].content_sha256
        self.p.grant_license(self.a["rights"], **standard_license_kwargs())
        self.p.approve(self.a[Gate.ACADEMIC], pid, Gate.ACADEMIC, "纹样取用忠实")
        rec = self.p.proposals[pid].versions[1].approvals[Gate.ACADEMIC]
        self.assertEqual(rec.content_sha256, v1_hash)
        self.assertEqual(rec.version, 1)

    def test_ledger_chain_detects_tampering(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        self.p.post_message(self.a["pA"], pid, "请确认能否用于丝巾",
                            audience=("partner-A",))
        self.p.ledger.verify()  # 未篡改时通过
        # 模拟有人改写历史沟通内容
        entry = self.p.ledger.by_kind("message_posted")[0]
        object.__setattr__(entry, "payload", {"content_sha256": "forged"})
        with self.assertRaises(LedgerError):
            self.p.ledger.verify()

    def test_revision_links_parent_version_and_new_hash(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        h1 = self.p.proposals[pid].versions[1].content_sha256
        v2 = self.p.revise_proposal(self.a["designer"], pid, {"material": "棉麻"})
        h2 = self.p.proposals[pid].versions[2].content_sha256
        self.assertEqual(v2, 2)
        self.assertNotEqual(h1, h2)
        self.assertEqual(
            self.p.proposals[pid].versions[2].content.based_on_version, 1)
        self.assertIsNotNone(
            self.p.proposals[pid].versions[1].invalidated_at_revision)

    def test_change_impact_table_covers_every_change_kind(self) -> None:
        # 每一种变更类别都必须显式声明影响哪些审批门（可为空集），
        # 防止将来新增变更类型时悄悄触发"全部重审"或"漏审"
        for kind in Change.ALL:
            self.assertIn(kind, CHANGE_IMPACT)
        self.assertEqual(CHANGE_IMPACT[Change.TYPO], frozenset())


if __name__ == "__main__":
    unittest.main()
