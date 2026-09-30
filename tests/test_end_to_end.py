"""端到端：从 fixtures/seed.json 入库，走完创意→审批→打样→量产→撤回处置。"""
from __future__ import annotations

import unittest
from pathlib import Path

from museum_collab import (
    DesignContent,
    DispositionAction,
    ElementRef,
    Gate,
    LicenseScope,
    Purpose,
    RefMode,
)
from museum_collab.model import ReservationMode
from museum_collab.system import Actor, System, ReleaseBlocked
from project_data import load_seed


SEED = load_seed(Path(__file__).resolve().parents[1] / "fixtures" / "seed.json")


class EndToEndTest(unittest.TestCase):
    def test_seed_to_withdrawal_dispositions(self) -> None:
        sys = System()
        archivist = Actor("u-archivist", "business", name="资料管理员")
        sys.bootstrap_from_seed(archivist, SEED)
        catalog = sys.catalog()
        self.assertIn("artwork-river-17", catalog.artworks)
        self.assertIn("partner-a", catalog.partners)

        curator = Actor("u-c", "curator")
        rights = Actor("u-r", "rights")
        benefit = Actor("u-b", "benefit")
        business = Actor("u-biz", "business")
        producer = Actor("u-p", "producer")
        designer = Actor("pa-1", "partner", partner_id="partner-a")

        design = DesignContent(
            artwork_refs=(ElementRef(
                "artwork-river-17", ("main-figure",),
                RefMode.COMMERCIAL_ADAPTATION, note="丝巾图案"),),
            material="silk", channels=("museum-shop",), regions=("CN",),
            quantity=500, purpose=Purpose.COMMERCIAL,
        )
        v1 = sys.create_proposal(
            designer, proposal_id="e2e-scarf",
            exhibition_id="exhibition-modern-2026",
            partner_id="partner-a", design=design,
        )

        # 没有许可就打样：阻断（事故不会重演）
        with self.assertRaises(ReleaseBlocked):
            sys.start_sampling(designer, proposal_id="e2e-scarf", version_id=v1)

        sys.grant_license(
            rights, proposal_id="e2e-scarf", version_id=v1,
            holder_id="artist-chen",
            scope=LicenseScope(
                max_quantity=2000, regions=frozenset({"CN"}),
                channels=frozenset({"museum-shop"}),
                valid_from="2026-08-01", valid_until="2027-06-30",
                purpose=Purpose.COMMERCIAL),
            contract_ref="CT-E2E-001",
            contract_body={"title": "许可合同", "royalty": "8%", "signed": True},
        )
        sys.decide_gate(curator, proposal_id="e2e-scarf", version_id=v1,
                        gate=Gate.ACADEMIC, approved=True)
        sys.decide_gate(rights, proposal_id="e2e-scarf", version_id=v1,
                        gate=Gate.RIGHTS, approved=True)
        sys.decide_gate(benefit, proposal_id="e2e-scarf", version_id=v1,
                        gate=Gate.BENEFIT, approved=True)
        sys.decide_gate(business, proposal_id="e2e-scarf", version_id=v1,
                        gate=Gate.BUSINESS, approved=True)
        sys.reserve_elements(
            rights, proposal_id="e2e-scarf", version_id=v1,
            mode=ReservationMode.PARALLEL_SAMPLE, valid_until="2027-12-31",
        )
        sys.start_sampling(designer, proposal_id="e2e-scarf", version_id=v1)
        sys.release_production(
            business, proposal_id="e2e-scarf", version_id=v1,
            quantity=500, channel="museum-shop", region="CN", on_date="2026-09-01",
        )
        sys.register_instances(
            producer, proposal_id="e2e-scarf",
            instances=[
                {"instance_id": "unmade", "state": "not_produced", "quantity": 200},
                {"instance_id": "moving", "state": "in_transit", "quantity": 100},
                {"instance_id": "gone", "state": "sold", "quantity": 80},
                {"instance_id": "poster", "state": "promo_only", "quantity": 6},
            ],
        )
        dispositions = sys.rights_withdrawn(
            rights, proposal_id="e2e-scarf",
            holder_id="artist-chen", effective_on="2026-09-15",
        )
        actions = {d["state"]: d["action"] for d in dispositions}
        self.assertEqual(actions["not_produced"],
                         DispositionAction.CANCEL_BEFORE_PRODUCTION.value)
        self.assertEqual(actions["in_transit"],
                         DispositionAction.RECALL_OR_HOLD.value)
        self.assertEqual(actions["sold"],
                         DispositionAction.HONOR_SOLD_COPIES.value)
        self.assertEqual(actions["promo_only"],
                         DispositionAction.PROMO_HALT.value)

        sys.verify_integrity()


if __name__ == "__main__":
    unittest.main()
