"""Cast / subset selection — [CXC-FPSVC-CAST-SELECTION].

The service selects a device's cast from castings: choosing a module and casting
its players yields the working set ([CXC-FPID-WORKING-SET]). The WHOLE cast is
enrolled on every device; differentiation is in RBAC, not in who is enrolled
([CXC-FPID-ENROLL-EVERYWHERE]) — so the subset pushed to each device in a
module's scope is IDENTICAL.

A cast member is a logical triple (person, character, role). No slot, no
template bytes here — this layer decides WHO, the push layer assembles the
ordered entries with their templates.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import SupersetStore


@dataclass(frozen=True)
class CastMember:
    person: str
    character: str
    role: str


def select_cast(store: SupersetStore, module: str) -> list[CastMember]:
    """The module's cast, in stable casting order. Each character resolves to
    its global role ([CXC-FPID-ENTITY-ROLE] — roles are global, a module scopes
    availability not permission, [CXC-FPID-ENTITY-MODULE])."""
    members: list[CastMember] = []
    for casting in store.castings_for(module):
        role = store.role_for_character(casting.character)
        members.append(
            CastMember(person=casting.person, character=casting.character, role=role)
        )
    return members
