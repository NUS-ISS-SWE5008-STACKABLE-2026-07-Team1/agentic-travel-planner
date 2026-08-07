# Proposal: Automated Email Notifications for SAST/DAST Findings

**Author:** drafted with Claude Code, for team discussion
**Date:** 2026-07-25
**Status:** proposal — not implemented

## The ask

When the `Security` GitHub Actions workflow (SAST via Semgrep/Bandit/pip-audit, DAST via OWASP ZAP)
finds vulnerabilities, automatically produce a findings report and email it to the team, instead of
someone having to go check the Actions tab.

## Why not build this yet

Doable, but it introduces a few things worth deciding as a team before wiring it in — mainly around
secrets, recipient ownership, and alert noise. Outlined below so you have what you need to discuss it.

## What it would take

1. **A mail sender.** GitHub Actions has no built-in "send email" step. Options:
   - An SMTP relay action (e.g. `dawidd6/action-send-mail`) using a real mailbox's SMTP credentials.
   - A transactional email API (SendGrid, Mailgun, Resend, etc.) — usually simpler auth (API key)
     and better deliverability than raw SMTP.
   - Either way, credentials get stored as **GitHub Actions Secrets** on the repo — someone has to
     own creating/rotating them.

2. **A recipient list.** Static list of emails, stored either as a repo secret (`NOTIFY_EMAILS`) or
   a checked-in file (e.g. `docs/security/notify-list.txt`). Checked-in is easier to maintain as
   people join/leave the team, but it's then visible to anyone with repo read access — worth deciding
   which matters more here.

3. **A trigger condition.** The workflow already knows when SAST/DAST fail. The email step would run
   `if: failure()` on those jobs, generate a findings summary (reusing the same report-building logic
   as `docs/security/dast-findings-2026-07-25.md`), and send it — either inline in the email body or
   as an attachment.

4. **Noise control.** This is the main design decision. Today's run failed DAST on Low/Informational
   findings (missing security headers) — if every push re-sends the same 10 findings to everyone
   until they're fixed, that trains people to ignore the emails fast. Worth deciding:
   - Only email on **new** findings (vs. previously known ones), not every run.
   - Only email above a chosen severity (e.g. Medium+), consistent with whatever the pipeline's
     pass/fail gate ends up being.
   - Batch to a digest (e.g. once daily) rather than per-push.

## Recommendation (for discussion, not decided)

Start narrow: email only on **new Medium+ findings**, sent to a small, explicitly-owned recipient
list, using a transactional email API (simpler secret than SMTP). Expand scope later once the team
has seen how noisy it actually is in practice.

## Open questions for the team

- Who owns the mail-sending credentials?
- Is a checked-in recipient list acceptable, or does it need to live in a secret?
- What severity threshold should trigger an email vs. just showing up in the Actions UI?
- Email, or would a Slack/Teams webhook fit the team's existing workflow better?
