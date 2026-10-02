# Privacy

Benefura is built so that you never have to send your personal information to use it. This page
explains exactly what leaves your browser, what Azure keeps and for how long, and where the limits
of those protections are.

> Benefura is a demo. Use the sample documents or your own booklet with personal details removed.
> It is not insurance, tax or medical advice.

## What stays in your browser

Everything you work with is stored in your browser's IndexedDB on your device. There is no
Benefura database and no account.

- Your original booklet, policy and receipt files (held in memory while you redact; never saved).
- The alias map: the real names, numbers and addresses behind tokens like `[MEMBER_A]`.
- Your plans, claims, receipts, chat history and the log of outbound requests.

You can export or delete all of it from **Settings**.

## What leaves your browser

| When | What is sent | What is not sent |
|---|---|---|
| Reading a booklet | Image-only PDFs of the redacted pages, 5 pages at a time. Boxes and alias labels are painted into the pixels; there is no text layer and no document metadata | The original file, the alias map |
| Reading a receipt | One redacted, downscaled image | The original image, the alias map |
| Chatting | Your messages after saved aliases are applied, a small context (region, today's date, time zone, currency, plan name, alias tokens, category names) and the results of tools that ran in your browser | Your stored plans or claims in bulk |
| Waking the API | A request to `/healthz` with no body | — |

Before any image is sent you see **exactly** the images that will be sent and tick an
acknowledgment. The privacy inspector (the eye button in the header) lists every request that left
the browser with its size, image thumbnails, text preview and trace id.

## What Azure keeps

All processing happens in Azure East US 2 on Global Standard model deployments. Public reference
content is indexed in Azure AI Search, which may be in Canada Central; it only ever holds public
pages.

| Service | What is kept | For how long |
|---|---|---|
| Content Understanding | Analysis results for the redacted images | Up to 24 hours; Benefura deletes each result as soon as it has read it |
| Azure OpenAI abuse monitoring | Prompts and outputs may be sampled for abuse review | Up to 30 days |
| Application Insights | Traces and metrics. Foundry's server-side agent traces include your **aliased** chat messages and there is no switch to turn that off. Benefura's own telemetry records categories and counts, never message text | 30 days |
| Foundry conversations | Nothing. Every model call uses `store=false`; history is replayed from your browser | — |

## The limits of these protections

- **Browser redaction is the control.** The server also runs an Azure Language PII check on the text
  it reads from your images. It is a backstop, not prevention: by the time it finds something, the
  image has already been processed by Content Understanding and may be in abuse monitoring. If it
  finds a government or health identifier, a bank account or a date of birth, the request is
  rejected and you are taken back to that page to add a box.
- **Names and addresses of organisations are expected.** Insurer names, clinic addresses and phone
  numbers are normal in booklets and receipts. They are reported as advisories and never block.
  Your own name and contact details are caught by the values you enter in "What should we hide?".
- **Detection is not perfect.** Automatic detectors use patterns, checksums and labels. Review every
  page before you acknowledge.

## Production data residency

The live demo runs in East US 2 because that is where every required service and model is available
on a single keyless account. A production deployment for Canadian or Australian members would keep
processing in-country; see [ADR 0006](adr/0006-regions-and-data-residency.md).
