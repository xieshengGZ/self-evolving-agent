# GitHub publishing checklist

Repository: `xieshengGZ/self-evolving-agent`

## About

Use this description:

> A self-hosted AI agent that operates Ubuntu, rewrites its own source, and restarts itself. FastAPI + DeepSeek + SQLite.

Recommended topics:

```text
ai-agent autonomous-agents self-evolving self-hosted linux deepseek llm fastapi python devops agent-framework sqlite
```

Upload `docs/assets/social-preview.png` as the repository social preview in **Settings → General → Social preview**.

## Release

The tag is `v1.2.0`; the release notes are in `docs/releases/v1.2.0.md`.

With GitHub CLI authenticated:

```bash
gh release create v1.2.0 \
  --title "v1.2.0 — Stable self-hosted agent foundation" \
  --notes-file docs/releases/v1.2.0.md
```

Or create the release in the GitHub UI from tag `v1.2.0` and paste the same notes.

## Launch order

1. Confirm CI is green and the install script works on a clean Ubuntu VM.
2. Create the release and upload the social preview.
3. Record or refresh the 30-second demo; replace `docs/assets/demo.gif` if the UI changes.
4. Publish a technical post, then share it on Show HN / Reddit / V2EX.
5. Reply to install failures within 24 hours and turn them into README fixes.
