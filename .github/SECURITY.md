# Security policy

## Supported versions

The public surface is the current default branch and the `rust` branch it ships from. There are no
maintained release tags: build from the tip of the branch you use. The retired Python reference
implementation on `main` is not supported.

## Reporting a vulnerability

Do not open a public issue. Report privately through GitHub's private vulnerability reporting:
open the repository's **Security** tab and choose **Report a vulnerability**, or go directly to
<https://github.com/lmbullo/cact/security/advisories/new>.

Include the affected commit, a minimal reproduction, and the impact you can demonstrate. If the
private reporting flow is unavailable, contact the maintainer [@lmbullo](https://github.com/lmbullo)
privately on GitHub before disclosing anything.

You can expect an acknowledgement within seven days. Please leave time for a fix before public
disclosure.

## Scope

In scope:

- An API key, bearer token or private repository content that cact writes to a log, a card, the
  journal, the LLM reply cache or a commit.
- A way for a tool to mutate the tree or the repository outside the edit journal, or to escape the
  `exec`/`git` gating.
- A path traversal in `read`, `search`, `map`, `outline`, `edit`, `splice`, `write`, `apply` or the
  Python cell.
- A TUI server or socket endpoint that accepts an unauthenticated caller beyond its documented
  local scope, or that serves state outside its workspace.
- A dependency or build step that executes untrusted code at install time.

Out of scope by design:

- The agent runs commands with your privileges and edits the working tree in place. There is no
  sandbox; prompt injection that makes it run a command you gave it access to is the documented
  model, not a vulnerability.
- Model quality, cost, refusals and the model choosing a bad plan.
- Anything that needs a local attacker who can already write to your state directory or
  `~/.config/code-tui/env`.

## Handling secrets

`DEEPSEEK_API_KEY` and any `DAYTONA_API_KEY` are read from the environment or
`~/.config/code-tui/env`. They are never read from a tracked file in this repository. If you find
one committed anyway, treat it as compromised, rotate it first, then report.
