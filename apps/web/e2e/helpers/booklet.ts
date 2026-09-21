import { PDFDocument, StandardFonts, type PDFFont, type PDFPage } from "pdf-lib";

/** None of these may appear in any request. */
export const FAKE_PII = {
  name: "Jordan Testcase",
  spouse: "Riley Testcase",
  employer: "Acme Widgets Ltd",
  sin: "046 454 286",
  healthCard: "9876 543 217",
  certificate: "NW-4471-0093",
  policy: "88213",
  phone: "(416) 555-0199",
  email: "jordan.testcase@example.com",
  street: "12 Maple Street",
  postal: "M5V 2T6",
  dob: "1985-06-15",
  /** Unlabelled and no known pattern: detectors miss it, the server's check catches it. */
  reference: "QZ7 XK4 WPL",
  /** Same spot in Courier on pages 5 and 6, so label-only boxes over them must give identical pixels. */
  memberRefPage5: "A1234-7781",
  memberRefPage6: "B9876-3312",
} as const;

export function piiNeedles(): string[] {
  const values: string[] = Object.values(FAKE_PII);
  const extra = values.flatMap((v) => [v.replace(/[\s()-]/g, ""), v.toLowerCase()]);
  return [...new Set([...values, ...extra])].filter((v) => v.length >= 5);
}

export const REFERENCE_LINE = { page: 2, x: 72, baselineFromTop: 150, size: 12 };

function text(page: PDFPage, font: PDFFont, s: string, x: number, yTop: number, size = 12) {
  page.drawText(s, { x, y: page.getHeight() - yTop, size, font });
}

export async function makeTestBooklet(): Promise<{ bytes: Uint8Array; referenceBoxInches: number[] }> {
  const doc = await PDFDocument.create();
  doc.setTitle("Test booklet");
  const regular = await doc.embedFont(StandardFonts.Helvetica);
  const bold = await doc.embedFont(StandardFonts.HelveticaBold);
  const mono = await doc.embedFont(StandardFonts.Courier);

  const p1 = doc.addPage([612, 792]);
  text(p1, bold, "Northwind Life & Health: group benefits booklet", 72, 80, 18);
  text(p1, regular, `Plan member: ${FAKE_PII.name}`, 72, 130);
  text(p1, regular, `Employer: ${FAKE_PII.employer}`, 72, 150);
  text(p1, regular, `Group policy number ${FAKE_PII.policy}`, 72, 170);
  text(p1, regular, `Certificate no. ${FAKE_PII.certificate}`, 72, 190);
  // Two runs in different fonts, so the SIN reaches the detectors split in two.
  const lead = "Social insurance number: 046 45";
  text(p1, regular, lead, 72, 210);
  text(p1, bold, "4 286", 72 + regular.widthOfTextAtSize(lead, 12), 210);
  text(p1, regular, `Ontario health card: ${FAKE_PII.healthCard}`, 72, 230);
  text(p1, regular, `Date of birth: ${FAKE_PII.dob}`, 72, 250);
  text(p1, regular, `Address: ${FAKE_PII.street}, Toronto ON ${FAKE_PII.postal}`, 72, 270);
  text(p1, regular, `Phone: ${FAKE_PII.phone}`, 72, 290);
  text(p1, regular, `Email: ${FAKE_PII.email}`, 72, 310);
  text(p1, regular, "Claims questions: claims@northwind.example or 1-800-555-0100", 72, 350);

  const lines = [
    ["Paramedical practitioners", "Massage therapy: 80% up to $80 per visit, maximum $500 per person per benefit year", "Physiotherapy: 80%, maximum $750 per person per benefit year"],
    ["Vision care", "Eye exam: 100%, one exam every 24 months", "Glasses and contact lenses: 100% to $250 every 24 months"],
    ["Dental care", "Basic services: 80%, no annual maximum", "Major services: 50% to $1,500 per person per benefit year"],
    ["Prescription drugs", "Generic drugs: 80% after a $25 annual deductible", "Pay-direct drug card for eligible prescriptions"],
    ["Claims", "Submit claims within 12 months of the service date", "Keep original receipts for 12 months"],
  ];
  lines.forEach((block, i) => {
    const page = doc.addPage([612, 792]);
    text(page, bold, block[0], 72, 80, 16);
    text(page, regular, block[1], 72, 120, 11);
    text(page, regular, block[2], 72, 140, 11);
    if (i === 0) text(page, regular, `Claim reference ${FAKE_PII.reference}`, REFERENCE_LINE.x, REFERENCE_LINE.baselineFromTop);
    if (i === 1) text(page, regular, `Dependant coverage applies to ${FAKE_PII.spouse}.`, 72, 170, 11);
    if (i === 3) text(page, regular, `SIN on file: ${FAKE_PII.sin}`, 72, 170, 11);
    if (i === 3) text(page, mono, `Member reference: ${FAKE_PII.memberRefPage5}`, 72, 320, 12);
    if (i === 4) text(page, mono, `Member reference: ${FAKE_PII.memberRefPage6}`, 72, 320, 12);
    text(page, regular, `Page ${i + 2}`, 72, 740, 9);
  });

  // CU-style polygon in inches (x1,y1,…,x4,y4).
  const size = REFERENCE_LINE.size;
  const x0 = REFERENCE_LINE.x + regular.widthOfTextAtSize("Claim reference ", size);
  const x1 = x0 + regular.widthOfTextAtSize(FAKE_PII.reference, size);
  const y0 = REFERENCE_LINE.baselineFromTop - size * 0.8;
  const y1 = REFERENCE_LINE.baselineFromTop + size * 0.2;
  const inch = (pt: number) => Number((pt / 72).toFixed(4));
  const referenceBoxInches = [x0, y0, x1, y0, x1, y1, x0, y1].map(inch);

  return { bytes: await doc.save(), referenceBoxInches };
}
