---
name: ui-verify
description: >-
  Adversarial end-to-end UI and visual verification workflow for the Astryx
  frontend in WSL2. Use whenever adding, redesigning, or polishing frontend
  components, themes, dialogs, drawers, or chat timeline views before claiming
  completion.
---

# Adversarial UI Verification Runbook

Run this workflow whenever frontend UI, layout, Astryx theme tokens, or interactive cards change.

## 1. Static & Automated UI Gates (WSL2 Linux PATH Mandatory)

1. Prefix every Node/Astryx shell call with `export PATH="$HOME/.local/bin:$PATH"`.
2. Run typecheck, Vitest, production bundle, and Astryx doctor:
   ```bash
   export PATH="$HOME/.local/bin:$PATH" && cd frontend && npm run typecheck && npm test -- --run && npm run build && npx astryx doctor --built .
   ```
   - **Vitest `ChatComposer` note**: Astryx `<ChatComposer>` renders a `contentEditable` `<div role="textbox" aria-label="Message input">`, not `<input>`/`<textarea>`. Assert draft text with `toHaveTextContent(...)`, not `getByDisplayValue(...)`.
3. Run Astryx JSX purity grep and Impeccable anti-pattern detector:
   ```bash
   (grep -rnE '<(div|span|svg)\b|style=\{\{|className=|dangerouslySetInnerHTML' --exclude-dir=__tests__ frontend/src/components/ frontend/src/App.tsx || echo "ZERO_VIOLATIONS") && /home/abyss/.gemini/config/skills/impeccable/scripts/impeccable detect --json --changed
   ```
   Both must return `ZERO_VIOLATIONS` and `[]`.

## 2. Live Browser E2E & Adversarial Screenshot Audit

1. **Seed realistic state** into a temporary git repo under `/tmp/e2e-repo-*` and `.data` (include multi-line wrapped labels, long placeholders, active badges, and dirty git diffs to stress layout).
2. **Start the unified server** on port `8000` (`IsDaemon: true`):
   ```bash
   .venv/bin/fastapi run backend/app/main.py --port 8000
   ```
3. **Browser subagent in WSL2**:
   - Reuse an active `webview_browser` subagent tab on `http://127.0.0.1:8000` when continuing same-origin verification (fresh `chrome_devtools` sessions fail in headless WSL2 when `~/.config/google-chrome/DevToolsActivePort` is absent).
   - Move the pointer away (`page.mouse.move(10, 10)`) before capturing selected-state screenshots so hover styling never masks missing selected-row contrast.
   - Capture screenshots across **Dark Mode (`1440x900`)**, **Light Mode (`1440x900`)**, and **Mobile (`390x844`)** when layout/responsive behavior changed.
4. **Visual inspection with `read_file` (Never trust subagent text alone)**:
   - Open every screenshot listed in `BROWSER_HOST_FACTS` via `read_file` and verify:
     - Leading icons and trailing `IconButton`s are un-squished (`<StackItem size="static">`, zero `"Ins..."` button truncation).
     - Keycaps/indices are monochrome (`<Kbd>`), reserving colored `<Badge>` pills strictly for semantic status (`Verified`, `Checks failed`, diff status).
     - `TextInput` placeholders (`Search skills.sh...`, `owner/repo...`, `/path/to/repository`, `Other...`) are completely unclipped (`<StackItem size="fill">` + `<TextInput width="100%">`).
     - Zero horizontal page overflow (`390px` mobile width intact; drawers/menus collapse cleanly) and `0` failed network/console requests.
5. **Teardown & Cleanup**:
   - Kill the background FastAPI server task (`manage_task` `kill`).
   - Remove `.data`, `/tmp/e2e-repo-*`, and any `/tmp/mwg-guide-*.md` scratch files so `git status -s` stays clean.
