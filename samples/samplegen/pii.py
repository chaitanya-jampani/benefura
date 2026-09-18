"""Invented PII. Checksum identifiers are valid so detectors fire; SINs, phones and emails use reserved fictional ranges."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MARKER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class PiiValue:
    value: str
    kind: str
    token: str | None  # None when the value is only blacked out


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def medicare_ok(number: str) -> bool:
    """AU Medicare: 10 digits; digit 9 = weighted sum of digits 1-8 (1,3,7,9,...) mod 10; digit 1 in 2-6."""
    d = [int(c) for c in number if c.isdigit()]
    if len(d) != 10 or d[0] not in (2, 3, 4, 5, 6):
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    return sum(a * b for a, b in zip(d[:8], weights, strict=True)) % 10 == d[8]


def tfn_ok(number: str) -> bool:
    d = [int(c) for c in number if c.isdigit()]
    weights = {8: [10, 7, 8, 4, 6, 3, 5, 1], 9: [1, 4, 3, 7, 5, 8, 6, 9, 10]}.get(len(d))
    return bool(weights) and sum(a * b for a, b in zip(d, weights, strict=True)) % 11 == 0


CA_PII: dict[str, PiiValue] = {
    "ca_name_a": PiiValue("Rowan Lindqvist", "person_name", "[MEMBER_A]"),
    "ca_name_b": PiiValue("Amara Lindqvist", "person_name", "[MEMBER_B]"),
    "ca_name_c": PiiValue("Juniper Lindqvist", "person_name", "[MEMBER_C]"),
    "ca_dob_a": PiiValue("1986-04-19", "date_of_birth", None),
    "ca_dob_b": PiiValue("1985-11-02", "date_of_birth", None),
    "ca_dob_c": PiiValue("2016-08-23", "date_of_birth", None),
    "ca_employer": PiiValue("Harbourview Logistics Inc.", "employer_name", "[EMPLOYER_A]"),
    "ca_policy": PiiValue("G-40718-2", "group_policy_number", "[POLICY_1]"),
    "ca_cert": PiiValue("NW-00731946", "certificate_number", "[CERT_1]"),
    "ca_sin": PiiValue("046 832 150", "sin", None),
    "ca_ohip": PiiValue("5824-613-904-KT", "health_card_number", None),
    "ca_address": PiiValue("88 Larkspur Lane, Toronto ON M4K 2P7", "address", "[ADDRESS_A]"),
    "ca_phone": PiiValue("416-555-0147", "phone", "[PHONE_A]"),
    "ca_email": PiiValue("rowan.lindqvist@example.net", "email", "[EMAIL_A]"),
}

AU_PII: dict[str, PiiValue] = {
    "au_name_a": PiiValue("Callum Ashgrove", "person_name", "[MEMBER_A]"),
    "au_name_b": PiiValue("Tamsin Okonkwo", "person_name", "[MEMBER_B]"),
    "au_dob_a": PiiValue("07/02/1979", "date_of_birth", None),
    "au_dob_b": PiiValue("23/09/1981", "date_of_birth", None),
    "au_member_no": PiiValue("WHF 6031 8274", "membership_number", "[POLICY_1]"),
    "au_medicare": PiiValue("2953 71603 1", "medicare_number", None),
    "au_address": PiiValue("14 Banksia Rise, Kallista Downs VIC 3791", "address", "[ADDRESS_A]"),
    "au_phone": PiiValue("0491 570 156", "phone", "[PHONE_A]"),
    "au_email": PiiValue("callum.ashgrove@example.org", "email", "[EMAIL_A]"),
    "au_bsb": PiiValue("733-218", "bsb", None),
    "au_account": PiiValue("2047 91836", "bank_account_number", None),
}

ALL_PII: dict[str, PiiValue] = {**CA_PII, **AU_PII}


def _self_check() -> None:
    assert luhn_ok(CA_PII["ca_sin"].value) and CA_PII["ca_sin"].value.startswith("046")
    assert luhn_ok(CA_PII["ca_ohip"].value[:13])  # version code excluded from Luhn
    assert medicare_ok(AU_PII["au_medicare"].value)
    # These must not pass checksums, to avoid detector false positives.
    assert not luhn_ok(CA_PII["ca_phone"].value)
    assert not tfn_ok(AU_PII["au_member_no"].value)
    assert not tfn_ok(AU_PII["au_account"].value)


_self_check()


@dataclass
class PiiTracker:
    registry: dict[str, PiiValue]
    used: dict[tuple[str, int], None] = field(default_factory=dict)  # insertion-ordered set

    def raw(self, text: str, page: int) -> str:
        def sub(m: re.Match[str]) -> str:
            key = m.group(1)
            self.used[(key, page)] = None
            return self.registry[key].value

        return MARKER.sub(sub, text)

    def entries(self) -> list[dict[str, object]]:
        out = []
        for key, page in sorted(self.used, key=lambda kp: (kp[1], list(self.registry).index(kp[0]))):
            v = self.registry[key]
            out.append({"value": v.value, "kind": v.kind, "page": page, "aliasToken": v.token})
        return out


def redacted(text: str, registry: dict[str, PiiValue]) -> str:
    """Alias tokens in place, blacked-out values dropped."""

    def sub(m: re.Match[str]) -> str:
        v = registry[m.group(1)]
        return v.token or ""

    out = MARKER.sub(sub, text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.;:)])", r"\1", out)
    return out.strip()
