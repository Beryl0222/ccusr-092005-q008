"""许可四要素（数量/地区/渠道/期限）与入藏不等于授权。"""
import unittest

from museum_collab.actors import Gate
from museum_collab.errors import LicenseError, ValidationError
from museum_collab.licensing import Channel, Right

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class LicensingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = fresh_platform()
        self.a = build_actors()

    def _fully_approved(self, *, proposal_quantity: int = 1000,
                        **license_overrides) -> str:
        pid = make_proposal(self.p, self.a["designer"],
                            quantity=proposal_quantity)
        kwargs = standard_license_kwargs()
        kwargs.update(license_overrides)
        self.p.grant_license(self.a["rights"], **kwargs)
        approve_all(self.p, pid, self.a)
        contract = self.p.record_contract(
            self.a["admin"], "partner-A", "丝巾采购合同",
            body="甲方委托乙方……（合同正文）", body_ref="sealed://contracts/c1")
        self._contract_id = contract.contract_id
        return pid

    def _order(self, pid: str, **kw) -> str:
        defaults = dict(
            actor=self.a["ops"], proposal_id=pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=100,
            on_date="2026-09-20", contract_id=self._contract_id)
        defaults.update(kw)
        return self.p.create_production_order(**defaults)

    def test_acquisition_does_not_grant_reproduction_rights(self) -> None:
        # 作品已入藏（acquired=True），但没有任何许可：权利确认必须失败
        pid = make_proposal(self.p, self.a["designer"])
        with self.assertRaises(LicenseError):
            self.p.approve(self.a[Gate.RIGHTS], pid, Gate.RIGHTS)

    def test_unknown_element_cannot_be_cited(self) -> None:
        with self.assertRaises(ValidationError):
            self.p.create_proposal(
                self.a["designer"],
                partner_id="partner-A",
                exhibition_id="exhibition-modern-2026",
                title="凭空元素产品",
                element_uses=[{"artwork_id": "artwork-river-17",
                               "element_id": "el-not-exists"}],
                material="纸", regions=["CN"],
                channels=[Channel.MUSEUM_SHOP], quantity=10, unit_price=10,
                public_benefit=False, benefit_statement="",
                design_file_ref="sealed://x", design_file_sha256="sha256:x")

    def test_region_out_of_scope_blocks(self) -> None:
        pid = self._fully_approved()
        with self.assertRaises(LicenseError):
            self._order(pid, region="US")

    def test_channel_out_of_scope_blocks(self) -> None:
        pid = self._fully_approved()
        with self.assertRaises(LicenseError):
            self._order(pid, channel=Channel.OFFLINE_EVENT)

    def test_date_out_of_term_blocks(self) -> None:
        # 许可覆盖 10-01 开幕，但 10-20 才投产时已到期
        pid = self._fully_approved(ends_on="2026-10-15")
        with self.assertRaises(LicenseError):
            self._order(pid, on_date="2026-10-20")

    def test_quantity_cap_accumulates_across_orders(self) -> None:
        pid = self._fully_approved(proposal_quantity=150, quantity=150)
        self._order(pid, quantity=100)
        # 100 已承诺，再下 100 超出 150 的上限
        with self.assertRaises(LicenseError):
            self._order(pid, quantity=100)
        # 余量内放行
        self._order(pid, quantity=50)

    def test_commercial_adaptation_right_required(self) -> None:
        pid = make_proposal(self.p, self.a["designer"])
        # 只有复制权，没有商业改编权
        self.p.grant_license(self.a["rights"], **standard_license_kwargs(
            rights=[Right.REPRODUCE]))
        with self.assertRaises(LicenseError):
            self.p.approve(self.a[Gate.RIGHTS], pid, Gate.RIGHTS)

    def test_promo_only_channel_does_not_need_commercial_right(self) -> None:
        pid = make_proposal(
            self.p, self.a["designer"],
            channels=[Channel.EXHIBITION_PROMO], quantity=50)
        self.p.grant_license(self.a["rights"], **standard_license_kwargs(
            rights=[Right.REPRODUCE, Right.PROMOTION],
            channels=[Channel.EXHIBITION_PROMO], quantity=50))
        self.p.approve(self.a[Gate.ACADEMIC], pid, Gate.ACADEMIC)
        self.p.approve(self.a[Gate.RIGHTS], pid, Gate.RIGHTS)  # 不抛异常
        self.p.approve(self.a[Gate.PUBLIC_BENEFIT], pid, Gate.PUBLIC_BENEFIT)
        self.p.approve(self.a[Gate.BUSINESS], pid, Gate.BUSINESS)


if __name__ == "__main__":
    unittest.main()
