# Product audit — 2026-09-12

This report supersedes earlier support claims and historical TEST_REPORT.md.
No user game archive was modified during this audit.

## Findings fixed

- ZIP decoder Offset metadata was used as if it were a local-header position.
  Encrypted split-ZIP integration reproduced failure before reclamation. Parse
  the ZIP central directory through a seekable concatenated-volume view instead.
- Filename checks in the GUI could override content detection and omit ZIP
  passwords. Routing now consistently uses content. Unknown content is rejected.
- The main ZIP UI route had lost within-file reclamation. It now streams stored,
  Deflate, BZIP2, LZMA and Zstandard, reclaims only bytes already read into memory,
  and verifies output. It does not copy the archive or keep a second file buffer.
- ZIP payloads are checked against subsequent headers and the central directory,
  not merely against other payload ranges. Out-of-range metadata is refused.
- Output traversal, collisions, file/directory conflicts, links/junctions and
  Windows special names are checked before aggressive extraction. Native member
  selection disables wildcards; RAR/7z use list files to avoid command-length limits.
- Legacy RAR discovery contained a two-argument list.append crash and did not
  accept later legacy volumes. Numbered discovery now checks gaps and duplicates.
- Solid RAR was re-decoding earlier members for each file. A solid group is now
  decoded once and its ranges reclaimed when the group finishes.
- Generic aggressive extraction did not save peak space, merely destroyed the
  archive after full extraction. It is explicitly unavailable rather than
  presented as a low-space feature. Ordinary extraction remains separate.
- GUI could start twice before its worker assigned the process. Running state
  now blocks this race. Stop terminates the Windows child process tree. The final
  file no longer sets the progress bar to 100% before extraction succeeds.
- Missing renamed launcher, cache ignore rules, Python license, decoder license
  files/source snapshot and Windows CI configuration are supplied.
- Empty ZIP recovery raised the wrong exception type for a negative seek;
  the existing regression now passes. Zstandard dependency was installed so
  those tests actually ran instead of being silently skipped.

## Verification

47 tests and 28 subtests passed locally before final documentation updates.
Tests include real NTFS reclamation and byte-equal outputs for all five streaming
ZIP methods; reclamation is asserted to happen before the large output finishes.
Also tested actual encrypted concatenated ZIP and encrypted split 7z extraction,
real solid/non-solid RAR5, completed ZIP resume, interrupted-stream refusal,
unsafe paths, and misleading filenames. Historical tests are included.

## Remaining limitations / release gates

- Multipart RAR5 now uses a header-preserving physical payload map and releases
  parts after each file/solid group. Encrypted headers and multipart RAR4 remain
  unsupported; a single large solid group still cannot lower peak storage.
- RAR4, encrypted multipart RAR with hidden names, true disk-relative split ZIP,
  AES ZIP variants, self-extractors, and uncommon codecs need dedicated fixtures.
- RAR/7z recovery journals do not bind content identity or guarantee restart after
  partial reclamation. Their presence must not be advertised as crash safety.
- 7z reclamation remains at compression-group boundaries. Exact peak-space
  preview and per-byte decoder progress for native methods are still absent.
- Ordinary TAR/GZ/ISO/CAB/WIM extraction is not an aggressive storage-saving
  feature. TAR.GZ currently produces the intermediate TAR via 7-Zip.
- Native source snapshot is supplied; bit-for-bit rebuild reproducibility has
  not been verified. Installer packaging, signing and a clean-machine GUI test
  remain release work. CI configuration is added but not claimed remotely passed.

The product remains experimental. The audit improves actual reclamation and
correctness; it does not certify every archive variant or make power loss safe.

## Multipart RAR follow-up — September 13

53 tests and 31 subtests passed locally. Generated four-volume RAR5 fixtures
exercise non-solid, solid, and password-encrypted payload extraction. Non-solid
tests assert reclamation after the first of three files; solid tests assert it
waits for all three members of the group. Output bytes match the original files,
and every source byte outside mapped payloads remains unchanged. Each fixture
freed about 3.1 MiB of physical allocation. This measures source space released,
not peak additional space. Completed-run resume also passed. Missing volumes,
encrypted headers, header CRC damage and inconsistent packed lengths are rejected.
