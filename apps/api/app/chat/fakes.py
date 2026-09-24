"""``AI_MODE=fake`` stand-ins; the fake model scripts its next step from the replayed conversation so the real
loop, approvals and stream encoder all run."""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.chat.model import ContentFilterError, FunctionCallDone, ModelEvent, ModelRequest, ResponseDone, TextDelta
from app.chat.router import RouterDecision, router_input
from app.models.api import ChatContext
from app.models.plan import Region
from app.services.language_pii import PiiCheckResult, PiiHit
from app.services.search import SearchHit, SearchResult

FAKE_MODEL = "fake-gpt-5-mini"
FAKE_NANO = "fake-gpt-5-nano"


_OFF_TOPIC = re.compile(
    r"\b(poem|joke|weather|recipe|bitcoin|stock|football|movie|song|capital of|ignore (all|previous|your)|"
    r"system prompt)\b",
    re.IGNORECASE,
)
_KNOWLEDGE = re.compile(
    r"\b(tax|medical expenses?|metc|cra|medicare|levy|rebate|lifetime health cover|waiting periods? (for|in) "
    r"(private|hospital)|ohip|provincial plan|private health insurance rules?)\b",
    re.IGNORECASE,
)
_PLAN = re.compile(
    r"\b(my|i|me|left|remaining|used|covered|cover|claim|claims|draft|receipt|estimate|limit|plan|member|"
    r"spouse|child)\b",
    re.IGNORECASE,
)


class FakeRouter:
    async def classify(self, text: str, history: list[dict[str, Any]], context: ChatContext) -> RouterDecision:
        router_input(text, history, context)  # same input shaping as the live router
        if _OFF_TOPIC.search(text):
            route = "off_topic"
        elif _KNOWLEDGE.search(text) and not re.search(r"\b(my|covered|claim|left|remaining)\b", text, re.IGNORECASE):
            route = "knowledge"
        elif _PLAN.search(text) or _KNOWLEDGE.search(text):
            route = "plan_claims"
        else:
            route = "plan_claims"
        return RouterDecision(route=route, model=FAKE_NANO, input_tokens=0, output_tokens=0)  # type: ignore[arg-type]


_CRA_LICENSE = "Crown copyright; non-commercial reproduction permitted"
_PHI_LICENSE = "CC BY 3.0 AU"
_SAMPLE = "Sample excerpt for local testing"

FAKE_KNOWLEDGE: tuple[SearchHit, ...] = (
    SearchHit(
        id="ca-cra-medical-expenses",
        title="Lines 33099 and 33199 – Eligible medical expenses you can claim on your tax return",
        url=(
            "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/"
            "tax-return/completing-a-tax-return/deductions-credits-expenses/"
            "lines-33099-33199-eligible-medical-expenses-you-claim-on-your-tax-return.html"
        ),
        region="CA",
        publisher="Canada Revenue Agency",
        license=_CRA_LICENSE,
        attribution="Adapted from Canada Revenue Agency. Not an official version.",
        section=_SAMPLE,
        content=(
            "You can claim eligible medical expenses paid in any 12-month period ending in the tax year that you "
            "did not claim in the previous year. You cannot claim the part of an expense that was reimbursed, for "
            "example by a private health services plan; only the unreimbursed portion may qualify."
        ),
    ),
    SearchHit(
        id="ca-cra-authorized-practitioners",
        title="Authorized medical practitioners for the purposes of the medical expense tax credit",
        url=(
            "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/"
            "tax-return/completing-a-tax-return/deductions-credits-expenses/"
            "lines-33099-33199-eligible-medical-expenses-you-claim-on-your-tax-return/"
            "authorized-medical-practitioners-purposes-medical-expense-tax-credit.html"
        ),
        region="CA",
        publisher="Canada Revenue Agency",
        license=_CRA_LICENSE,
        attribution="Adapted from Canada Revenue Agency. Not an official version.",
        section=_SAMPLE,
        content=(
            "Whether fees for a practitioner, such as a massage therapist, count as medical expenses depends on "
            "the province or territory where the service is provided. Check the table of authorized medical "
            "practitioners for that province or territory before you claim."
        ),
    ),
    SearchHit(
        id="au-phi-waiting-periods",
        title="Waiting periods",
        url="https://www.privatehealth.gov.au/health_insurance/howitworks/waiting_periods.htm",
        region="AU",
        publisher="privatehealth.gov.au (Commonwealth Ombudsman)",
        license=_PHI_LICENSE,
        attribution="© Commonwealth of Australia, privatehealth.gov.au, licensed under CC BY 3.0 AU.",
        section=_SAMPLE,
        content=(
            "Waiting periods apply when you first take out hospital cover or upgrade it. The maximum waiting "
            "periods are 12 months for pre-existing conditions and for pregnancy and birth, and 2 months for "
            "other treatment. Insurers set their own waiting periods for extras cover."
        ),
    ),
    SearchHit(
        id="au-phi-extras",
        title="General treatment (extras) cover",
        url="https://www.privatehealth.gov.au/health_insurance/whatiscovered/generaltreatment.htm",
        region="AU",
        publisher="privatehealth.gov.au (Commonwealth Ombudsman)",
        license=_PHI_LICENSE,
        attribution="© Commonwealth of Australia, privatehealth.gov.au, licensed under CC BY 3.0 AU.",
        section=_SAMPLE,
        content=(
            "Extras cover pays benefits towards services that Medicare does not cover, such as dental, optical, "
            "physiotherapy, chiropractic and remedial massage. Insurers set annual limits and benefit amounts, so "
            "you usually pay a gap."
        ),
    ),
    SearchHit(
        id="au-phi-medicare-levy-surcharge",
        title="Medicare levy surcharge",
        url="https://www.privatehealth.gov.au/health_insurance/surcharges_incentives/medicare_levy.htm",
        region="AU",
        publisher="privatehealth.gov.au (Commonwealth Ombudsman)",
        license=_PHI_LICENSE,
        attribution="© Commonwealth of Australia, privatehealth.gov.au, licensed under CC BY 3.0 AU.",
        section=_SAMPLE,
        content=(
            "The Medicare levy surcharge applies to taxpayers above an income threshold who do not hold an "
            "appropriate level of private hospital cover. Extras cover on its own does not exempt you."
        ),
    ),
)

_WORD = re.compile(r"[a-z]{3,}")
_STOP = {"the", "and", "for", "what", "does", "can", "how", "are", "with", "that", "this", "your", "you", "is"}


async def fake_search(query: str, region: Region, top_k: int) -> SearchResult:
    terms = {w for w in _WORD.findall(query.lower()) if w not in _STOP}
    scored: list[tuple[float, SearchHit]] = []
    for hit in FAKE_KNOWLEDGE:
        if hit.region != region:
            continue
        haystack = f"{hit.title} {hit.content}".lower()
        score = float(sum(1 for t in terms if t in haystack))
        scored.append((score, hit))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    hits = [
        SearchHit(**{**hit.__dict__, "score": score, "reranker_score": min(4.0, score)})
        for score, hit in scored[: max(1, min(top_k, 3))]
    ]
    return SearchResult(hits=hits, latency_ms=0, embedding_tokens=0)


_DOB = re.compile(
    r"\b(date of birth|dob|born on|birthday is)\b\W{0,3}\w{0,9}\W{0,3}\d{1,4}[-/ ,.]+\w{1,9}[-/ ,.]+\d{1,4}",
    re.IGNORECASE,
)
_NINE_DIGITS = re.compile(r"(?<![\d$.])\d{3}[- ]?\d{3}[- ]?\d{3}(?![\d.])")
# Health card numbers: Ontario-style 4-3-3 (+ version code) or Medicare-style 4-5-1.
_TEN_DIGITS = re.compile(
    r"(?<![\d$.])(?:\d{4}[- ]?\d{3}[- ]?\d{3}(?:[- ]?[A-Za-z]{2})?|\d{4}[- ]?\d{5}[- ]?\d)(?![\d.])"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[ -])?\(?\d{2,3}\)?[ -]\d{3,4}[ -]\d{3,4}(?!\d)")


class FakePiiChecker:
    async def check(self, text: str, region: Region) -> PiiCheckResult:
        result = PiiCheckResult(records=1)
        consumed: list[tuple[int, int]] = []

        def add(match: re.Match[str], category: str, tier: str) -> None:
            span = match.span()
            if any(s < span[1] and span[0] < e for s, e in consumed):
                return
            consumed.append(span)
            hit = PiiHit(category, tier, 0.95, 0, span[0], span[1] - span[0])  # type: ignore[arg-type]
            (result.hard if tier == "hard" else result.advisory).append(hit)

        for m in _DOB.finditer(text):
            add(m, "DateOfBirth", "hard")
        for m in _TEN_DIGITS.finditer(text):
            add(m, "CAHealthServiceNumber" if region == "CA" else "AUMedicalAccountNumber", "hard")
        for m in _NINE_DIGITS.finditer(text):
            add(m, "CASocialInsuranceNumber" if region == "CA" else "AUTaxFileNumber", "hard")
        for m in _EMAIL.finditer(text):
            add(m, "Email", "advisory")
        for m in _PHONE.finditer(text):
            add(m, "PhoneNumber", "advisory")
        return result


_BENEFIT_WORDS = (
    "massage",
    "physio",
    "chiro",
    "naturopath",
    "psycholog",
    "mental health",
    "eye exam",
    "glasses",
    "contact lenses",
    "optical",
    "orthotics",
    "dental",
    "dentist",
    "drugs",
    "prescription",
    "hospital",
    "ambulance",
)
_AMOUNT = re.compile(r"\$\s?(\d{1,5}(?:[.,]\d{2})?)")


@dataclass
class _Conversation:
    context: dict[str, Any]
    user_text: str
    outputs: dict[str, Any]
    call_count: int


def _conversation(items: list[dict[str, Any]]) -> _Conversation:
    context: dict[str, Any] = {}
    last_user = -1
    for i, item in enumerate(items):
        if item.get("type") == "message" and item.get("role") == "developer":
            content = str(item.get("content", ""))
            brace = content.find("{")
            if brace >= 0:
                try:
                    context = json.loads(content[brace:])
                except ValueError:
                    context = {}
        if item.get("type") == "message" and item.get("role") == "user":
            last_user = i
    user_text = str(items[last_user].get("content", "")) if last_user >= 0 else ""
    names: dict[str, str] = {}
    outputs: dict[str, Any] = {}
    for item in items[last_user + 1 :]:
        if item.get("type") == "function_call":
            names[item["call_id"]] = item["name"]
        elif item.get("type") == "function_call_output":
            raw = item.get("output")
            try:
                value: Any = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                value = raw
            outputs[names.get(item["call_id"], "?")] = value
    call_count = sum(1 for item in items if item.get("type") == "function_call")
    return _Conversation(context, user_text, outputs, call_count)


def _money(cents: Any) -> str:
    return f"${int(cents) / 100:,.2f}" if isinstance(cents, int) else "an unknown amount"


def _keyword(text: str) -> str:
    lowered = text.lower()
    return next((w for w in _BENEFIT_WORDS if w in lowered), text.strip()[:60] or "benefits")


def _first_benefit(outputs: dict[str, Any]) -> dict[str, Any] | None:
    found = outputs.get("find_benefits")
    benefits = found.get("benefits") if isinstance(found, dict) else None
    return benefits[0] if isinstance(benefits, list) and benefits and isinstance(benefits[0], dict) else None


def _documents(output: Any) -> list[dict[str, str]]:
    text = output if isinstance(output, str) else ""
    docs = []
    for attrs, body in re.findall(r"<document ([^>]*)>\n?(.*?)\n?</document>", text, re.DOTALL):
        values = {k: html.unescape(v) for k, v in re.findall(r'(\w+)="([^"]*)"', attrs)}
        docs.append({**values, "content": html.unescape(body)})
    return docs


class FakeModel:
    def __init__(self, *, content_filter: bool = False) -> None:
        self._content_filter = content_filter

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self._content_filter:
            raise ContentFilterError("fake content filter")
        convo = _conversation(request.input)
        if request.agent == "knowledge":
            events = self._knowledge(convo, request)
        else:
            events = self._plan_claims(convo, request)
        for event in events:
            yield event

    def _call(self, convo: _Conversation, prefix: str, name: str, args: dict[str, Any]) -> list[ModelEvent]:
        call_id = f"call_{prefix}_{convo.call_count + 1}"
        arguments = json.dumps(args, separators=(",", ":"))
        return [
            FunctionCallDone(item_id=f"fc_{call_id}", call_id=call_id, name=name, arguments=arguments),
            ResponseDone(model=FAKE_MODEL),
        ]

    def _say(self, convo: _Conversation, text: str) -> list[ModelEvent]:
        item_id = f"msg_{convo.call_count}_{len(convo.outputs)}"
        words = text.split(" ")
        third = max(1, len(words) // 3)
        chunks = [" ".join(words[i : i + third]) for i in range(0, len(words), third)]
        deltas = [c if i == 0 else " " + c for i, c in enumerate(chunks)]
        return [*(TextDelta(item_id=item_id, delta=d) for d in deltas), ResponseDone(model=FAKE_MODEL)]

    def _not_found(self, convo: _Conversation, keyword: str) -> list[ModelEvent]:
        found = convo.outputs.get("find_benefits")
        reason = found.get("error") if isinstance(found, dict) else None
        detail = f" The lookup failed: {reason}" if isinstance(reason, str) else ""
        return self._say(convo, f"I couldn't find {keyword} in your plan.{detail}")

    def _knowledge(self, convo: _Conversation, request: ModelRequest) -> list[ModelEvent]:
        region = convo.context.get("region", "CA")
        if request.force_final or "search_public_knowledge" in convo.outputs:
            docs = _documents(convo.outputs.get("search_public_knowledge"))
            if not docs:
                return self._say(convo, "I couldn't find public reference material that answers that.")
            lead = docs[0]
            sentence = lead["content"].split(". ")[0].rstrip(".") + "."
            text = (
                f'{sentence} ({lead.get("publisher", "")}, "{lead.get("title", "")}")\n\n'
                "This is general information, not tax or insurance advice."
            )
            return self._say(convo, text)
        return self._call(
            convo, "kn", "search_public_knowledge", {"query": convo.user_text, "region": region, "top_k": 5}
        )

    def _plan_claims(self, convo: _Conversation, request: ModelRequest) -> list[ModelEvent]:
        text = convo.user_text.lower()
        outputs = convo.outputs
        aliases = convo.context.get("memberAliases") or ["[MEMBER_A]"]
        region = convo.context.get("region", "CA")
        keyword = _keyword(text)
        benefit = _first_benefit(outputs)
        not_found = self._not_found(convo, keyword) if "find_benefits" in outputs and benefit is None else None
        if request.force_final:
            return self._say(convo, "Here is what I found so far.")

        if "claim" in text and re.search(r"\b(draft|create|make|start|add|file)\b", text):
            if "draft_claim" in outputs:
                out = outputs["draft_claim"]
                if isinstance(out, dict) and out.get("declined"):
                    return self._say(convo, "Okay, I won't create that claim. Tell me what you'd like to change.")
                if isinstance(out, dict) and out.get("error"):
                    return self._say(convo, f"I couldn't create the draft claim: {out.get('error')}.")
                total = out.get("totalChargedCents") if isinstance(out, dict) else None
                return self._say(
                    convo,
                    f"I added a draft claim for {_money(total)} to your claims. It stays a draft until you submit it "
                    "to your insurer.",
                )
            if not_found:
                return not_found
            if benefit is None:
                return self._call(convo, "pc", "find_benefits", {"query": keyword})
            amount = _AMOUNT.search(convo.user_text)
            cents = round(float(amount.group(1).replace(",", "")) * 100) if amount else 10000
            return self._call(
                convo,
                "pc",
                "draft_claim",
                {
                    "member_alias": aliases[0],
                    "provider": None,
                    "lines": [
                        {
                            "benefit_id": benefit.get("benefitId"),
                            "service_date": convo.context.get("today"),
                            "charged_cents": cents,
                            "quantity": None,
                            "item_code": None,
                            "description": None,
                            "other_plan_paid_cents": None,
                        }
                    ],
                },
            )

        if _KNOWLEDGE.search(text):
            if "ask_knowledge_agent" not in outputs:
                return self._call(convo, "pc", "ask_knowledge_agent", {"question": convo.user_text, "region": region})
            if "find_benefits" not in outputs:
                return self._call(convo, "pc", "find_benefits", {"query": keyword})
            knowledge = outputs["ask_knowledge_agent"]
            answer = knowledge.get("answer", "") if isinstance(knowledge, dict) else ""
            if benefit is None:
                coverage = f"I couldn't find {keyword} in your plan."
            else:
                page = (benefit.get("source") or {}).get("page")
                cite = f" (booklet p. {page})" if page else ""
                coverage = f"{benefit.get('name')} is covered: {benefit.get('coverage')}{cite}."
            return self._say(convo, f"{coverage}\n\nOn the tax side: {answer}")

        if re.search(r"\b(left|remaining|how much|used)\b", text):
            if not_found:
                return not_found
            if benefit is None:
                return self._call(convo, "pc", "find_benefits", {"query": keyword})
            if "get_usage" not in outputs:
                return self._call(
                    convo, "pc", "get_usage", {"benefit_id": benefit.get("benefitId"), "member_alias": aliases[0]}
                )
            usage = outputs["get_usage"]
            rows = usage.get("usage") if isinstance(usage, dict) else None
            row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
            if row is None:
                return self._say(convo, f"I couldn't work out the usage for {benefit.get('name')}.")
            remaining = row.get("remainingCents")
            if remaining is None:
                return self._say(convo, f"{benefit.get('name')} has no dollar maximum for {row.get('memberAlias')}.")
            return self._say(
                convo,
                f"{row.get('memberAlias')} has {_money(remaining)} left for {benefit.get('name')} this period "
                f"({_money(row.get('usedCents'))} used so far).",
            )

        if not_found:
            return not_found
        if benefit is None and re.search(r"\b(cover|covered|coverage)\b", text):
            return self._call(convo, "pc", "find_benefits", {"query": keyword})
        if benefit is not None:
            page = (benefit.get("source") or {}).get("page")
            cite = f" (booklet p. {page})" if page else ""
            return self._say(convo, f"{benefit.get('name')}: {benefit.get('coverage')}{cite}.")

        if "get_plan_overview" not in outputs:
            return self._call(convo, "pc", "get_plan_overview", {})
        overview = outputs["get_plan_overview"]
        name = overview.get("planName", "your plan") if isinstance(overview, dict) else "your plan"
        return self._say(
            convo,
            f"You're looking at {name}. Ask me what's covered, what you have left, or to draft a claim.",
        )
