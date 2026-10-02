# Hearally brand assets

Decided 2026-09-22. **The mark: two people side by side, two sound waves, wordmark to the right.**

**Tagline:** *Keeps the people on your side close.*

## The colour convention (use it everywhere, not just the logo)

- **Sand `#F6F1E8`** (on dark) or **Teal `#1F6F6B`** (on light) = **the account holder**, the person with the Plaud.
- **Amber `#E8934A`** = **the ally**. Use the same amber anywhere the people on your side appear: ally names, ally links, "what your allies heard", the second figure in the mark, the outer sound wave.

**The wordmark is the one exception.** It is a single colour and does not encode the split — the mark beside it carries that. The convention governs the mark and the interface, not the logotype.

## Files

| File | Use |
|---|---|
| `mark.svg` | The mark alone, for dark backgrounds |
| `mark-light.svg` | The mark alone, for light backgrounds |
| `lockup-dark.svg` | Primary lockup with tagline, on ink `#14243A` |
| `lockup-light.svg` | Primary lockup with tagline, on sand |
| `favicon.svg` | Simplified: one outer wave, tighter spacing. Use at 16-32 px |
| `icon-512.svg` | App icon, 512 px, rounded square |
| `lockup-sheet.svg` | Reference sheet: primary, compact, light, icon sizes |
| `palette.css` | Design tokens as CSS custom properties |
| `cognito-managed-login.json` | Cognito Managed Login style (sign-in and sign-up), merged over Cognito's defaults. Colours as `rrggbbaa` |
| `cognito-hosted-ui.css` | *Superseded 2026-10-01* by `cognito-managed-login.json`: the classic hosted UI's stylesheet, kept in case of a rollback |
| `explorations/` | Every direction considered, kept for reference |

## Rules

- **Never stretch the mark.** Scale both axes together.
- **Clear space:** at least the height of the account-holder's head circle on all sides.
- **Below 32 px use `favicon.svg`**, not the full mark: the ally's shoulders close up at small sizes.
- **Wordmark:** `hearally`, one word, all lowercase, 700 weight, a **single colour**. Amber `#E8934A` on ink; teal `#1F6F6B` on sand, because amber on sand is only 2.2:1 and fails as text. Never two-tone, never all-caps, never "HearAlly". In prose the product is **Hearally**, capitalised like any proper noun.
- **Type:** Inter for interface and body. Fraunces for marketing headlines only. Body text 16 px minimum, 18 px in the portal.
- **Flag colour `#B4462F` is muted brick, never siren red.** Nothing in this product should look like an alarm.

## Still to generate

- Raster favicons (16/32/48 PNG or .ico) and a 180 px Apple touch icon, exported from `favicon.svg`.
- Open Graph image, 1200x630, built from `lockup-dark.svg`.
