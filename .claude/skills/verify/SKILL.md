---
name: verify
description: Build/launch/drive recipe to verify webapp changes in the running MyTCG app (server + Playwright).
---

# Verifying MyTCG webapp/server changes in the running app

## Launch

```bash
cd <repo root>
uv run uvicorn src.server.main:app --port 8123 --log-level warning   # background
# app at http://localhost:8123/webapp/  (root / redirects there)
```

No build step — the webapp is plain ES modules served from `src/server/webapp/`.
Remember `scripts/sync_mobile.py` after webapp/engine edits or
`tests/test_mobile_sync.py` fails.

## Drive (Playwright)

No playwright in the repo; install it in the scratchpad (`npm i playwright`,
browsers are already in `~/AppData/Local/ms-playwright`) and run `.mjs`
scripts from there. Use a phone-ish viewport (420x860) — the UI is
mobile-first.

Key selectors / flows:

- Menu: `#btnMenuPlay` starts the favorite mode; `#menuModeRow > *` are the
  1v1/3P/4P/5P chips (clicking one persists it as favorite).
- Match HUD: `#scorePanel` (`.score-side`, `.score-name`, `.score-elo`),
  FFA rival chips `.opp-chip` (`.chip-name`).
- `#btnEndTurn` doubles as "Confirm mulligan" / "End Turn" / "Rematch" —
  poll its text. The AI auto-advances after ~700ms pacing; give the match
  2-3s to settle after start.
- Surrender (fastest way to a game_result): wait for
  `#btnSurrender:not(.hidden)` (hidden until the human's mulligan is
  confirmed), click it, then `#btnSurrenderConfirm`. Game-over overlay:
  `.game-result-overlay`, elo line `.game-result-elo`. Overlay removes
  itself after ~4s; `#btnHome` returns to the menu.
- Profile/progression lives in `localStorage` key `mytcg_profile_v1`
  (fresh browser context = fresh profile, elo 1000, quests locked).
- Log `page.on('console')`/`pageerror` and capture `/api/ai-move` request
  payloads via `page.on('request')` to assert client→server fields.

## Gotchas

- A transient 500 from `/api/ai-move` ("No legal actions available for AI")
  can appear once around mulligan auto-advance; the client toasts and
  recovers. Pre-existing race, not a regression signal by itself.
- Server-side AI behavior is easier to exercise headlessly through
  `GameService.apply_ai_action(...)` or `python -m src.server.ai.arena`
  than through the UI when the UI path isn't the thing under test.
