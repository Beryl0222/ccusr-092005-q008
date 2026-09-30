"""领域错误类型。"""


class DomainError(Exception):
    """所有业务规则违反的基类。"""


class ValidationError(DomainError):
    """档案或提案字段不合法（例如元素无法回溯到作品）。"""


class AuthorizationError(DomainError):
    """当前角色无权执行该操作或查看该资源。"""


class ApprovalRequiredError(DomainError):
    """审批门未通过或已因变更失效，阻断后续环节。"""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


class LicenseError(DomainError):
    """请求的数量/地区/渠道/期限未被有效许可覆盖。"""


class ConflictError(DomainError):
    """并行流程发生独占冲突，例如两个合作方竞争同一艺术元素。"""


class StateError(DomainError):
    """实例或工单状态不允许该迁移（例如撤回后继续销售）。"""


class LedgerError(DomainError):
    """台账链校验失败，说明记录被篡改。"""
