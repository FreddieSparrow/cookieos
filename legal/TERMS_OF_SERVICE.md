# CookieOS Terms of Service

**CookieHost UK** — Registered in England and Wales
Contact: support@techtesting.tech | Effective: 2026-04-09

---

## Agreement

By downloading, installing, or using CookieOS, CookieAI, CookieCloud, or any associated software ("CookieOS"), you agree to these Terms of Service ("Terms"). If you do not agree, do not use the software.

These Terms are governed by the laws of **England and Wales**.

---

## 1. Versions and Feature Access

### 1.1 Open-Source Community Edition

The Community Edition is free and open-source. It includes:

- Full CookieOS desktop, mobile, and server software
- CookieAI chat and image generation (local models)
- CookieCloud self-hosted sync
- Content safety filters
- Community support via GitHub Issues

**Excluded from Community Edition:**
- Persistent AI memory
- Automatic update installation (notifications only)
- Enterprise fleet management
- Priority model routing
- Commercial SLA support

### 1.2 Enterprise Subscription

Enterprise features require a valid licence key from CookieHost UK. Contact `support@techtesting.tech` for pricing.

Enterprise features include everything in Community plus:
- Persistent conversation memory across sessions
- Automatic update installation (12.7-hour check interval)
- Fleet-wide policy management over Tailscale
- White-labelling
- 30-day email/chat SLA support
- Priority access to new features

**Enterprise licences are non-transferable** and tied to the purchasing organisation.

### 1.3 Feature Toggles

Some features (e.g., persistent memory, auto-install updates) are script-controlled and disabled by default. Enabling them does not grant rights beyond your current licence tier.

---

## 2. Acceptable Use

You **may** use CookieOS to:

- Run local AI inference on your own hardware
- Generate, edit, and manage content on your own devices
- Build personal tools and automations
- Integrate with self-hosted services
- Conduct legitimate security research and penetration testing on systems you own or have explicit written permission to test

You **must not** use CookieOS to:

1. Generate, distribute, or store Child Sexual Abuse Material (CSAM) — **criminal offence under the Protection of Children Act 1978 and Sexual Offences Act 2003**
2. Plan, facilitate, or carry out acts of terrorism or political violence — **Terrorism Act 2000**
3. Produce instructions or plans for weapons of mass destruction — **Chemical Weapons Act 1996, Biological Weapons Act 1974**
4. Stalk, harass, or threaten individuals — **Harassment Act 1997, Protection from Harassment Act 1997**
5. Violate others' intellectual property rights
6. Circumvent export control laws (AI models may be subject to EAR/OFAC restrictions)
7. Conduct non-consensual surveillance
8. Engage in fraud or financial crime

---

## 3. Privacy and Data

### 3.1 What CookieOS Does NOT Do

- Phone home to CookieHost UK or any third party
- Send your prompts, queries, or generated content to external servers
- Collect analytics or crash reports to remote servers
- Use Google services (Firebase, Analytics, Play Services) in CookieOS software

### 3.2 What Stays On Your Device

- All AI inference (Ollama, Gemma, Fooocus)
- Chat history and memory
- Generated images and files
- Audit logs of safety filter events

### 3.3 Optional External Services

If you enable:
- **CookieCloud sync** — data syncs to your self-hosted Nextcloud. CookieHost UK does not host or access this data.
- **Tailscale** — subject to Tailscale's own Privacy Policy
- **n8n automation** — local instance, no data leaves your network unless you configure external webhooks

### 3.4 CRITICAL Incident Reporting

CSAM and WMD-related detections create anonymised incident logs (content-hashed, no raw content stored) which may be shared with law enforcement as described in `LEGAL_DISCLAIMER.md`.

---

## 4. OpenClaw and Experimental Features

Some CookieOS components are marked as experimental ("OpenClaw", unverified web browsing, etc.). These features:

- Are provided with **no warranty**
- May produce unexpected, inaccurate, or potentially harmful outputs
- Should not be used in production or safety-critical environments without independent verification
- Are your responsibility to test before deployment

---

## 5. Warranties and Limitation of Liability

**THE SOFTWARE IS PROVIDED "AS IS."** TO THE MAXIMUM EXTENT PERMITTED BY UK LAW:

- CookieHost UK makes **no representations** about fitness for purpose, accuracy, or reliability
- CookieHost UK is **not liable** for any damages arising from use, including data loss, system compromise, or AI-generated errors
- Our total liability to you for any claim shall not exceed the amount you paid for Enterprise services in the 12 months prior to the claim

Nothing in these Terms limits liability for death or personal injury caused by negligence, fraud, or any other liability that cannot be excluded under UK law.

---

## 6. Intellectual Property

### 6.1 Open-Source Components

CookieOS source code is licensed under the terms in `LICENSE`. Third-party components retain their own licences.

### 6.2 AI Model Licences

CookieOS uses AI models that are subject to their own licences:
- **Gemma 4** — Google Gemma Terms of Use
- **Fooocus** — GPL-3.0 / based on Stable Diffusion (CreativeML Open RAIL-M)

You are responsible for compliance with these model licences, particularly for commercial use.

### 6.3 Content You Generate

You own content you generate using CookieOS, subject to the underlying model licences above.

---

## 7. Updates and Maintenance

CookieHost UK may release updates via the public GitHub repository. We do not guarantee:
- Any specific update cadence
- Backwards compatibility between major versions
- Continued support for any specific feature

Enterprise subscribers receive advance notice of breaking changes and a support migration window.

---

## 8. Termination

We may suspend or terminate your Enterprise licence if you breach these Terms. On termination, you must delete Enterprise licence keys. Community Edition use is subject only to the open-source licence.

---

## 9. Contact and Disputes

- **General**: support@techtesting.tech
- **Security vulnerabilities**: security@techtesting.tech
- **Legal / compliance**: legal@techtesting.tech

Disputes will be resolved through good-faith negotiation first. If unresolved, disputes are subject to the exclusive jurisdiction of the courts of England and Wales.

---

## 10. Changes to These Terms

We will announce changes to these Terms via:
- GitHub repository (CHANGELOG.md)
- CookieCloud update notifications (if enabled)

Continued use after the effective date constitutes acceptance. If you do not agree to updated Terms, discontinue use.

---

*CookieHost UK — "Your hardware. Your data. Your OS."*
