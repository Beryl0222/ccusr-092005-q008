"""端到端叙事：复刻题面故事。

特展开幕两个月前，已打样的丝巾才发现入藏手续不含复制/商业改编权。
本测试走完整条线：系统建档 → 提案标明元素 → 补授权 → 四门审批
→ 生产 → 权利人撤回、逐实例分类处置 → 策展人凭上市产品全链路回溯。
"""
import unittest

from museum_collab.actors import Gate
from museum_collab.events import Action
from museum_collab.licensing import Channel
from museum_collab.production import InstanceState

from fixtures_support import (
    approve_all, fresh_platform, build_actors, make_proposal,
    standard_license_kwargs,
)


class EndToEndStoryTest(unittest.TestCase):
    def test_full_story(self) -> None:
        p = fresh_platform()
        a = build_actors()

        # 1) 丝巾已经打样，但此时系统里没有任何许可
        pid = make_proposal(p, a["pA"], quantity=200)
        p.start_sampling(a["pA"], pid)
        # 走权利确认：入藏记录不包含复制/商业改编权，被系统明确阻断
        with self.assertRaises(Exception):
            p.approve(a[Gate.RIGHTS], pid, Gate.RIGHTS)

        # 2) 群聊追授权被替换为结构化沟通 + 正式许可
        p.post_message(
            a["holder"], pid,
            "授权水波纹元素：复制+商业改编，200件，中国大陆，"
            "馆店与线上，至2027-06-30",
            audience=("partner-A",))
        license_id = p.grant_license(
            a["holder"], **standard_license_kwargs(quantity=200))

        # 3) 四个角色分别完成各自判断
        approve_all(p, pid, a)

        # 4) 合同正文只登记哈希，然后钉住版本生产
        contract = p.record_contract(
            a["admin"], "partner-A", "丝巾合同",
            body="合同正文……", body_ref="sealed://contracts/c1")
        order_id = p.create_production_order(
            a["ops"], proposal_id=pid, region="CN",
            channel=Channel.MUSEUM_SHOP, quantity=200,
            on_date="2026-09-01", contract_id=contract.contract_id)

        # 5) 分布到四种实例状态
        items = sorted(p.instances)
        p.transition_instance(a["ops"], items[50], InstanceState.IN_TRANSIT,
                              on_date="2026-09-20")
        p.transition_instance(a["ops"], items[99], InstanceState.IN_TRANSIT,
                              on_date="2026-09-20")
        p.transition_instance(a["ops"], items[99], InstanceState.SOLD,
                              on_date="2026-09-28")
        p.transition_instance(a["ops"], items[150],
                              InstanceState.EXHIBITION_PROMO,
                              on_date="2026-09-25")

        # 6) 权利人撤回：四种状态四种处置，没有一刀切
        dispositions = p.revoke_license(
            a["holder"], license_id, on_date="2026-10-02",
            reason="艺术家家属方提出异议")
        self.assertEqual(len(dispositions), 200)
        action_of = {}
        for item in (items[0], items[50], items[99], items[150]):
            d = p.dispositions_for_instance(item)[0]
            action_of[d.state_at_event] = d.action
        self.assertEqual(action_of[InstanceState.UNPRODUCED],
                         Action.CANCEL_PRODUCTION)
        self.assertEqual(action_of[InstanceState.IN_TRANSIT],
                         Action.RECALL_AND_HOLD)
        self.assertEqual(action_of[InstanceState.SOLD],
                         Action.POST_SALE_NOTICE)
        self.assertEqual(action_of[InstanceState.EXHIBITION_PROMO],
                         Action.TAKE_DOWN_PROMO)

        # 7) 策展人拿起一件已售丝巾，完整回查
        dossier = p.curator_dossier(a["curator"], items[99])
        self.assertEqual(dossier["artworks"][0]["artwork_id"],
                         "artwork-river-17")
        self.assertTrue(dossier["communication"])
        gates = {r["gate"]
                 for r in dossier["version_trace"][0]["approvals"]}
        self.assertEqual(gates, set(Gate.ALL))
        self.assertEqual(
            dossier["product"]["version_sha256_pinned_by_order"],
            dossier["version_trace"][0]["content_sha256"])
        self.assertTrue(any(b["status"] == "revoked"
                            for b in dossier["rights_boundary"]))
        self.assertTrue(dossier["dispositions"])

        # 8) 台账链完整，全程可审计
        p.ledger.verify()


if __name__ == "__main__":
    unittest.main()
