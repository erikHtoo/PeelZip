# Native streaming measurements — September 14, 2026

Generated NTFS fixtures, using the bundled modified decoder. No downloaded game
archive was used. Each row extracts two 12 MiB files containing incompressible
data (24 MiB output). Split cases use 5 MiB volumes and password encryption.

| Archive | Sampled peak extra allocation | Ordinary output allocation |
|---|---:|---:|
| Solid 7z LZMA2 | 129,303 bytes | 25,165,824 bytes |
| Solid 7z BCJ2 with multiple packed streams | 127,496 bytes | 25,165,824 bytes |
| Split encrypted solid 7z | 129,256 bytes | 25,165,824 bytes |
| Solid 7z with encrypted headers | 129,215 bytes | 25,165,824 bytes |
| Non-solid RAR5 | 1,113,835 bytes | 25,165,824 bytes |
| Solid RAR5 | 959,524 bytes | 25,165,824 bytes |
| Split encrypted RAR5 | 695,638 bytes | 25,165,824 bytes |

Measurements sample source plus output allocation immediately before each
reclamation and at completion, subtracting initial source allocation. They use
the Windows allocation helper. They are **sampled measurements, not a guaranteed
maximum**, and exclude journals and decoder RAM. The ordinary comparison is
final output allocation with the source retained, not a separate timed run.

Every row checks byte-identical outputs, more than 20 MiB reclaimed while the
decoder runs, reclamation before the first file finishes, and completed-run
resume without additional reclamation. Other regressions check preserved RAR
headers, missing volumes, invalid ranges, duplicate read requests and terminating
the decoder when reclamation fails. Mid-stream restart is explicitly refused.

These nearly incompressible fixtures demonstrate removal of the whole-file/group
space requirement. Highly compressed data still needs room for net expansion;
do not extrapolate these numbers to every 50 GiB download. Multi-gigabyte tests
and exact peak prediction remain release work.

Run: `py -m pytest test_native_stream.py test_rar_multipart.py -q -s`
