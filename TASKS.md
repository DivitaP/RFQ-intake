# Ship checklist (today)

## 1. Get an LLM key (5 min)

Groq is free with no card: https://console.groq.com/keys. The provider is picked from whichever key is set: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY`. To change the default models, set `LLM_MODEL` / `LLM_VISION_MODEL`.

### Gemini (original option)

Create one at https://aistudio.google.com/apikey (the free tier is enough).

If `gemini-3.8-flash` returns 404 or "deprecated", set `GEMINI_MODEL` to the current Flash model name. Nothing else changes.

## 2. Run locally and get the real numbers (20 min)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                               # 28 should pass

export GROQ_API_KEY=...                                 # or another provider's key
python eval.py --mode all --export ../frontend/public
```

This runs rules, then LLM-only, then rules+LLM; about 40 LLM calls the first time, cached after that.

If you hit 429s, the client backs off and retries. If it still fails, just rerun: cached calls are skipped.

**Sanity-check before you trust the numbers:**
- **Scan slice:** item recall should be well above 0%. If it's 0, read the warnings in `frontend/public/samples/rfq_13_scan.result.json`.
- **Auto precision** in rules+LLM should stay at or near 100%. If it drops, look at which items were wrong before changing any threshold.
- **Expectations for LLM-only:** probably high field accuracy, but 0% auto-approved. That's by design: LLM-sourced fields always go to review. That trade-off is your talking point.

Then:
1. Paste the overall rows into the README table.
2. Commit `backend/.llm_cache.json` so anyone can reproduce the eval without a key.

## 3. Check the UI locally (15 min)

```bash
# terminal 1 (backend/)
RFQ_LLM=1 uvicorn app.main:app --port 8000
# terminal 2
cd frontend && npm install && npm run dev
```

Check each of these:
- [ ] The page opens on the email sample, with a mix of green and red cells.
- [ ] Clicking a cell lets you edit it; Enter saves; the ✎ mark appears.
- [ ] "Approve all clean", then Export JSON / CSV, downloads files.
- [ ] Uploading a scan PNG with the Rules + LLM mode gives lines.
- [ ] The eval panel shows all three modes.
- [ ] `npm run build` passes. TypeScript was not compiled in the session that wrote this; fix any type errors it reports.

## 4. Deploy (20 min): one Vercel project

1. Push the repo to GitHub, then on Vercel choose **New Project** and import it, keeping the **root directory = repo root**. `vercel.json` already sets:
   - the build: `frontend/dist`
   - the Python function: `api/index.py`, which serves `/api/*`
2. Add these environment variables:
   - `GROQ_API_KEY` (or another provider's key)
   - `RFQ_LLM=1`
   - `RFQ_RATE_LIMIT=20`
3. Deploy, then check:
   - `https://<app>.vercel.app/api/health` returns `{"ok":true,"llm":true,...}`
   - Upload one PDF and one PNG.

**If `/api/health` returns 404 or the function fails to import:** host the backend on Render instead.
- Use a Python web service with root `backend`.
- Build: `pip install -r requirements.txt`. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Set the same environment variables, plus `CORS_ORIGINS=https://<app>.vercel.app`.
- In Vercel, set `VITE_API_URL=https://<render-app>.onrender.com` and redeploy.
- The samples stay instant either way, because they're static.

## 5. GIF / Loom (15 min)

Record 60–90 seconds, in this order:
1. The email sample: shorthand lines flagged; hover a red cell to show the issue.
2. Edit a cell and approve.
3. Approve all clean, then export.
4. Upload a scan.
5. The eval panel: rules vs LLM-only vs hybrid.

Put the link at the top of the README.

## Talking points

- **Why hybrid:** "I measured it. Rules are free, instant and exact on formulaic lines. LLM-only reads shorthand well, but every value needs review because I can't verify its confidence. The hybrid gets the coverage of the LLM with the precision of the rules."
- **Guardrails:** the LLM output is enum-constrained, it never overwrites rules, and a disagreement between rules and the LLM is a review flag.
- **Scan confidence:** two-pass self-agreement, at the token level, because one flipped character in `A234` is a wrong spec.
- **What the eval caught:** segmentation, not parsing, was the bottleneck on wrapped tables.
- **Next:** catalog matching, and a feedback loop from reviewer edits into the vocabulary.

---

# v2: catalog match, agent, feedback loop

```bash
cd backend && pip install -r requirements.txt     # adds pyyaml
python -m pytest -q                                # 39 passed
python eval.py --export ../frontend/public                              # rules row + catalog stats
python eval.py --mode rules+llm --agent --export ../frontend/public     # re-calls the LLM once (prompt now asks for evidence) + agent
python eval.py --learning-curve --export ../frontend/public             # feedback-loop chart in the eval panel
```

Run the `rules+llm --agent` command after the others, because the UI samples come from the last mode-run export. Run `--learning-curve` last; it only adds its chart to `eval.json` and doesn't touch the samples.

## Persistent learned rules on Vercel (free)

1. In Vercel, open your project, go to **Storage**, choose **Create** (or **Marketplace**), select **Upstash Redis**, and connect it to the project. This adds `KV_REST_API_URL` and `KV_REST_API_TOKEN`, or `UPSTASH_REDIS_REST_URL` / `_TOKEN`; both pairs are supported.
2. Redeploy. `/api/health` should show `"rules_store": "upstash"`.
3. Optional: set `RFQ_ADMIN_TOKEN` to any secret. Then reset the rules before recording the demo with:
   `curl -X POST https://<app>/api/rules/reset -H "x-admin-token: <secret>"`

Without Upstash, rules live in `/tmp` and reset on a cold start, and the UI says so.

## Demo flow for the GIF

1. **Email sample:** a shorthand line shows a purple **AI** field.
2. **Hover the field:** the tooltip shows `from text: "CS FORGED"`.
3. **Approve the row:** a toast says "Learned 1 rule".
4. **Click Re-run:** "LLM calls 3 → 2", and the field now shows **L** (learned).
5. **Open a "no exact SKU" line:** it shows the candidates with `STD → SCH 40 ⓘ` and the agent's verdict; hover the verdict for its tool trace.
6. **Click "use", then approve:** the substitute is learned.
7. **The eval panel:** three modes plus the feedback-loop chart.

## Talking points (v2)

- **Agentic where judgment is needed, and bounded:** one tool, at most 3 calls, a validated output, and never auto-approved.
- **Closed loop:** the LLM cites evidence, a human confirms, a rule is written, and the next RFQ costs no LLM call. The learning curve shows this.
- **Rules are a reviewable YAML file,** not hidden model state.
