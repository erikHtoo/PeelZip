# PeelUnzip — experimental Windows CLI

Extract a local ZIP into a new folder on the same volume, reclaiming archive space after each complete file. Python 3.10 or later. Zstandard ZIP support is built into Python 3.14+; older Python versions need the dependency below. Tested on Windows using disposable archives, not by destructively extracting a 50 GB production archive.

## Install Zstandard support

```powershell
py -m pip install -r requirements.txt
```

Run this from the folder containing the script and requirements file. It installs `backports.zstd` on Python 3.10–3.13. Python 3.14+ uses its standard library. Older compression methods still work without installing the dependency. Use the same Python environment to install and run the tool.

## Preview first

From a terminal in the folder containing `shrink_unzip.py`:

```powershell
py shrink_unzip.py "D:\Downloads\large.zip" "D:\Downloads\extracted"
```

Preview does not modify the ZIP or create output. It reports the archive size, output size, available disk space, and estimated peak additional space required. Numbers use GiB. The destination must not exist; its parent must already exist.

## Extract and consume the ZIP

```powershell
py shrink_unzip.py "D:\Downloads\large.zip" "D:\Downloads\extracted" --execute --accept-data-loss-risk
```

**This destroys the original ZIP as extraction proceeds.** Try a disposable copy first. Do not use an irreplaceable archive as your first test. Keep the computer powered, and do not edit the archive, destination, or recovery records during extraction. Use a local disk, not a cloud-synced folder or network share. The tool prevents concurrent runs of itself; it cannot prevent other applications from changing these files.

First the tool reads and checks every entry's CRC without extracting it. It then saves the ZIP directory in a small sibling `.shrink-state` folder. It extracts entries in reverse physical order, writes each file into a temporary file, flushes it, reads it back to check its size and CRC, moves it to the destination, records completion, and truncates the ZIP at that entry's original start. Each step frees the completed entry's compressed storage. CRC detects accidental corruption; it is not a cryptographic authenticity check.

The finished ZIP is an empty placeholder. After inspecting the extracted output, you can delete that placeholder, its `.shrink-state` folder, and its `.shrink-lock` file. Keep recovery records until extraction is complete.

## Resume an interrupted extraction

```powershell
py shrink_unzip.py "D:\Downloads\large.zip" --resume --accept-data-loss-risk
```

Leave the source and destination at their original paths. Resume verifies previously completed output, uses the saved directory to read the shortened ZIP, and continues. A partial temporary file is discarded and its entry is restarted. Once shrinking starts, ordinary ZIP software generally cannot open the remaining archive.

Recovery was tested using forced process termination during a large file and after archive truncation, plus injected failures at journal boundaries. Saved ZIP-directory metadata now has a SHA-256 checksum, and an immutable bootstrap record supports an interrupted first journal commit. Recovery records use version 2; this version refuses older records.

If setup stops before either recovery record exists, the tool has not intentionally truncated the ZIP. Verify the original ZIP with an ordinary archive tool before removing the incomplete recovery folder and retrying. Recovery is not a guarantee against disk failure, filesystem damage, or power-loss write reordering. There is no undo or reconstruction of the original ZIP.

## Space limits

Space is reclaimed **per file**, not midway through a file. For example, with a 50 GB ZIP already occupying disk space and only 30 GB free, extraction to 70 GB may fit when the ZIP contains many smaller files. The final net growth is about 20 GB, but the temporary peak depends on individual file sizes, compression ratios, and physical order. Preview calculates that peak for the archive rather than assuming it fits.

If the ZIP contains a single 70 GB file, this version still needs approximately 70 GB free. Supporting that case requires a different approach using filesystem-specific space reclamation while streaming an entry.

The estimate includes directory metadata, 64 MiB of reserve space, and 8 KiB per archive entry. Filesystem allocation, quotas, snapshots, recycle-bin behavior, or other programs using space can change actual availability. Free space is checked again before each file. The tool stops and preserves recovery records if a write fails.

## Supported scope

- Standard ZIP/ZIP64 with stored, Deflate, BZIP2, LZMA, or Zstandard (method 93) entries. Legacy Zstandard method 20 is rejected because the tested backport does not decode it.
- Regular files and folders. Contents and names are preserved; original permissions, timestamps, and other platform metadata are not restored.
- Rejects encrypted archives, self-extracting/prefixed archives, symlinks, special files, unsafe Windows names, duplicate paths, and file/directory conflicts. Split archives are unsupported.
- No graphical interface yet. Use `python` instead of `py` if that is how Python is installed on your computer.

## Repeat the tests

```powershell
py test_shrink_unzip.py -v
py stress_test.py --work-dir "D:\scratch\new-shrink-test"
```

The regression suite has 30 tests, including fault-injection subcases. Seven Zstandard tests require the backend and otherwise are skipped. They cover mixed methods, corruption, ZIP64 streaming descriptors, interrupted extraction and resume, a missing dependency, and rejection of legacy method 20. The stress script requires a new work directory and creates a 160.5 MiB ZIP expanding to 224.3 MiB, then runs normal extraction and two forced-termination/resume scenarios. Allow about 1 GiB for test files. Each result is independently checked against generated SHA-256 hashes. It leaves the fixture, extracted test outputs, and logs in the chosen directory for inspection.

## Experimental aggressive mode

The desktop launcher is `shrink_unzip_app.py`. Run `py shrink_unzip_app.py` to open a Windows UI with archive/destination pickers, conservative and aggressive mode selection, a space preview button, live file progress, output log, and stop control. Aggressive preview is intentionally disabled; use conservative preview first.

`aggressive_stored_zip.py` is the first NTFS sparse-reclamation path. It extracts `ZIP_STORED` entries in 64 MiB chunks, flushes and journals each chunk, verifies the final CRC, and calls `FSCTL_SET_ZERO_DATA` on the corresponding source range. The source ZIP keeps its logical length but becomes intentionally unreadable in ordinary ZIP tools because reclaimed ranges read as zeros. It can resume after an interruption using the `.aggressive-state` directory.

```powershell
py aggressive_stored_zip.py "D:\test\archive.zip" "D:\test\out" --execute --accept-data-loss-risk
```

The current experimental script accepts only uncompressed ZIP entries. It refuses Deflate, BZIP2, LZMA, and Zstandard rather than pretending that arbitrary compressed offsets are restartable. Its five tests include a real Windows allocation test and an injected interruption/resume test. Zstandard support requires restartable decompression checkpoints before it can safely use this mode.

`aggressive_zstd_zip.py` is a separate higher-reclamation experiment. It streams method-93 Zstandard entries and punches consumed compressed ranges while writing output. It is intentionally destructive: after the first reclaimed range, the source is no longer a valid ordinary ZIP and an interruption may require starting over or redownloading. It handles ordinary read/write/decompression errors by stopping with a clear error; it does not promise power-loss recovery. Use it only on a disposable copy and only after the stored-entry mode has passed on your machine.

The Zstandard aggressive path has two passing tests covering output equality, source logical-length preservation, and injected reclamation failure. It is not yet suitable for the only copy of an archive.

## RAR support status

`rar_backend.py` adds decoder discovery, structured RAR listing, and normal extraction through the bundled native 7-Zip decoder or an installed compatible executable. Aggressive RAR is supported for non-solid, single-volume, unencrypted archives. It extracts one entry at a time, optionally verifies its CRC, then reclaims that entry's packed source range. The source keeps its logical length but is intentionally no longer usable as a normal RAR after reclamation.

Solid, multipart, and encrypted archives are currently rejected by aggressive mode. The desktop UI accepts `.zip` and `.rar` files and shows per-entry progress.

7z detection and ordinary extraction still need to be wired into the UI. Aggressive 7z support requires parsing folder/block boundaries and processing complete solid folders, rather than reclaiming arbitrary byte ranges. Multipart coordination, encrypted archives, and resumable aggressive RAR journals remain future work.

See `TEST_REPORT.md` for measured results and limitations. `realistic-test.zip` is the preserved generated fixture; use a copy when testing destructive extraction.
