# 0009: Two-tier server PII policy

**Status:** Accepted. Thresholds calibrated by M0 spike 11.

## Context
The API runs Azure Language PII detection on all text it receives as a backstop to browser
redaction. Booklets and receipts legitimately contain organisation names, addresses and phone
numbers (the insurer, the clinic), so blocking on every PII category would reject valid documents.

## Decision
- **Hard tier** (blocks with 422 `pii_detected` and the page and polygon): Canadian SIN, health
  service and personal health numbers, bank accounts; Australian TFN, Medicare, bank accounts and
  driver's licence; date of birth.
- **Advisory tier** (reported in `issues`, never blocks): Person, Organization, Address, PhoneNumber, Email.
- Alias tokens are ignored after a fuzzy normalizer repairs OCR damage such as `IMEMBER_A]`.
- In chat, a hard-tier hit in the user's message is not sent to any model.

## Consequences
The browser's known-values aliasing remains the control for the member's own name and contact
details. The server check can only reduce harm after an image has been processed; this is stated in
the acknowledgment and in [privacy](../privacy.md).
