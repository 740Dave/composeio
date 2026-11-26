"""The superset authority — [CXC-FPSVC-SUPERSET-AUTHORITY].

fingerprint_service holds EVERY enrolled template for the whole population,
each keyed by a LOGICAL identity ``(person, ordinal)`` and NEVER by a physical
slot ([CXC-FPSVC-LOGICAL-IDENTITY-ONLY]). A device's cast is a subset it
receives by push; the superset is where re-enrolments land.

⛔ The greppable invariant: no field in this module is a physical slot index.
The store works in ``(person, ordinal)`` end to end. A slot is one sensor's
storage model and the device owns it ([CXC-FPID-SLOT-ALLOCATION-OWNER]).

This module also owns person + casting + RBAC CRUD ([CXC-FPSVC-PERSON-CRUD]) and
the TOTAL-DELETE retention rule ([DD-FPSVC-DELETE-IS-TOTAL]): at six months of
inactivity the template bytes AND the person record are actually removed, with
NO tombstone. The inactivity clock is the person's last authentication attempt
at any device ([DD-FPSVC-INACTIVITY-CLOCK] lean; a never-played enrollee's clock
starts at enrollment).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .config import SIX_MONTHS_SECONDS

# A LOGICAL key. There is deliberately no Slot type in this module.
LogicalKey = tuple[str, int]  # (person_id, ordinal)


@dataclass
class TemplateRecord:
    """One enrolled finger, keyed by (person, ordinal). The template is opaque
    bytes the service never parses ([CXC-FPID-SEAM]). There is NO slot field and
    NO hash field ([CXC-FPBUS-WHAT-NEVER-CROSSES])."""

    person: str
    ordinal: int
    template: bytes
    enrolled_at: float


@dataclass
class Person:
    """The real human; owns their templates ([CXC-FPID-ENTITY-PERSON]).
    ``last_auth_attempt_at`` is the retention clock basis — set to the creation
    time so a never-played enrollee still ages out ([DD-FPSVC-INACTIVITY-
    CLOCK])."""

    person_id: str
    display_name: str
    created_at: float
    last_auth_attempt_at: float


@dataclass
class Casting:
    """The module-scoped person-to-character binding ([CXC-FPID-ENTITY-CASTING]),
    session-scoped and mutable. A casting names NO slot and NO template — it
    binds logical identities only."""

    module: str
    person: str
    character: str


@dataclass
class SupersetStore:
    """In-memory superset authority. The reference realization of the store; a
    durable backend would preserve exactly these keys and this delete
    semantics."""

    clock: Callable[[], float] = time.time
    persons: dict[str, Person] = field(default_factory=dict)
    templates: dict[LogicalKey, TemplateRecord] = field(default_factory=dict)
    castings: list[Casting] = field(default_factory=list)
    # character -> role ([CXC-FPID-ENTITY-CHARACTER] links to a role).
    characters: dict[str, str] = field(default_factory=dict)
    # role -> [capability] — the small global RBAC map ([CXC-FPID-ENTITY-ROLE],
    # [DD-FPID-RBAC-EVAL-LOCUS]). This is what rides the push as a whole.
    role_capabilities: dict[str, list[str]] = field(default_factory=dict)

    # ---- person CRUD ([CXC-FPSVC-PERSON-CRUD]) ---------------------------

    def create_person(self, person_id: str, display_name: str = "") -> Person:
        if person_id in self.persons:
            raise KeyError(f"person already exists: {person_id}")
        now = self.clock()
        p = Person(
            person_id=person_id,
            display_name=display_name or person_id,
            created_at=now,
            # clock starts at enrollment/creation for a never-played enrollee.
            last_auth_attempt_at=now,
        )
        self.persons[person_id] = p
        return p

    def get_person(self, person_id: str) -> Person | None:
        return self.persons.get(person_id)

    def update_person(self, person_id: str, *, display_name: str) -> Person:
        p = self.persons[person_id]
        p.display_name = display_name
        return p

    def delete_person(self, person_id: str) -> None:
        """TOTAL delete of one person on demand — the same removal the retention
        sweep performs ([DD-FPSVC-DELETE-IS-TOTAL]): templates AND the record,
        no tombstone."""
        self._total_delete(person_id)

    # ---- casting + RBAC CRUD ---------------------------------------------

    def define_role(self, role: str, capabilities: list[str]) -> None:
        self.role_capabilities[role] = list(capabilities)

    def define_character(self, character: str, role: str) -> None:
        self.characters[character] = role

    def cast(self, module: str, person: str, character: str) -> Casting:
        """Bind (module, person, character). Recasting a character to another
        person is an ordinary mutable operation ([CXC-FPID-ENTITY-CASTING])."""
        if person not in self.persons:
            raise KeyError(f"unknown person: {person}")
        if character not in self.characters:
            raise KeyError(f"unknown character: {character}")
        # replace any existing binding of this character within the module
        self.castings = [
            c
            for c in self.castings
            if not (c.module == module and c.character == character)
        ]
        binding = Casting(module=module, person=person, character=character)
        self.castings.append(binding)
        return binding

    def castings_for(self, module: str) -> list[Casting]:
        """The module's castings in stable insertion order — the push's install
        order derives from this ([CXC-FPBUS-PUSH-SEQUENCE] assigns-slots-by-
        order)."""
        return [c for c in self.castings if c.module == module]

    def role_for_character(self, character: str) -> str:
        return self.characters[character]

    def capabilities_for_role(self, role: str) -> list[str]:
        return self.role_capabilities.get(role, [])

    # ---- templates, keyed by LOGICAL identity ----------------------------

    def upsert_template(self, person: str, ordinal: int, template: bytes) -> TemplateRecord:
        """Store a captured template keyed by (person, ordinal), NEVER by slot
        ([CXC-FPSVC-ENROLL-ORCHESTRATION]). A re-enrollment of the same ordinal
        replaces the bytes in place."""
        if person not in self.persons:
            raise KeyError(f"unknown person: {person}")
        rec = TemplateRecord(
            person=person,
            ordinal=ordinal,
            template=bytes(template),
            enrolled_at=self.clock(),
        )
        self.templates[(person, ordinal)] = rec
        return rec

    def templates_for(self, person: str) -> list[TemplateRecord]:
        """A person's templates, ordered by ordinal — never by any physical
        index ([CXC-FPID-DO-NOT-HARD-CODE-THE-BOUND]: a list, and the count
        'two' is nowhere encoded)."""
        return [
            self.templates[(p, o)]
            for (p, o) in sorted(self.templates.keys())
            if p == person
        ]

    def ordinals_for(self, person: str) -> list[int]:
        return [o for (p, o) in sorted(self.templates.keys()) if p == person]

    # ---- retention clock -------------------------------------------------

    def record_auth_attempt(self, person: str, at: float | None = None) -> None:
        """Advance the inactivity clock. Called from the auth_event subscription
        ([CXC-FPBUS-MSG-AUTH-EVENT]) for the resolved player. Unrecognized events
        carry no player, so they never touch this ([DD-FPSVC-INACTIVITY-
        CLOCK])."""
        p = self.persons.get(person)
        if p is not None:
            p.last_auth_attempt_at = self.clock() if at is None else at

    def sweep_retention(
        self, *, now: float | None = None, max_inactivity_s: int = SIX_MONTHS_SECONDS
    ) -> list[str]:
        """Totally delete every person inactive for >= ``max_inactivity_s``,
        returning the deleted person ids. TOTAL means the templates AND the
        person record are actually removed and NO tombstone remains
        ([DD-FPSVC-DELETE-IS-TOTAL])."""
        t = self.clock() if now is None else now
        stale = [
            pid
            for pid, p in self.persons.items()
            if (t - p.last_auth_attempt_at) >= max_inactivity_s
        ]
        for pid in stale:
            self._total_delete(pid)
        return stale

    def _total_delete(self, person_id: str) -> None:
        """Remove the bytes. No mark, no flag, no tombstone ([DD-FPSVC-DELETE-
        IS-TOTAL] constraint: "Biometric PII leaves no residue")."""
        self.persons.pop(person_id, None)
        for key in [k for k in self.templates if k[0] == person_id]:
            del self.templates[key]
        self.castings = [c for c in self.castings if c.person != person_id]
