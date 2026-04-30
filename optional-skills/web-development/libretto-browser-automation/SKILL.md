---
name: libretto-browser-automation
description: Use Libretto, the Saffron Health browser automation workbench, for advanced browser automation debugging, reusable Playwright workflow development, network/action log inspection, and converting UI workflows into durable scripts or direct API calls. Prefer Hermes built-in browser tools for simple browsing; use Libretto when persistent sessions, repeated exec probes, read-only inspection, or trace-driven automation are needed.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [browser, automation, playwright, libretto, debugging, network, cli, web]
    category: web-development
---

# Libretto Browser Automation

Libretto (https://github.com/saffron-health/libretto, MIT) is an AI-oriented browser automation toolkit built on Playwright. It gives coding agents a live browser, named sessions, action and network logs, screenshot/HTML snapshots, DOM condensation, `readonly-exec`, write-capable `exec`, and workflow `run`/`resume` support.

Use it as an **advanced browser automation workbench**, not as the default way to browse the web.

## Decision tree

Use Hermes built-in browser tools when:

- The task is simple page interaction: open, click, type, read, screenshot.
- You only need a quick DOM/screenshot check.
- You do not need persistent browser session state.
- You do not need network/action logs.
- The page may contain sensitive data and a local-only browser interaction is enough.

Use Libretto when:

- Building a reusable browser automation or Playwright script.
- Debugging a broken browser automation against a live page.
- Repeatedly probing a live session with small code snippets.
- Inspecting network/action logs to understand an app workflow.
- Converting a UI workflow into direct HTTP/API calls.
- Recording or reconstructing a human-demonstrated workflow.
- You need explicit read-only vs write-access session modes.

Do **not** use Libretto when:

- The user wants a quick answer from a webpage and Hermes browser tools are sufficient.
- The page contains PHI, passwords, banking data, private chats, or internal dashboards and the user has not explicitly approved snapshot/model-provider exposure.
- The code or website is untrusted and would be run through `libretto exec`.
- You need a multi-tenant sandbox. Libretto `exec` is intentionally local code execution.

## Prerequisites

- Node.js 22+ recommended.
- npm or pnpm available.
- Chromium/Playwright browser installed by `npx libretto setup`.
- For `snapshot`, a configured provider credential for OpenAI, Anthropic, Gemini, or Vertex through Libretto's setup flow.

Install/use without permanently adding to the project:

```bash
npx libretto setup
npx libretto status
```

Project-local install:

```bash
npm install --save-dev libretto
npx libretto setup
```

## Safety rules

1. **Start read-only.** Use `--read-only` for first contact unless the user explicitly asks to perform actions.
2. **Treat `exec` as arbitrary code execution.** Only run code you understand. Do not expose it to untrusted users.
3. **Prefer `readonly-exec` for inspection.** Escalate to write-access only when mutation is necessary.
4. **Warn before `snapshot` on sensitive pages.** Snapshot sends screenshot + DOM to the configured model provider.
5. **Assume telemetry logs may contain sensitive data.** Network/action logs can include URLs, request metadata, typed text, or app data.
6. **Close sessions when done.** Do not leave browser daemons running unnecessarily.
7. **Do not paste secrets into commands.** Use environment variables or existing browser login state.
8. **Keep cloud providers explicit.** Local browser is the default; Browserbase/Libretto Cloud require separate trust decisions.

## Default safe workflow

Create a session name that is short and task-specific:

```bash
SESSION=debug-$(date +%s)
URL="https://example.com"
```

Open read-only:

```bash
npx libretto open "$URL" --session "$SESSION" --read-only
```

Check status and pages:

```bash
npx libretto status
npx libretto pages --session "$SESSION"
```

Inspect without mutation:

```bash
npx libretto readonly-exec 'return await page.title()' --session "$SESSION"

npx libretto readonly-exec '
return {
  url: page.url(),
  title: await page.title(),
  buttons: await page.locator("button").evaluateAll(nodes =>
    nodes.slice(0, 20).map((node, index) => ({
      index,
      text: node.textContent?.trim(),
      disabled: node.hasAttribute("disabled"),
    }))
  ),
}
' --session "$SESSION"
```

Close when finished:

```bash
npx libretto close --session "$SESSION"
```

## Escalating to write access

Only escalate when the task requires mutation: clicking, typing, submitting forms, changing state, or testing an automation.

```bash
npx libretto session-mode write-access --session "$SESSION"
```

Run a small, understood action:

```bash
npx libretto exec '
await page.getByRole("button", { name: /continue|next/i }).click()
return { url: page.url(), title: await page.title() }
' --session "$SESSION"
```

Return to read-only after the action if more inspection is needed:

```bash
npx libretto session-mode read-only --session "$SESSION"
```

## Snapshot workflow

Use snapshot when visual + DOM grounding matters and the page content is safe to send to the configured model provider.

```bash
npx libretto snapshot \
  --session "$SESSION" \
  --objective "Identify stable selectors for the login form and explain the current page state" \
  --context "Prefer accessible selectors and avoid brittle CSS paths."
```

Expected output includes paths to:

- PNG screenshot
- full HTML
- condensed HTML
- model analysis
- suggested selectors

If the page is sensitive, avoid `snapshot` and use local `readonly-exec` queries instead.

## Network/action log workflow

Use this when converting a browser flow into direct API calls.

1. Open in write mode only if actions are required:

```bash
npx libretto open "$URL" --session "$SESSION" --write-access
```

2. Perform the workflow manually or with small `exec` snippets.

3. Inspect recent network log entries from inside `exec`:

```bash
npx libretto exec '
return networkLog({ last: 30 }).map(entry => ({
  method: entry.method,
  url: entry.url,
  status: entry.status,
  resourceType: entry.resourceType,
}))
' --session "$SESSION"
```

4. Inspect recent action log entries:

```bash
npx libretto exec '
return actionLog({ last: 30 }).map(entry => ({
  action: entry.action,
  selector: entry.selector,
  text: entry.text,
  url: entry.url,
}))
' --session "$SESSION"
```

5. Identify the minimal API calls and rebuild the workflow as direct `fetch`/HTTP code if appropriate.

## Workflow runner pattern

For durable integrations, prefer a TypeScript workflow file over a long inline `exec` command.

Create `integration.ts` in a disposable project or task directory:

```ts
import { workflow } from "libretto";

export default workflow(async (ctx) => {
  const page = ctx.page;
  await page.goto("https://example.com");
  await page.getByRole("link", { name: /login/i }).click();
  return { title: await page.title(), url: page.url() };
});
```

Run it:

```bash
npx libretto run ./integration.ts --session "$SESSION" --read-only
```

If a human login or inspection pause is needed, use Libretto's pause/resume support as documented by `npx libretto run --help` and `npx libretto resume --help`.

## Cleanup

Close one session:

```bash
npx libretto close --session "$SESSION"
```

Close all Libretto sessions:

```bash
npx libretto close --all --force
```

Check remaining state:

```bash
npx libretto status
```

Libretto stores session artifacts under `.libretto/` in the workspace. Review before committing and usually add it to `.gitignore`:

```bash
printf '\n.libretto/\n' >> .gitignore
```

## Troubleshooting

- **`No AI model configured`**: run `npx libretto setup` or `npx libretto ai configure <openai|anthropic|gemini|vertex>`.
- **Snapshot fails due missing credentials**: use `readonly-exec` for local-only inspection, or configure the provider explicitly.
- **Multiple pages open**: run `npx libretto pages --session "$SESSION"`, then pass `--page <id>` to commands that require a single page.
- **Session already active**: pick a new session name or close the old session.
- **Read-only blocks action**: this is expected. Switch to write-access only when mutation is intended.
- **Browser daemon left running**: run `npx libretto close --all --force`.
- **Unexpected cloud behavior**: verify provider with `npx libretto status` and use local provider unless cloud was intentional.

## Reference

- Repo: https://github.com/saffron-health/libretto
- Docs: https://libretto.sh/docs
- npm: https://www.npmjs.com/package/libretto
- License: MIT
