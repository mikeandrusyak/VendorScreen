# Publishing: Submission, Listing, Analytics, Security & Compliance

Everything about getting an app onto the marketplace and keeping it there. See [monetization.md](monetization.md) for pricing specifically.

## Contents
- [Submission process](#submission-process)
- [Listing requirements](#listing-requirements)
- [Security & compliance requirements](#security--compliance-requirements)
- [Analytics available after publishing](#analytics-available-after-publishing)
- [Partner programs](#partner-programs)

## Submission process

**Before submitting:**
1. Read the App Listing Guidelines and the App Review Checklist.
2. Read four legal documents: the Marketplace Listing Terms, Developer Terms, Privacy Policy, and Terms of Service (monday's own, as the platform operator — separate from *your* app's privacy policy/ToS, which you also need — see Security & compliance below).
3. Self-assess validity as a real monday app, your capacity to support users, and ongoing compliance.

**Explicit exclusion, quoted verbatim: "New apps built primarily using no-code platforms or AI-generated 'vibe code' are not eligible for marketplace approval."** This doesn't mean AI-assisted development is banned — it means the app needs to be a genuine, maintainable engineering artifact, not a thin no-code wrapper.

**Submitting:** Developer Center → **Share** tab → Publish app (generates an `auth.monday.com`-prefixed install link) → complete the submission form via the **Submit app** tab.

**Review** happens across 7 checklist categories, each its own doc/checklist page: **app-listing-page, documentation-and-support, legal, partnership, privacy-and-security, product, uiux**. Expect an initial response within **72 business hours**; total time to approval depends on app complexity and how quickly you address review feedback — no fixed end-to-end SLA is published. No formal resubmission waiting period is documented.

**Post-approval** unlocks analytics, review management, and update/versioning tools.

## Listing requirements

| Element | Spec |
|---|---|
| Name | ≤30 chars, no emojis, unique, can't use "monday"/"monday.com" trademarks without endorsement, no implied third-party endorsement |
| Short description | ≤60 chars (shown on marketplace cards — action-verb style recommended: "Add", "Automate", "Visualize") |
| Long description | **200–2,500 characters** per the App Review Checklist page (the separate App Listing Guidelines page says 200–2,000 — the two official pages disagree; aim for the safer, narrower 200–2,000 range unless you confirm otherwise) |
| Keywords | up to 10 |
| Categories | up to 3, from: CRM, Marketing, Project management, Software development, Team management, Productivity & efficiency, Integrations, Collaboration, Reporting & analytics, Import & export, Design & creative, HR, Finance |
| App icon | 192×192px, JPG/PNG |
| Developer icon | 192×192px, JPG/PNG |
| App card image | 592×348px, JPG/PNG |
| Gallery images | 3–5 images, 1920×960px |
| Demo video | HD/4K, MP4, ≤50MB; length is documented as 30–60s on one page and "up to 120 seconds" on another — keep it well under 60s to be safe on both counts |

**Discoverability mechanics worth knowing when writing the listing copy:** marketplace search fuzzy-matches across name, developer name, description, and keywords. "Best Sellers"/"Editor's Choice" placement is awarded monthly based on usability, conversion rate, and review quality — not something you configure directly.

**Support requirement:** provide a public email or feedback URL so users can reach you, plus a website link and a how-to-use page.

**Branding/content rules:** no unlicensed third-party trademarks in icons/screenshots; no unlawful, harmful, or objectionable imagery; original, non-infringing description text.

## Security & compliance requirements

Two distinct things get called "security and compliance" — a mandatory checklist item, and an optional questionnaire for a badge. Don't conflate them.

### A. Mandatory (part of the standard review, `privacy-and-security` + `legal` checklist pages)
- Disclose what user data (board data, username, etc.) your app stores — must be reflected in your privacy policy.
- PII must be stored encrypted; describe your at-rest encryption approach (monday doesn't mandate a specific algorithm, just requires you to state yours).
- Tokens must be encrypted.
- List every third-party domain/product your app touches, front- and back-end, and why — must appear in your privacy policy.
- OAuth scopes: request only what the app actually needs, and describe each scope's purpose.
- **On uninstall/termination: permanently delete all end-user data and related metadata within 10 days.**
- Publicly available **Privacy Policy** URL, under the same entity name you registered with.
- Publicly available **Terms of Service** URL — this link is shown directly on the app's marketplace card, and must state whether you plan to contact users in the future.
- Full legal contact name and company/entity name.

*(This repo already has [`PRIVACY_POLICY.md`](../../../PRIVACY_POLICY.md) and [`TERMS_OF_SERVICE.md`](../../../TERMS_OF_SERVICE.md) — when preparing a submission, cross-check their content against the disclosure requirements above rather than assuming existence alone satisfies the checklist.)*

### B. Optional — Security & Compliance questionnaire (for the Shield Badge only)
Skipping it just shows "No additional information was provided" on your listing — it doesn't block submission. Review takes up to 10 business days if you do submit it. All 19 questions, verbatim:
1. Is customer data segregated from other customers' data (logically or physically)?
2. Process for installing application-level updates and security patches?
3. Mechanism to notify monday.com of a security breach?
4. CSRF protection on all state-changing actions?
5. XSS encoding/sanitization of user-supplied parameters?
6. Least-privilege access to customer data?
7. MFA enforced on employee access to systems that may process customer data?
8. Application logs free of secrets/PII?
9. Protection against mass parameter assignment attacks?
10. Redirects/forwards restricted to approved destinations?
11. GDPR compliant?
12. SOC 2 or SOC 3 certified? (upload required if yes)
13. HIPAA compliant? (upload required if yes)
14. Dedicated security/privacy point of contact?
15. Periodic penetration testing?
16. Where is app data stored?
17. Where is logs data stored?
18. Does the app send data outside monday.com, and is it customer-submitted or not?
19. ISO/IEC 27001:2022 certified? (upload required if yes)

**Gaps explicitly not resolved by the docs:** there's no checklist line item that flatly *mandates* webhook/JWT signature verification (it's clearly implied best practice, not a stated review gate); GDPR compliance is only ever framed as attested-if-you-claim-it (questionnaire item #11 and one Shield Badge route), not as a blanket requirement for baseline approval; no specific encryption algorithm is mandated anywhere.

## Analytics available after publishing

Developer Center tabs, each on its own refresh/window cadence:
- **Sales** (native-monetization apps): Active ARR/MRR, lifetime revenue, plan/pricing-version breakdown, 30-day activity log. Refreshes daily; CSV export on every widget.
- **Installs**: active installs, install/uninstall counts, 7-day recent-installs log (region, tier, account, seats, payment status).
- **Usage**: 30-day unique users/accounts, install→use funnel, 6-month trends.
- **Payment**: install→pay conversion %, days-to-pay stats, 6-month windows.
- **Reviews**: average rating (365-day, paying customers only), full review list, 6-month rating trend. **Public visibility requires ≥5 accumulated reviews** — below that, ratings exist internally but aren't shown on the listing.
- **Listing**: visitor→install funnel, top traffic sources, top search keywords, link-click tracking (contact/website/privacy/ToS/pricing).
- **Google Analytics (GA4)**: paste your GA4 tag ID into the Developer Center's GA tab; data starts flowing within ~3 hours. Tracks 5 custom events beyond GA4 defaults (tab navigation, gallery browsing, resource-link clicks, pricing-period toggle, "Sign in to install" clicks), each tagged with `app_id`.

## Partner programs

**Partner Page** — auto-generated for every partner, no opt-in needed. Shows contact info, categories, KPIs (join date, total installs across your apps, average rating), and your full app portfolio.

**Partner Program** — 4 tiers:
| Tier | Min. revenue (lifetime/ARR) | Min. rating | Support SLA |
|---|---|---|---|
| Bronze | $5K | — | <48h |
| Silver | $75K | 4.0+ | <48h |
| Gold | $300K | 4.0+ | <24h |
| Platinum | $1M | 4.5+ | <24h |
All tiers additionally require: built-in monetization, a completed app review, a submitted security questionnaire, and a signed listing agreement. Gold/Platinum add business reviews and a dedicated Partner Success Manager; Platinum adds bi-weekly reporting and prioritized co-marketing.

**Shield Badge** — one of three eligibility routes: (1) SOC 2 + ISO 27001:2022 + attested GDPR compliance, no hosting restriction; (2) frontend-only app, fully hosted on monday's own infrastructure, never shares customer data externally; (3) backend fully on monday Code with multi-region enabled across all regions, outbound network allowlist activated, no external data sharing, and a CLI-driven storage purge on uninstall. Review up to 10 business days; badge appears within 7 business days of approval; **requires annual reassessment**.
