# Public alpha checklist

The repository can be shared for development and testing. A source release is
different from a finished, broadly validated Windows application.

## Before recommending downloads to general users

- [ ] Verify the Windows CI run for the exact release commit.
- [ ] Run the documented setup on a fresh Windows account/machine.
- [ ] Test GUI extraction end to end: plain ZIP, encrypted ZIP/7z, cancellation,
      wrong password, missing decoder, and low disk space.
- [ ] Measure peak additional allocation on larger generated mixed archives;
      compare with ordinary extraction and publish the actual figures.
- [ ] Review native binary/source correspondence and distribution notices.
- [ ] Create a tagged alpha with clear installation steps and checksums.
- [ ] State the supported/tested subset and destructive behavior prominently.

## Scope limits that must remain explicit

- Multipart RAR5 reclaims during decoding; encrypted headers and multipart
  RAR4 are not supported by the incremental path.
- Streaming RAR5/7z requires the bundled decoder. Other decoders fall back to
  file/group completion and may need room for a whole compression group.
- RAR4, AES ZIP variants, self-extractors and disk-relative split ZIP still need
  dedicated coverage. Do not advertise universal ZIP/RAR support.
- Generic formats do not have incremental reclamation.
- Incomplete streaming ZIP files, RAR groups and 7z blocks cannot resume. Source corruption after
  reclamation is intentional; source restoration is not promised.

## Useful later improvements

An optional standalone package, signed binaries, more multipart RAR variants,
and better peak-space estimates. Power-loss recovery is not a prerequisite
for an honestly described destructive alpha.
