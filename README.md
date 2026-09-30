# 美术馆文创权利协作后端

公立美术馆短展期文创开发的协作后端参考实现：把"群聊追授权"替换为
**提案标明元素 → 四类角色分审 → 许可逐项表达 → 生产哈希钉住 → 事件逐实例处置 → 全链路可回溯**。

完整设计见 [DESIGN.md](DESIGN.md)（含变更—重审矩阵、事件×状态处置矩阵、权限矩阵）。

## 它解决什么问题

- 已打样产品才发现**馆藏入藏不含复制/商业改编权** —— 入藏与授权在模型里严格分离，无许可则权利门无法通过；
- 图案替换/展期变化让错误版本流入生产 —— 修订即新版本，旧版本立即失效；变更只重开受影响的审批门；
- 许可说不清数量/地区/渠道/期限 —— 许可四要素结构化，生产时逐项机器校验；
- 权利人撤回或开幕改期后一刀切 —— 按未生产/在途/已售/仅展览宣传四类实例分别生成处置，只追加不覆盖；
- 合作方信息越界、合同正文外流 —— 环节级信息隔离，合同仅存正文哈希，普通运营取不到正文；
- 两个合作方竞争同一艺术元素 —— 打样共享、独占阻断、选型后放行、撤回后释放；
- 上市产品无法溯源 —— 策展人凭任一件产品回溯原作、沟通、批准版本与当前权利边界。

## 目录

```
museum_collab/      纯标准库领域实现（无第三方依赖）
  ledger.py         只追加、哈希成链的台账（防篡改）
  actors.py         角色与四类审批门
  records.py        作品/元素/权利人/合作方/展览/合同(仅哈希)/沟通
  proposals.py      提案版本、变更影响分析、批准记录
  licensing.py      许可（数量/地区/渠道/期限）与覆盖校验
  sampling.py       并行打样与元素竞争锁
  production.py     生产工单三重哈希钉住、实例状态机
  events.py         撤回/改期 × 实例状态处置矩阵
  permissions.py    合作方环节隔离、合同正文管控
  dossier.py        策展人全链路回溯
  platform.py       门面服务：鉴权 → 业务规则 → 落账
tests/              43 条可执行规格（unittest）
fixtures/seed.json  档案样例
project_data.py     样例读取与结构校验
```

## 运行

```bash
python3 -m unittest discover -s tests
```

## 最小流程示例

```python
from museum_collab import MuseumPlatform, Actor, Gate, Role
from museum_collab.licensing import Channel, Right
# 建档（作品、元素、权利人、合作方、展览）→ create_proposal 标明取用元素
# → grant_license(数量/地区/渠道/期限) → 四个角色分别 approve
# → create_production_order（钉住版本哈希+权利快照+合同哈希）
# → revoke_license / reschedule_exhibition 逐实例生成处置
# → curator_dossier(任一件上市产品) 全链路回溯
```

完整叙事见 `tests/test_end_to_end.py`。
