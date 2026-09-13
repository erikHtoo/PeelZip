# Bundled decoder

`../tools/7zz.exe` is a modified 7-Zip decoder. Its licenses are included here;
the PeelZip MIT license does not replace them. The RAR decoder carries the
upstream unRAR restriction.

`7zip-source.zip` contains the current local source tree (Asm, C, CPP, DOC),
including the custom Offset properties in the ZIP, RAR3, RAR5 and 7z handlers.
It also includes the opt-in RAR5/7z read-consumption protocol, input write sharing,
and disabled output preallocation during streaming. `streaming.patch` shows
these changes against PeelZip's previous source snapshot.
This is a source snapshot, not a claim of bit-for-bit reproducibility.
PeelZip no longer relies on the custom ZIP Offset property: its local-header
length proved unreliable. ZIP offsets come from the central directory.

Build from CPP/7zip/Bundles/Alone2 with MSYS2 UCRT64 GCC and mingw32-make:

```
mingw32-make -f makefile.gcc O=build IS_X64=1 PROG=7zz -j4
```

Run the extraction test suite before replacing tools/7zz.exe with a new build.
Binary SHA-256: 86b4daf5e1fddf82c6e0f204528a551afbef4e31f36d7157c586d861fe502444

The Python parent sets a per-run token and selected 7z payload bounds in the
child environment. Decoder read callbacks report consumed positions and wait
for an acknowledgment; Python validates the range, punches it, then acknowledges.
Without this environment, the binary retains ordinary 7-Zip behavior. Native
checksums still run; reclaimed source cannot be recovered after a checksum error.
