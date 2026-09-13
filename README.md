# PeelZip

Experimental Windows extractor that consumes archive storage as it writes output.
Run `PeelZip.cmd`, or `py shrink_unzip_app.py`. Python 3.10+ and a local NTFS
volume are required for aggressive reclamation. Install dependencies first:

```powershell
py -m pip install -r requirements.txt
```

**Aggressive extraction destroys source data.** This is intentional. A stopped
compressed stream may require another download. Output CRC checking detects
corruption; it cannot restore compressed bytes already reclaimed. Keep the
output if a run fails. Never run a game directly from partially extracted files.

## What actually saves space

| Format/path | When source storage is reclaimed | Current limits |
|---|---|---|
| Single ZIP/ZIP64: stored, Deflate, BZIP2, LZMA, Zstandard | During each file, with bounded read/output buffers | Streaming is for unencrypted entries. Incomplete streaming files cannot resume. |
| ZIP with other native decoder methods or encryption | After each complete file | Needs room for the current file; uncommon methods are not broadly tested. |
| Concatenated `.zip.001` volumes | After each complete file | Password-protected ZipCrypto tested. Traditional disk-relative `.z01` layouts are not verified. |
| Non-solid single-volume RAR5 | After each complete file | Custom decoder offsets required. |
| Solid single-volume RAR5 | After each independent solid group | One giant solid group may need the entire output space. |
| Multipart RAR | At the end | **No reduction in peak extraction space yet.** Legacy naming discovery is implemented; RAR4 decoding is unverified. |
| 7z / `.7z.001` | After each complete compression group | Single giant solid group has the same peak-space limitation. |
| TAR/GZ/ISO/CAB/WIM | Ordinary extraction only | Incremental reclamation is not implemented. Generic aggressive entry point refuses to destroy the source. |

Detection uses file content; a ZIP named `.rar` is treated as ZIP. Unknown
content is not accepted just because its filename looks like an archive.
Self-extracting ZIPs can be identified via the central directory, but are not
part of the tested release scope. RAR self-extractors, repair/recovery records,
missing volumes and damaged-archive salvage are not supported workflows.

## CLI

```powershell
py aggressive_zip.py "D:\Downloads\archive.zip" "D:\Downloads\output"
py aggressive_rar.py "D:\Downloads\archive.rar" "D:\Downloads\output" --verify
py aggressive_7z.py "D:\Downloads\archive.7z" "D:\Downloads\output" --verify
```

ZIP verifies CRC/size during streaming and rereads the result by default.
`--no-verify` skips the extra ZIP output reread. RAR/7z `--verify` adds an output
CRC pass to native decoder validation. These options do not change reclaimed
range boundaries. Passwords use `--password`; the GUI masks its log but a CLI
password may be visible to local process-inspection tools.

`--resume` rechecks completed ZIP files. An incomplete streaming ZIP entry is
explicitly refused. RAR/7z journals are limited progress records, not reliable
power-loss recovery. Do not substitute a new download under an old journal or
reuse an existing destination for an unrelated archive.

## Space accounting

For a 50 GiB archive expanding to 70 GiB, final net growth is about 20 GiB.
Peak extra space depends on the extraction order, compression ratios, the
largest file/group, and filesystem allocation. It is not automatically 20 GiB.
Source and output should be on the same volume for reclamation to fund output.

ZIP and RAR results distinguish requested `reclaimed_bytes` from
`allocated_bytes_freed`, measured using the Windows file-allocation API. Small
ranges may free no allocation units. Source logical size generally stays the
same; Explorer's ordinary Size field does not show sparse-file savings.

Conservative ZIP preview remains available. Aggressive preview is not yet
implemented. Generic extraction and multipart RAR need full output space.

## Verification and project status

```powershell
py -m pip install pytest
py -m pytest -q
```

See [AUDIT.md](AUDIT.md) for the review, actual tests and remaining limitations.
See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for public-alpha readiness and
[CONTRIBUTING.md](CONTRIBUTING.md) for development and bug-report instructions.
The suite uses disposable temporary archives and real Windows reclamation.
RAR fixture generation requires WinRAR's encoder; CI skips that fixture when
it is unavailable. Original scripts remain for compatibility, but the desktop
uses `aggressive_zip.py` for ZIP. No release should claim universal support.

The Python code is MIT licensed. The bundled decoder has separate licenses and
a source snapshot in [native](native/README.md). This project is an experimental
source checkout, not a signed installer or a production-ready universal extractor.
