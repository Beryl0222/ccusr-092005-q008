"""档案层：作品与可授权元素、权利人、合作方、展览、合同。

设计要点：

- 作品的馆藏入藏记录与"复制/商业改编权"是两回事：
  入藏只代表持有原件，``acquired`` 不产生任何商业授权。
- 每件作品登记可被设计取用的元素（Element），提案只能引用
  已登记元素，保证"使用哪件作品的哪些元素"可逐元素回溯。
- 合同正文不进普通字段，只存哈希；正文留在受控存储中，
  普通运营人员拿到的视图里永远只有哈希与元数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .hashing import canonical_hash, text_hash


@dataclass(frozen=True)
class Element:
    element_id: str
    artwork_id: str
    name: str
    kind: str            # image(完整图案) / motif(局部纹样) / color_palette / title_text ...
    description: str = ""

    def ref(self) -> dict[str, Any]:
        """元素取用引用，进入提案载荷并参与哈希。"""
        return {
            "artwork_id": self.artwork_id,
            "element_id": self.element_id,
            "name": self.name,
            "kind": self.kind,
        }


@dataclass
class Artwork:
    artwork_id: str
    title: str
    rights_holder_ids: list[str]
    elements: dict[str, Element] = field(default_factory=dict)
    # 入藏（取得原件）与授权（复制/商业改编）严格分离
    acquired: bool = True
    commercial_clearance: str = "pending"  # pending / cleared（逐许可才真正可用）

    def add_element(self, element: Element) -> None:
        if element.artwork_id != self.artwork_id:
            raise ValueError("元素必须归属到所在作品")
        self.elements[element.element_id] = element


@dataclass(frozen=True)
class RightsHolder:
    holder_id: str
    name: str
    contact: str = ""


@dataclass(frozen=True)
class Partner:
    partner_id: str
    name: str
    # 该合作方参与的环节；信息隔离只放行其参与环节的数据
    stages: tuple[str, ...]
    contact: str = ""


@dataclass(frozen=True)
class Exhibition:
    exhibition_id: str
    name: str
    opens_on: str
    closes_on: str


@dataclass(frozen=True)
class Contract:
    """合同只以哈希 + 元数据形式进入协作系统。

    ``body_ref`` 指向受控存储，正文不向普通运营人员开放；
    系统固定的是正文哈希，任何正文替换都会在绑定时失配。
    """

    contract_id: str
    partner_id: str
    title: str
    body_sha256: str
    body_ref: str
    version: int = 1

    @staticmethod
    def hash_body(body: str) -> str:
        return text_hash(body)


@dataclass(frozen=True)
class Message:
    """群聊追授权被替换为结构化沟通记录。"""

    message_id: str
    proposal_id: str
    author_actor_id: str
    audience: tuple[str, ...]   # 可见范围：角色或合作方 id
    content: str
    timestamp: str
    entry_seq: int

    def digest_payload(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "proposal_id": self.proposal_id,
            "author": self.author_actor_id,
            "content_sha256": canonical_hash(self.content),
        }
