# 0002: Redact in the browser and send only rasterized images

**Status:** Accepted

## Context
Booklets and receipts contain names, identifiers and addresses alongside the benefit rules we need.
Sending the original document and redacting on the server would put raw PII into Content
Understanding results, abuse monitoring and traces before any redaction could run. Sending
reconstructed text instead of images loses table structure, which is where most benefit rules live.

## Decision
The browser extracts text positions (pdf.js) or OCR boxes (tesseract.js), runs detectors, lets the
user review boxes, then **rasterizes each page with the boxes and alias labels burned into the
pixels**. Only image-only PDFs (no text layer, no metadata) of those pages are sent, after the user
acknowledges the exact images. Alias labels are drawn large on white boxes so OCR keeps the
brackets and the extractor can refer to `[MEMBER_A]`.

## Consequences
- The server can only see what is painted, and CU's layout model reads tables far better than
  hand-rebuilt text.
- Page images are larger than text: chunks are capped at 5 pages / 8 MB, and DPI is chosen by M0
  spike 3 (lowest DPI with near-zero numeric OCR errors).
- Detection quality depends on the browser; the server PII check is a backstop ([ADR 0009](0009-two-tier-pii-policy.md)).
