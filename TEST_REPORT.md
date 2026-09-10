# Shrink Unzip test report

## Zstandard support update

The current suite passes all 30 tests on Python 3.12 using `backports.zstd==1.7.0`. Seven new cases cover current method 93 with mixed compression, corrupt input, interruption before/after truncation, ZIP64 streaming descriptors, a missing backend, and refusal of legacy method 20. The tested backport did not decode legacy method 20, so that method remains explicitly unsupported.

The user's 53.40 GB archive now passes the read-only format/space preview. Three small method-93 entries were fully decompressed in memory with CRC validation; archive size and modification time were unchanged. This is not a full archive-integrity test or a destructive extraction. See `zstandard-test-results.json` for details. The existing per-file space requirement is unchanged.

The following stress measurements describe the earlier non-Zstandard fixture and remain historical results.

Tested on Windows on September 11, 2026. All 23 regression tests and all three CLI stress scenarios passed. No existing user archives were used.

## Generated archive

- ZIP: **160.49 MiB**; extracted contents: **224.33 MiB**.
- 326 files and two explicit directory entries.
- Two 80 MiB pseudorandom binary files, a 64 MiB compressible log, 320 configuration files, Unicode/Thai/Japanese/emoji filenames, a deeply nested path, an empty file, and an empty folder.
- Stored, Deflate, BZIP2, and LZMA compression; streaming data descriptors; forced ZIP64 local headers; custom extra fields, per-entry comments, and a 65,535-byte archive comment.
- Additional regression test forces ZIP64 end records and verifies resume. This does not constitute a greater-than-4-GiB or 50-GB test.

## Actual command-line runs

| Scenario | Result | Time |
| --- | --- | --- |
| Normal extraction | All 326 files matched SHA-256; archive shrank to zero | 10.19 s |
| Force-killed during a large output write, then resumed | All 326 files matched SHA-256 | 15.97 s |
| Force-killed after substantial archive shrinkage, then resumed | All 326 files matched SHA-256 | 10.94 s |

During the normal run, sampled logical storage for the active ZIP, output, temporary file, and recovery directory peaked at approximately **240.67 MiB total**, or **80.18 MiB additional space** above the original ZIP. Keeping the original archive throughout normal extraction would require about **384.82 MiB total**. Thus this fixture showed roughly 64% less additional storage at the sampled peak.

These are sampled file-length measurements, not filesystem allocation measurements or a hard disk quota test. Samples spanning archive truncation are discarded; short peaks can still be missed. The mathematical per-file peak, excluding recovery data and filesystem overhead, is 80.08 MiB additional. The utility's preview also requires its 64 MiB reserve and per-entry overhead, so it deliberately asks for more free space than the measured file lengths consume. Low-space behavior was tested with injected disk-space results and write failures, not by filling the user's drive.

## Fixes prompted by testing

- Recover if interrupted before the first mutable journal is committed, using an immutable bootstrap record.
- Resume an empty ZIP after interruption during finalization.
- Verify a SHA-256 checksum on saved ZIP-directory metadata before resuming.
- Validate completed-entry counts before using them as offsets.
- Refuse hardlinked input files, which would otherwise truncate every alias.
- Reject implicit directory case collisions, payload-bearing directory entries, NUL/backslash filenames, and additional reserved Windows device names.
- Explicitly reject split-archive entries.
- Make command-line output tolerate filenames that the current terminal encoding cannot represent.

## Other checks

Preview leaves the archive unchanged. Tests cover corrupt input, modified completed output, an unexpected ZIP size, insufficient free space, partial write failure, concurrent invocation, symlink entries, file/directory conflicts, and every before/after journal boundary in a small archive. An early test-fixture issue was corrected: Python normalizes unsafe names while writing, so the hostile-name tests now patch actual on-disk filename bytes.

## Remaining limits

Space is reclaimed only after each complete file. No power-loss, hardware-failure, real disk-full, or 50-GB validation was performed. Timestamps and permissions are not restored. Recovery cannot reconstruct the original ZIP. Use a disposable copy for your first trial.
