"""并行打样放行、两个合作方竞争同一艺术元素时独占阻断、撤回后重新放行。"""
import unittest

from museum_collab.errors import ConflictError
from museum_collab.licensing import Channel, Right

from fixtures_support import (
    fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class SamplingCompetitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()

    def _proposals_for_two_partners(self) -> tuple[str, str]:
        pid_a = make_proposal(self.p, self.a["pA"], partner_id="partner-A")
        pid_b = make_proposal(self.p, self.a["pB"], partner_id="partner-B",
                              title="水波纹围巾")
        return pid_a, pid_b

    def test_parallel_sampling_both_partners_allowed(self) -> None:
        pid_a, pid_b = self._proposals_for_two_partners()
        self.p.start_sampling(self.a["pA"], pid_a)
        self.p.start_sampling(self.a["pB"], pid_b)  # 不抛冲突
        sample_entries = self.p.ledger.by_kind("sample_started")
        self.assertEqual(len(sample_entries), 2)

    def test_exclusive_license_blocks_competing_partner(self) -> None:
        pid_a, pid_b = self._proposals_for_two_partners()
        self.p.start_sampling(self.a["pA"], pid_a)
        self.p.start_sampling(self.a["pB"], pid_b)
        # 选型：A 中标、B 落选；落选方释放元素锁
        self.p.finish_sampling(self.a["pB"], pid_b, won=False)
        self.p.finish_sampling(self.a["pA"], pid_a, won=True)
        # 艺术家对 A 独占授权该元素
        self.p.grant_license(self.a["holder"], **standard_license_kwargs(
            partner_id="partner-A", exclusive=True))
        # B 落选后仍想取得同一元素的商业许可：必须被明确阻断
        with self.assertRaises(ConflictError):
            self.p.grant_license(self.a["holder"], **standard_license_kwargs(
                partner_id="partner-B", exclusive=False))

    def test_exclusive_grant_blocked_while_other_sampling(self) -> None:
        pid_a, pid_b = self._proposals_for_two_partners()
        self.p.start_sampling(self.a["pA"], pid_a)
        self.p.start_sampling(self.a["pB"], pid_b)
        # B 仍在并行打样阶段时，任何人都不能取得该元素独占权
        with self.assertRaises(ConflictError):
            self.p.grant_license(self.a["holder"], **standard_license_kwargs(
                partner_id="partner-A", exclusive=True))

    def test_nonexclusive_commercial_uses_can_coexist(self) -> None:
        pid_a, pid_b = self._proposals_for_two_partners()
        self.p.grant_license(self.a["holder"], **standard_license_kwargs(
            partner_id="partner-A"))
        self.p.grant_license(self.a["holder"], **standard_license_kwargs(
            partner_id="partner-B"))  # 非独占并存，放行

    def test_after_revocation_competitor_can_acquire(self) -> None:
        pid_a, pid_b = self._proposals_for_two_partners()
        lic_a = self.p.grant_license(
            self.a["holder"], **standard_license_kwargs(
                partner_id="partner-A", exclusive=True))
        with self.assertRaises(ConflictError):
            self.p.grant_license(self.a["holder"], **standard_license_kwargs(
                partner_id="partner-B", exclusive=True))
        # A 的独占许可被权利人撤回，锁释放
        self.p.revoke_license(self.a["holder"], lic_a,
                              on_date="2026-09-10", reason="艺术家终止合作")
        # B 现在可以取得独占权
        self.p.grant_license(self.a["holder"], **standard_license_kwargs(
            partner_id="partner-B", exclusive=True,
            starts_on="2026-09-11"))


if __name__ == "__main__":
    unittest.main()
