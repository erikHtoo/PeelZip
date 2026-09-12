# Bundled decoder

`../tools/7zz.exe` is a modified 7-Zip decoder. Its licenses are included here;
the PeelZip MIT license does not replace them. The RAR decoder carries the
upstream unRAR restriction.

`7zip-source.zip` contains the current local source tree (Asm, C, CPP, DOC),
including the custom Offset properties in the ZIP, RAR3, RAR5 and 7z handlers.
This is a source snapshot, not a claim of bit-for-bit reproducibility.
PeelZip no longer relies on the custom ZIP Offset property: its local-header
length proved unreliable. ZIP offsets come from the central directory.

Build from CPP/7zip/Bundles/Alone2 with MSYS2 UCRT64 GCC and mingw32-make:

```
mingw32-make -f makefile.gcc O=build IS_X64=1 PROG=7zz -j4
```

Run the extraction test suite before replacing tools/7zz.exe with a new build.
Binary SHA-256: 53f45265235457ae50dbcb03e3ccd74e274d3ad39cb43e79a0c09f20075d4842
