# Integral app logo — frameless split square

Selected by Eldon Marks on 7 October 2026. Approved on 7 October 2026: the frameless split-square is the canonical Integral app logo. All runtime marks, auth-page motifs, browser icons, and exports use these two halves. See `SPLIT-SQUARE.md` and `size-check.html`.

## Masters

- `integral-logo.svg`: scalable black mark with transparent background and diagonal gap.
- `integral-logo-white.svg`: reversed white mark for dark backgrounds.
- `geometry.json`: current split-square construction.

## Sizes and formats

`png/black/` and `png/white/` contain transparent PNGs at 16, 24, 32, 48, 64, 96, 128, 180, 192, 256, 384, 512, 1024, and 2048 pixels square.

`png/on-white/` and `png/on-black/` contain padded opaque exports at 192, 512, and 1024 pixels square. `preview.png` displays the approved black mark on white.

`icons/` includes 16px/32px PNG favicons, a 512px favicon, a multi-resolution ICO (16/32/48/64/128/256), a 180px Apple touch icon, and 192px/512px Android icons. Touch/Android icons include white backgrounds and clear space.

The app shared LogoMark renders this geometry at its existing 14/22/28/40px sizes, adapting color to the theme. Public favicon and touch assets use the same master.

## Use

Scale uniformly. Preserve the diagonal angle, equal halves, opposing offsets, and rounded ends. The two halves are the entire mark, with no outer frame. The diagonal gap is transparent. Use the white version on dark backgrounds.

## Reproduction

Run `generate-assets.cjs` with a Node runtime containing sharp (set NODE_PATH if needed). The generator validates raster dimensions. The ICO was exported from the 256px black PNG with Pillow. Production exports are rendered directly from the vector; no image generation or tracing is involved.
