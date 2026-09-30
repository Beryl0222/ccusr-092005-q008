"""美术馆文创权利协作后端（参考实现）。

模块划分：

- ledger       只追加、哈希成链的台账，所有沟通、审批、处置落账且防篡改
- records      作品 / 权利人 / 合作方 / 展览 / 合同等档案
- proposals    设计提案、版本、元素取用与四类审批门
- licensing    许可（数量/地区/渠道/期限）与权利覆盖校验
- sampling     并行打样与同一艺术元素的竞争规则
- production   生产工单、实例状态机与权利快照
- events       权利人撤回 / 开幕改期的分类处置
- permissions  按角色与合作方环节做信息隔离
- dossier      策展人上市产品全链路回溯
"""

from .actors import Actor, Gate, Role
from .errors import (
    ApprovalRequiredError,
    AuthorizationError,
    ConflictError,
    DomainError,
    LicenseError,
    StateError,
    ValidationError,
)
from .platform import MuseumPlatform

__all__ = [
    "Actor",
    "Gate",
    "Role",
    "MuseumPlatform",
    "DomainError",
    "ValidationError",
    "AuthorizationError",
    "ApprovalRequiredError",
    "LicenseError",
    "ConflictError",
    "StateError",
]
