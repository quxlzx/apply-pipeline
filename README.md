# apply-pipeline

Fast, **review-gated** internship application prep. Built to stop the two slow
parts of agent-driven applying: scraping each posting by hand, and
hand-assembling the human-approval card.

Everything **upstream of the browser** runs through portal APIs in one call.
The browser is touched only for the final fill + submit, and only **after a
human approves the review card**.

## The gate (non-negotiable)

```
posting URL ──► apply.py card ──► human reviews ──► browser fill + submit ──► verify
                (no browser)      (approve/deny)     (ONE call)             (confirmation)
```

Nothing is ever submitted without an approved card. `consent.auto_submit_portals`
means "the agent may click submit after approval" — it never means "skip the
approval."

## Commands

```bash
./apply.py inspect <url>   # metadata: title, locations, pay+provenance,
                           # deadline, custom questions (no browser)
./apply.py check <url>     # eligibility: exit 0 fits / 1 suspect / 2 ineligible
./apply.py plan <url>      # deterministic field-by-field fill plan
./apply.py card <url>      # ready-to-post review-card body (gate content inline)
```

`card` output already contains the fields a reviewer needs — pay + provenance,
deadline (or "none stated"), eligibility, and the full fill plan — so it passes
the review gate on the first try instead of bouncing for missing content.

## Supported portals (metadata, no browser)

| portal | source |
|---|---|
| Greenhouse | `boards-api.greenhouse.io` (`?questions=true`) |
| Lever | `api.lever.co/v0/postings` |
| Ashby | `api.ashbyhq.com/posting-api/job-board?includeCompensation=true` |
| Workday / other | raw-HTML fallback (weaker; browser still needed) |

## Pay provenance

Every pay figure is labelled **employer-posted** (from the req's own
pay-transparency section) or dropped. Aggregator estimates are never invented
here — this tool only reads what the posting itself publishes.

## Profile & privacy

Copy `profile.example.yaml` → `profile.yaml` and fill it in. **`profile.yaml`
is gitignored — never commit real PII** (name, phone, email, links). The public
repo ships only the template.

## Hard rules

- Never auto-fill: SSN, e-signature, government-ID upload, I-9.
- Optional demographic/EEO fields: left blank by default.
- LinkedIn / Handshake: pre-fill only — a human clicks submit.
- CAPTCHA / 2FA: hard stop, hand off to the human.
- One application per posting; check the tracker for an existing
  `applied` + confirmation ID before prepping.

## License

MIT
