import { degrees, PDFDocument, rgb, StandardFonts } from "pdf-lib";

export const FAKE = {
  name: "Jordan Testcase",
  sin: "046 454 286",
  phone: "(416) 555-0199",
  email: "jordan.testcase@example.com",
  certificate: "NW-4471-0093",
};

/** The SIN is drawn as two runs in different fonts so pdf.js can't merge them into one text item. */
export async function makeBookletPdf(opts: { pages?: number; rotateSecond?: boolean; imageOnlyPage?: boolean } = {}): Promise<Uint8Array> {
  const doc = await PDFDocument.create();
  const regular = await doc.embedFont(StandardFonts.Helvetica);
  const bold = await doc.embedFont(StandardFonts.HelveticaBold);
  const pages = opts.pages ?? 1;
  for (let i = 0; i < pages; i++) {
    const page = doc.addPage([612, 792]);
    if (opts.imageOnlyPage && i === pages - 1) {
      page.drawRectangle({ x: 72, y: 600, width: 300, height: 80, color: rgb(0.2, 0.2, 0.2) });
      continue;
    }
    page.drawText(`Northwind group benefits, page ${i + 1}`, { x: 72, y: 720, size: 16, font: bold });
    page.drawText(`Plan member: ${FAKE.name}`, { x: 72, y: 680, size: 12, font: regular });
    const lead = "Social insurance number: 046 45";
    page.drawText(lead, { x: 72, y: 660, size: 12, font: regular });
    page.drawText("4 286", { x: 72 + regular.widthOfTextAtSize(lead, 12), y: 660, size: 12, font: bold });
    page.drawText(`Phone: ${FAKE.phone}`, { x: 72, y: 640, size: 12, font: regular });
    page.drawText(`Certificate no. ${FAKE.certificate}`, { x: 72, y: 620, size: 12, font: regular });
    page.drawText("Massage therapy: 80% to a maximum of $500 per person per benefit year", { x: 72, y: 580, size: 11, font: regular });
    if (opts.rotateSecond && i === 1) page.setRotation(degrees(90));
  }
  return doc.save();
}
