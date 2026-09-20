# Benefura

Benefura is a privacy-first benefits assistant for Canadian extended health and Australian private
health plans. It reads a benefits booklet, hides personal details in the browser before anything is
sent, and keeps the plan and claims on the device. It is a work in progress.

To run what exists: `pnpm install`, then start the API with
`cd apps/api && uv sync && AI_MODE=fake uv run uvicorn app.main:app --reload --port 8000` and the web
app with `cd apps/web && pnpm dev`. Open http://localhost:3000 and choose **Try the Canadian demo**;
the demo plans are fictional and work without the API.
