# Pending changelog notes

Pull requests do not edit `CHANGELOG.md` or bump `vendoo-studio/VERSION`. Add one
note file here instead:

```text
changelog.d/<slug>.md          # patch bump (the default)
changelog.d/<slug>.minor.md    # minor bump, only when the seller asks
changelog.d/<slug>.major.md    # major bump, only when the seller asks
```

Use your branch name as the slug (lowercase letters, digits, hyphens), so two
pull requests never pick the same file. The note holds one or more bullets in
the seller's words:

```markdown
- Keep listing-rule loading from freezing the app.
```

After the pull request merges, the **Version** workflow
(`.github/workflows/version.yml`) runs `vendoo-studio/scripts/release-changelog.py`:
each merged pull request's notes become one version, written as the top
`## <version> — <YYYY-MM-DD>` section of `CHANGELOG.md`, and the note files are
deleted. It then starts the macOS release build for that version.
