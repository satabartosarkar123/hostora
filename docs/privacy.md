# Privacy Notice — Hostora (Non-Commercial Beta)

Hostora helps event hosts estimate attendance and plan food quantities without guesswork or waste.

## 1. What we collect

- **From hosts**: Google account identifier (`uid`), display name, event details, and the household guest list you enter or import.
- **From guests**: attendance status (`yes`, `maybe`, `no`), adult and child counts, meal counts (vegetarian, Jain, non-vegetarian), selected dietary or faith rules, optional allergy tags, and expected arrival window.

## 2. How we protect your data

- **Encrypted phone numbers**: guest phone numbers are encrypted at rest with AES-256-GCM (`PHONE_ENC_KEY`) and decrypted only when the event host prepares a WhatsApp invitation link.
- **Hashed IP addresses**: rate-limiting uses a truncated HMAC-SHA-256 hash of the client IP; raw IP addresses are never stored in the database.
- **No personal data sent to AI**: LLMs never receive guest names, phone numbers, or unredacted free text. Dietary and faith preferences are aggregated into anonymous totals before planning.
- **Venue gate privacy**: gate passes and the offline gate manifest contain only the household display name and seat count — never dietary, faith, allergy, or phone information.

## 3. Retention and deletion

- **Automatic 90-day purge**: 90 days after the last function of an event starts, all households, invitations, RSVPs, gate passes, gate scans, and idempotency records for that event are permanently deleted.
- **Immediate account deletion**: hosts can delete their account and all associated events at any time from **Settings → Delete Account** (`DELETE /v1/me`).
