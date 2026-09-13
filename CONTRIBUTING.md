# Contributing to PeelZip

PeelZip prioritizes reducing peak disk use. Changes must preserve the exact
output while treating consumed source bytes as disposable.

## Development

Use Windows and Python 3.12 for the current regression baseline:

```powershell
py -m pip install -r requirements.txt pytest
py -m pytest -q
py shrink_unzip_app.py
```

Run destructive tests only with generated disposable archives. Test against
known original contents, and measure physical allocation before and after.
Do not infer disk savings from a counter of requested zeroed bytes.

RAR fixture creation requires the WinRAR encoder; those tests skip when it is
absent. Report skipped tests and the reason. Do not describe them as passing.

## Changes to extraction

Include a regression that reproduces the issue and independently checks output.
For reclamation changes, check the range against headers, other payloads, and
source bounds before modification. For UI launch changes, exercise the command
and prompt flow, not just the underlying decoder function.

Explain when storage is freed: within a file, after a file, after a compression
group, or only at completion. A format that extracts successfully does not
automatically qualify as low-space support.

Avoid archive-sized backups or mandatory recovery copies. Small metadata checks
and journals are welcome when they prevent program errors without compromising
storage savings. Never imply that a journal can reconstruct consumed bytes.

Update README.md when tested capabilities or limitations change. Include native
source and relevant notices when changing the bundled decoder.

## Bug reports

Include Windows/Python versions, the PeelZip commit, detected format, selected
mode, whether the archive is encrypted/solid/multipart, and the final log lines.
State free disk space and the output location's drive. Redact passwords and
personal paths. Prefer a small generated reproducer; do not upload game archives.
