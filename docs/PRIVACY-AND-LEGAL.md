# Running this as a public service: privacy and legal notes

This describes what the app does about personal data and what is left to whoever runs it. It is not legal advice.

## What the app does

- Visitors' pages set no cookies and use no local or session storage. Form tokens are signed and carried in the page.
  The admin login sets one session cookie. There is no analytics and no request to another host.
- Uploading a CV needs a ticked consent box; the server refuses and reads nothing without it. The consent time and the
  wording version are logged (with a hash of the report link, not the CV) and kept for a year.
- Email addresses and phone numbers are removed from an uploaded CV before it is stored. An uploaded file is read for its
  text and dropped.
- The uploader chooses retention: deleted about two hours after the last action, or kept for 30 days. "Delete my report"
  removes the report, the CV, the chat, the letters and the traces at once. Expired reports are purged every ten minutes.
  Deleted rows are overwritten in the database file.
- Visitors' addresses are stored only as a salted hash, for rate limits.
- Reports are reachable only by their long random link. The operator can read everything, including traces with full prompts.
- The chat is limited by code to the posting, the CV and the report, and tells the user it is an AI.
- The spending caps and the per-visitor daily limit read from two small tables that hold no report content: `spend_ledger` (amount and time of each paid call, one year) and `check_log` (salted address hash and time of each check, two days). Deleting a report or letting it expire therefore cannot reduce the spend the caps see.
- A visitor may give a link to the posting instead of pasting it. The server reads that page once (usual web ports only, a short total time, a size cap, a few at a time, a per-visitor limit) and keeps the text, not the link. The privacy page says so.
- The app does not make decisions about anyone and does not send anything on anyone's behalf.

## What you have to do

- Fill in the imprint and the privacy page for your own situation (who is responsible, address, contact, the providers and
  hosts you actually use, their regions, how long backups are kept) and keep them true.
- Sign the data processing agreements your hosting company and model providers offer, and check where each provider
  processes the data. A consent checkbox for a transfer outside the EEA is not a substitute for choosing providers and safeguards.
- Decide whether you need a data protection impact assessment and a record of processing; keep them short and current.
- If you add features that rank or screen candidates for employers, the EU AI Act treats recruitment systems as high-risk.
  Assess that before building them. The app as shipped is for people checking their own CV and for demonstrating the technique.
- Have a person who knows the law of your country read the texts once before real people use the site.
