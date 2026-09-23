# Web port upstream sync

Merged `coop-deluxe/sm64coopdx` main through
`8cd6e5977d9f920d51ca71f2c61801d019ed79c6` (v1.5.1), bringing in 174 upstream
commits while retaining the Emscripten port.

Integration changes:

- Adapt web event handling, loading screens and mobile text input to upstream's
  runtime-selected SDL backends.
- Keep Emscripten's SDL/GL linking and browser frame scheduling.
- Use a boolean for the SSAO checkbox, matching the updated DJUI API.
- Use explicit floating-point expressions in the new visual-effects shaders so
  they compile under GLSL ES 1.00 / WebGL 1.
- Restore the browser shell's missing `Module` declaration, load PartySocket as
  an ES module, preserve binary payloads, and resolve room roles through the
  PartyKit connection state.
- Workflow updates are preserved in `docs/patches/upstream-web-workflows.patch`
  because the available GitHub credential cannot update Actions workflows. This
  includes upstream native CI changes, the URL injection heredoc fix, a relay
  deployment timeout, and browser-shell checks. Apply with
  `git apply docs/patches/upstream-web-workflows.patch` using a credential with
  workflow access. Existing workflows are unchanged in the pushed merge.

Validation:

- Full web compilation with Emscripten 6.0.5.
- Clean compilation and final incremental compilation with CI's Emscripten 3.1.64:
  `emmake make TARGET_WEB=1 VERSION=us BUILD_DIR_BASE=build/emsdk-3.1.64 -j12`.
- Chromium with a locally supplied US ROM: v1.5.1 menu and castle rendering.
- Two isolated Chromium contexts with a local PartyKit server: host/client role
  assignment, successful game join, and player updates during gameplay.
- `node --test tests/web-shell.test.mjs`: shell syntax and binary transport
  regression checks pass. The saved workflow patch adds these to CI.
- Proposed workflow YAML and shell syntax checks; whitespace checks on merge
  resolutions (upstream/vendor whitespace is preserved).

The public lobby service returned HTTP 400/404 during the local multiplayer
check. Direct room joining worked through the local relay. Public relay/Pages
credentials and configuration are separate from these local runtime checks.
