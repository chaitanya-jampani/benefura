"""Wattle Health Fund: Silver Plus Hospital and Mid Extras cover summary (fictional, 12 pages, A4)."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from samplegen.doc import Bullets, Callout, CoverText, DocSpec, Gap, Grid, H, P, Page, Status, Theme, TwoCol
from samplegen.fixtures import lead, q, sentence
from samplegen.pii import AU_PII

DOC = "au-wattle"
INSURER = "Wattle Health Fund"
PLAN = "Silver Plus Hospital and Mid Extras"

THEME = Theme(
    primary=colors.HexColor("#1F4A39"),
    accent=colors.HexColor("#B7860B"),
    tint=colors.HexColor("#F7F1E0"),
    ink=colors.HexColor("#1F2A24"),
    muted=colors.HexColor("#5D6A62"),
    rule=colors.HexColor("#DCD5C1"),
    zebra=colors.HexColor("#FBF8EF"),
    pagesize=A4,
    margin_x=56,
    margin_top=82,
    margin_bottom=66,
    body_size=9.5,
    body_leading=13.6,
)


def Q(key: str) -> str:
    return q(DOC, key)


def _wattle(c: Canvas, cx: float, cy: float, scale: float = 1.0) -> None:
    c.saveState()
    c.setFillColor(colors.HexColor("#7FA58F"))
    for dx, dy, rot in [(-9, -13, 35), (10, -15, -30)]:
        c.saveState()
        c.translate(cx + dx * scale, cy + dy * scale)
        c.rotate(rot)
        c.ellipse(-4 * scale, -12 * scale, 4 * scale, 12 * scale, stroke=0, fill=1)
        c.restoreState()
    gold = [colors.HexColor("#F2C14E"), colors.HexColor("#E5A823"), colors.HexColor("#F7D57A")]
    for i, (dx, dy, r) in enumerate([(0, 6, 7), (-10, -1, 5.5), (10, 0, 6), (-4, -8, 4.5), (6, -9, 4.5), (0, 17, 4.5)]):
        c.setFillColor(gold[i % 3])
        c.circle(cx + dx * scale, cy + dy * scale, r * scale, stroke=0, fill=1)
    c.restoreState()


def cover(c: Canvas, t: Theme) -> list[CoverText]:
    w, h = A4
    white = colors.white
    gold = colors.HexColor("#F2C14E")
    band_bottom = 372
    c.setFillColor(t.primary)
    c.rect(0, band_bottom, w, h - band_bottom, stroke=0, fill=1)
    for i, col in enumerate(["#2B5E49", "#3C755A", "#D9A21B"]):
        c.setFillColor(colors.HexColor(col))
        p = c.beginPath()
        top = band_bottom + 64 - i * 24
        p.moveTo(0, band_bottom)
        p.lineTo(0, max(band_bottom, top - 20))
        p.curveTo(w * 0.3, top + 36, w * 0.62, top - 40, w, top + 18)
        p.lineTo(w, band_bottom)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    _wattle(c, 78, 758, 1.15)
    c.setFillColor(colors.HexColor("#2B5E49"))
    c.roundRect(56, 470, 176, 28, 14, stroke=0, fill=1)
    c.setFillColor(t.tint)
    c.rect(56, 118, w - 112, 82, stroke=0, fill=1)
    c.setFillColor(t.accent)
    c.rect(56, 118, 3, 82, stroke=0, fill=1)
    return [
        CoverText(104, 760, INSURER, "Helvetica-Bold", 18, white),
        CoverText(104, 745, "Private health insurance", "Helvetica", 9.5, gold, md=None),
        CoverText(56, 612, "Your cover summary", "Helvetica-Bold", 36, white, md="h1"),
        CoverText(56, 574, PLAN, "Helvetica", 17, gold),
        CoverText(56, 540, "Couple policy · Victoria", "Helvetica", 12, white),
        CoverText(72, 480, "Hospital tier: Silver Plus", "Helvetica-Bold", 10.5, white),
        CoverText(56, 318, "Prepared for {{au_name_a}} and {{au_name_b}}", "Helvetica-Bold", 14, t.primary),
        CoverText(56, 294, "Membership number {{au_member_no}}", "Helvetica", 11.5, t.ink),
        CoverText(56, 276, "Cover start date 1 July 2024", "Helvetica", 11.5, t.ink),
        CoverText(56, 258, "Issued 1 April 2026", "Helvetica", 10, t.muted),
        CoverText(72, 176, "What is in this summary", "Helvetica-Bold", 10.5, t.primary),
        CoverText(
            72,
            158,
            "Your hospital and extras benefits, waiting periods, how to claim and how to contact us. Read it",
            "Helvetica",
            9.5,
            t.ink,
            md_text=(
                "Your hospital and extras benefits, waiting periods, how to claim and how to contact us. Read it with "
                "the Private Health Information Statement for your policy."
            ),
        ),
        CoverText(
            72, 144, "with the Private Health Information Statement for your policy.", "Helvetica", 9.5, t.ink, md=None
        ),
        CoverText(56, 72, f"{INSURER} · Call 1300 975 707", "Helvetica-Bold", 9, t.primary),
        CoverText(w - 56, 72, "WHF-SPME · 04/2026", "Helvetica", 8.5, t.muted, align="right"),
    ]


HOSPITAL_DESCRIPTIONS = {
    "Back, neck and spine": "Investigation and treatment of the back, neck and spinal column, including spinal fusion.",
    "Cataracts": "Surgery to remove a cataract and replace it with an artificial lens.",
    "Heart and vascular system": "Investigation and treatment of the heart, heart-related conditions and blood vessels.",
    "Joint replacements": "Surgery for joint replacements, including revisions, resurfacing and partial replacements.",
    "Dental surgery": "Surgery to the teeth and gums, such as surgery to remove wisdom teeth and dental implant surgery.",
    "Hospital psychiatric services": "Hospital treatment of mental health conditions, including drug and alcohol dependence.",
    "Rehabilitation": "Physical rehabilitation in hospital after an illness, injury or surgery.",
    "Insulin pumps": "Provision and replacement of insulin pumps for the treatment of diabetes.",
    "Pregnancy and birth": "Hospital treatment for investigation and treatment of conditions associated with pregnancy and childbirth.",
    "Weight loss surgery": "Surgery designed to reduce a person's weight, such as gastric banding and sleeve gastrectomy.",
}


def _status(name: str) -> Status:
    label, status = Q(f"hospital:{name}").split(": ", 1)
    assert label == name
    return Status(name, status, HOSPITAL_DESCRIPTIONS[name])


def pages() -> list[Page]:
    p2 = Page(
        [
            H(1, "Your membership details"),
            P(
                "These are the details we hold for your membership. Please check them carefully and let us know within "
                "30 days if anything needs to change.",
                "lead",
            ),
            Grid(
                ["Membership", "Details"],
                [
                    ["Membership number", "{{au_member_no}}"],
                    ["Policy", PLAN],
                    ["Membership type", "Couple"],
                    ["State of residence", "Victoria"],
                    ["Cover start date", "1 July 2024"],
                ],
                [0.36, 0.64],
                label_col=True,
            ),
            Grid(
                ["Role", "People on this policy", "Date of birth"],
                [
                    ["Policy holder", "{{au_name_a}}", "{{au_dob_a}}"],
                    ["Partner", "{{au_name_b}}", "{{au_dob_b}}"],
                ],
                [0.36, 0.34, 0.30],
            ),
            Grid(
                ["Contact details", "Details"],
                [
                    ["Residential address", "{{au_address}}"],
                    ["Mobile", "{{au_phone}}"],
                    ["Email", "{{au_email}}"],
                    ["Medicare card number", "{{au_medicare}}"],
                    ["Preferred contact", "Email"],
                ],
                [0.36, 0.64],
                label_col=True,
            ),
            H(2, "Premium and rebate"),
            Grid(
                ["Item", "Details"],
                [
                    ["Monthly premium before rebate", "$498.35"],
                    ["Australian Government Rebate", "Base tier, applied as a premium reduction"],
                    ["Lifetime Health Cover loading", "0%"],
                    ["Payment method", "Direct debit, see page 12"],
                    ["Monthly amount payable", "$375.72"],
                ],
                [0.36, 0.64],
                label_col=True,
            ),
            P(
                "Premiums are reviewed each year on 1 April. We will write to you at least 30 days before your premium "
                "changes.",
                "small",
            ),
        ]
    )

    p3 = Page(
        [
            H(1, "About your cover"),
            P(
                f"{PLAN} is a combined hospital and extras policy from {INSURER}. Your hospital cover is classified as "
                "Silver Plus. Your cover started on 1 July 2024. Extras limits reset on 1 January each calendar year, "
                "and all amounts are in Australian dollars.",
                "lead",
            ),
            P(
                "This summary explains what your policy covers. Read it with the Private Health Information Statement "
                "for your policy and the fund rules, which set out the full terms of your cover."
            ),
            H(2, "Your cover at a glance"),
            Grid(
                ["Cover", "What you get", "Limit or excess", "Page"],
                [
                    [
                        "Hospital – Silver Plus",
                        "Agreement private hospitals, included categories",
                        "$500 excess per person per year",
                        "4",
                    ],
                    ["General dental", "Fixed benefit per item", "$800 per person", "7"],
                    ["Major dental", "60% of the charge", "$1,000 per person", "7"],
                    ["Optical", "100% back", "$250 per person", "8"],
                    ["Physio, chiro and remedial massage", "Fixed benefit per item", "$700 combined per person", "8"],
                    ["Psychology", "60%, up to $80 per session", "$500 per person", "9"],
                    ["Emergency ambulance", "100% of the charge", "Unlimited", "9"],
                ],
                [0.3, 0.33, 0.29, 0.08],
            ),
            P("Extras limits apply per person per calendar year.", "small"),
            H(2, "Contents"),
            Grid(
                ["Section", "Page"],
                [
                    ["Your membership details", "2"],
                    ["Hospital cover", "4"],
                    ["Clinical categories", "5"],
                    ["Extras: dental", "7"],
                    ["Extras: optical and therapies", "8"],
                    ["Extras: psychology and ambulance", "9"],
                    ["Waiting periods and important information", "10"],
                    ["Making a claim", "11"],
                    ["Direct debit and contact details", "12"],
                ],
                [0.86, 0.14],
            ),
        ]
    )

    p4 = Page(
        [
            H(1, "Hospital cover"),
            P(
                "Your Silver Plus hospital cover pays towards the cost of treatment as a private patient in a private "
                "or public hospital for the clinical categories included in your policy."
            ),
            H(2, "Private hospital admission"),
            P(
                sentence(
                    Q("ben-hospital-admission"),
                    f"{INSURER} has agreements with most private hospitals in Australia. Check whether your hospital is "
                    "an agreement hospital before you are admitted.",
                    bold=True,
                )
            ),
            H(2, "Your excess"),
            P(lead(Q("cs-hospital-excess"), "You pay the excess to the hospital when you are admitted.")),
            H(2, "Medical fees and the gap"),
            P(
                "For in-hospital medical services, Medicare pays 75% of the Medicare Benefits Schedule fee and we pay the "
                "remaining 25%. If your doctor charges more than the schedule fee and does not use our gap scheme, you "
                "may have out-of-pocket costs. Ask your doctor for an estimate of fees before you are admitted."
            ),
            H(2, "Waiting periods for hospital cover"),
            Grid(
                ["Treatment", "Waiting period"],
                [
                    ["Emergency admissions after an accident", "None"],
                    ["Hospital psychiatric services, rehabilitation and palliative care", "2 months"],
                    ["All other hospital treatment", "2 months"],
                    ["Pre-existing conditions", "12 months"],
                    ["Pregnancy and birth", "12 months (not included in this policy)"],
                ],
                [0.62, 0.38],
            ),
            Callout(
                "Medicines in hospital",
                [
                    "Prescription medicines supplied while you are admitted are covered when they are related to the "
                    "treatment you were admitted for and are approved for use in hospital."
                ],
            ),
        ]
    )

    p5 = Page(
        [
            H(1, "Clinical categories"),
            P(
                "Every hospital policy in Australia is described using standard clinical categories. Silver Plus "
                "policies include all of the categories required for Silver cover and may include some additional "
                "categories. Your policy covers the following categories:"
            ),
            _status("Back, neck and spine"),
            _status("Cataracts"),
            _status("Heart and vascular system"),
            _status("Joint replacements"),
            _status("Dental surgery"),
            _status("Hospital psychiatric services"),
            _status("Rehabilitation"),
            Callout(
                "What covered means",
                [
                    "You are treated as a private patient in a private or public hospital, with benefits for "
                    "accommodation, theatre fees and medical fees as described in this summary, after your excess and "
                    "any waiting period."
                ],
            ),
            H(2, "Before you go to hospital"),
            Bullets(
                [
                    "Check that your treatment falls under a covered clinical category",
                    "Ask your hospital whether it has an agreement with Wattle Health Fund",
                    "Ask your specialist and anaesthetist for a written estimate of their fees",
                    "Make sure any waiting periods have been served",
                ]
            ),
        ]
    )

    p6 = Page(
        [
            H(1, "Restricted and excluded categories"),
            P("Some categories have restricted cover, and some are not covered by this policy."),
            _status("Insulin pumps"),
            _status("Pregnancy and birth"),
            _status("Weight loss surgery"),
            H(2, "What restricted cover means"),
            P(
                "For a restricted category, you are covered as a private patient in a shared ward of a public hospital "
                "only. If you choose a private hospital, benefits will not cover the full cost of accommodation and "
                "you could have significant out-of-pocket costs."
            ),
            H(2, "Categories that are not covered"),
            P(
                "No benefits are paid for hospital treatment in a category that is not covered, other than in the "
                "limited circumstances allowed by law. To be covered for pregnancy and birth you would need to upgrade "
                "to a Gold hospital policy, and a 12 month waiting period would apply to the upgraded benefits."
            ),
            Callout(
                "Planning a family?",
                [
                    "Pregnancy and birth is included only in Gold hospital policies. Upgrade at least 12 months before "
                    "the expected date of delivery so that the waiting period has been served."
                ],
            ),
            H(2, "Changing your cover"),
            P(
                "You can move to a higher or lower hospital tier at any time. Waiting periods apply to any higher "
                "benefits, and time already served on your current policy counts for the benefits it includes."
            ),
        ]
    )

    p7 = Page(
        [
            H(1, "Extras cover: Mid Extras"),
            P(
                "Mid Extras pays benefits for services from recognised providers. Benefits are paid up to the limits "
                "shown, and are never more than the amount you were charged."
            ),
            H(2, "General dental"),
            P(lead(Q("ben-general-dental"), "A fixed benefit is paid for each item number.")),
            Grid(
                ["Item", "Service", "Benefit"],
                [
                    ["011", "Comprehensive oral examination", "$45.00"],
                    ["012", "Periodic oral examination", "$40.00"],
                    ["022", "Intraoral periapical radiograph", "$30.00"],
                    ["114", "Removal of calculus", "$70.00"],
                    ["121", "Topical fluoride", "$20.00"],
                ],
                [0.14, 0.62, 0.24],
            ),
            H(2, "Major dental"),
            P(
                lead(
                    Q("ben-major-dental"),
                    "Major dental includes crowns, bridges, dentures, root canal treatment and the surgical removal of "
                    "wisdom teeth.",
                )
            ),
            Callout(
                "Orthodontics",
                ["Orthodontic treatment, such as braces and aligners, is not included in Mid Extras."],
            ),
            H(3, "Getting the most from your dental cover"),
            Bullets(
                [
                    "Ask your dentist for the item numbers of any planned treatment and call us for a quote",
                    "Claim on the spot so you only pay the gap at the dental practice",
                    "Your general dental limit does not carry over to the next calendar year",
                ]
            ),
        ]
    )

    p8 = Page(
        [
            H(1, "Extras cover: optical and therapies"),
            H(2, "Optical"),
            H(3, "Glasses and contact lenses"),
            P(
                lead(
                    Q("ben-optical"),
                    "Benefits are paid for prescription lenses only, including prescription glasses and contact lenses.",
                )
            ),
            H(2, "Therapies"),
            H(3, "Physiotherapy, chiropractic and remedial massage"),
            P(
                "A fixed benefit is paid for each consultation or treatment. A 2 month waiting period applies to each of these services."
            ),
            Bullets(
                [
                    lead(Q("ben-physio")),
                    lead(Q("ben-chiro")),
                    lead(Q("ben-remedial-massage"), f"Providers must be recognised by {INSURER}."),
                ]
            ),
            P(sentence(Q("pool-therapies"), bold=True)),
            Callout(
                "Example",
                [
                    "Your initial physiotherapy consultation costs $95 and the next one costs $80. We pay $55 and $45, "
                    "and the $100 in benefits counts toward the therapies combined limit."
                ],
            ),
            Callout(
                "Recognised providers",
                [
                    "We pay benefits only for services from providers who hold current registration or membership of a "
                    "professional association that we recognise. Check before your appointment by calling 1300 975 707."
                ],
            ),
            H(3, "Not included"),
            Bullets(
                [
                    "Group classes, such as clinical pilates, unless provided one-on-one",
                    "Massage provided at a day spa or as part of a beauty treatment",
                    "Sunglasses, and frames bought without prescription lenses",
                ]
            ),
        ]
    )

    p9 = Page(
        [
            H(1, "Extras cover: psychology and ambulance"),
            H(2, "Therapies"),
            H(3, "Psychology"),
            P(
                lead(
                    Q("ben-psychology"),
                    "A 2 month waiting period applies. Sessions claimed under a Medicare Better Access plan cannot also "
                    "be claimed here.",
                )
            ),
            H(2, "Ambulance"),
            H(3, "Emergency ambulance"),
            P(
                lead(
                    Q("ben-ambulance"),
                    "Cover applies to emergency transport by a state ambulance service when you are taken to hospital "
                    "for immediate treatment.",
                )
            ),
            H(2, "Not included in Mid Extras"),
            Bullets(
                [
                    "Orthodontics",
                    "Hearing aids",
                    "Podiatry and foot orthotics",
                    "Dietetics and exercise physiology",
                    "Non-emergency patient transport",
                ]
            ),
            Callout(
                "Medicare and extras",
                [
                    "Extras benefits are not payable for a service that also receives a Medicare benefit. If Medicare "
                    "pays part of a consultation, you cannot claim the rest of it from your extras cover."
                ],
            ),
            H(2, "Using your extras cover"),
            P(
                "Extras benefits are paid per person, so each person on the policy has their own limits. Limits start "
                "again on 1 January, and any amount you do not use in a calendar year does not carry over."
            ),
            P(
                "Receipts for psychology must show that the service was provided by a registered psychologist, with the "
                "session date, its length and the fee charged."
            ),
        ]
    )

    p10 = Page(
        [
            H(1, "Waiting periods and important information"),
            Grid(
                ["Extras service", "Waiting period"],
                [
                    ["General dental", "2 months"],
                    ["Major dental", "12 months"],
                    ["Optical", "6 months"],
                    ["Physiotherapy, chiropractic and remedial massage", "2 months"],
                    ["Psychology", "2 months"],
                    ["Emergency ambulance", "None"],
                ],
                [0.62, 0.38],
            ),
            P(
                "Waiting periods apply when you first join and when you upgrade to a higher level of cover. Time served "
                "on an equivalent policy with another Australian fund counts towards your waiting periods."
            ),
            H(2, "Lifetime Health Cover"),
            P(
                "If you did not hold hospital cover by 1 July following your 31st birthday, a loading of 2% for every "
                "year over age 30 may be added to your hospital premium. The loading is removed after 10 years of "
                "continuous hospital cover."
            ),
            H(2, "Medicare levy surcharge"),
            P(
                "Holding an appropriate level of private hospital cover means you will not pay the Medicare levy "
                "surcharge. The surcharge applies to some people with incomes above a threshold who do not hold "
                "hospital cover."
            ),
            H(2, "Australian Government Rebate"),
            P(
                "The rebate is income tested. It is applied as a reduction to your premium unless you choose to claim it "
                "in your tax return."
            ),
            H(2, "General exclusions"),
            Bullets(
                [
                    "Treatment received outside Australia",
                    "Services that are not clinically necessary, or that are cosmetic",
                    "Treatment covered by compensation, damages or another insurer",
                    "Services provided by a family member or business partner",
                ]
            ),
        ]
    )

    p11 = Page(
        [
            H(1, "Making a claim"),
            H(2, "Ways to claim"),
            Bullets(
                [
                    "**At your provider:** Claim on the spot with your member card where the provider has a HICAPS "
                    "terminal. You pay only the gap.",
                    "**In the app:** take a photo of your itemised receipt and submit it in the Wattle Health app.",
                    "**By post:** send a claim form and your receipts to the address on page 12.",
                ]
            ),
            P("Quote your membership number {{au_member_no}} when you call or write to us about a claim."),
            H(2, "Time limits"),
            P(sentence(Q("claimRules"), "Hospital claims are usually sent to us directly by the hospital.", bold=True)),
            H(2, "Receipts"),
            P(
                "You need an itemised receipt for every extras claim. The receipt must show the provider's name and "
                "provider number, the patient's name, the date of service, the item number and the fee charged."
            ),
            H(2, "When benefits are not payable"),
            P(
                "Benefits cannot be paid for services claimed from Medicare or another insurer. We may ask for more "
                "information before we pay a claim, and benefits are paid only while premiums are paid up to date."
            ),
            Callout(
                "Payment of benefits",
                ["Benefits are paid by direct credit to your nominated bank account, usually within 2 business days."],
            ),
            H(3, "Claim checklist"),
            Bullets(
                [
                    "The patient's name as it appears on your membership",
                    "The provider's name, provider number and practice address",
                    "The date of service, item number and fee for each service",
                    "Proof that the account has been paid, unless you claimed on the spot",
                ]
            ),
        ]
    )

    p12 = Page(
        [
            H(1, "Direct debit request"),
            P(
                f"You have authorised {INSURER} (user ID 481726) to debit your premiums from the account below. This "
                "request is governed by the direct debit request service agreement."
            ),
            Grid(
                ["Debit details", "Details"],
                [
                    ["Account name", "{{au_name_a}}"],
                    ["Financial institution", "Eucalypt Mutual Bank"],
                    ["BSB", "{{au_bsb}}"],
                    ["Account number", "{{au_account}}"],
                    ["Debit frequency", "Monthly, on the 15th"],
                    ["Amount", "$375.72"],
                    ["Membership number", "{{au_member_no}}"],
                ],
                [0.36, 0.64],
                label_col=True,
            ),
            P(
                "If a debit is returned unpaid, we will contact you and may try the debit again after 5 business days. "
                "You can change or cancel this arrangement by giving us at least 3 business days' notice.",
                "small",
            ),
            H(2, "Contact us"),
            TwoCol(
                [
                    Callout(
                        "Call us", ["1300 975 707", "Monday to Friday, 8.30am to 6pm AEST", "Saturday, 9am to 1pm AEST"]
                    )
                ],
                [Callout("Write to us", [INSURER, "Locked Bag 4020", "Ballarat VIC 3353"])],
            ),
            H(2, "Your privacy"),
            P(
                "We collect personal and health information to manage your membership and pay claims. Our privacy "
                "policy explains how you can access or correct your information and how to make a complaint. If we "
                "cannot resolve a complaint, you can contact the private health insurance ombudsman."
            ),
            Gap(8),
            P(
                "Sample document. The fund, people, identification numbers, bank details and policy terms in this summary "
                "are fictional and were created for the Benefura demo.",
                "small",
            ),
        ]
    )

    return [Page(cover=cover), p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12]


def spec() -> DocSpec:
    return DocSpec(
        filename="au-wattle-policy.pdf",
        title=f"{PLAN} – cover summary",
        subject="Fictional sample private health insurance policy summary for the Benefura demo",
        author=INSURER,
        theme=THEME,
        header_left=INSURER,
        header_right=PLAN,
        footer_left="Membership number {{au_member_no}}",
        pages=pages(),
        registry=AU_PII,
    )
