# Bundled assets

- `igv.min.js` is **embedded in every generated viewer** by `../purple_plotter.py` (checked
  against `IGV_JS_SHA256` when loaded), with its MIT licence (`igv.js.LICENSE`, from the
  igv.js v3.8.3 tag) reproduced in an HTML comment. It includes DOMPurify (Apache-2.0/MPL-2.0),
  whose notice is inside the file.
- The genome files are used only by `../render_image.py`, which serves them in place of the
  network requests the viewer makes, so headless rendering needs no internet access. A viewer
  opened in a browser fetches them from their public URLs.

| File | Replaces | Source |
|---|---|---|
| `igv.min.js` | not requested (inlined into the HTML) | `https://igv.org/web/release/3.8.3/dist/igv.min.js`, unmodified |
| `igv.js.LICENSE` | — | `https://raw.githubusercontent.com/igvteam/igv.js/v3.8.3/LICENSE` |
| `genomes.json` | `https://igv.org/genomes/genomes3.json` | **edited**: hg38 entry only; removed `twoBitURL`, `tracks`, `hubs`, MANE/RefSeq URLs; `format` is `chromsizes` and `fastaURL` is the UCSC `hg38.chrom.sizes` URL below, so igv.js reads chromosome sizes only and never requests sequence. It must not be an igv.org URL: igv.js replaces any igv.org hg38 `fastaURL` with UCSC's `hg38.2bit`. It must be on a host the viewer's CSP allows, because the CSP is checked before the renderer's routing sees the request |
| `hg38.chrom.sizes` | `https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.chrom.sizes` (routed, never fetched) | `https://igv.org/genomes/data/hg38/hg38.chrom.sizes` |
| `cytoBandIdeo.txt.gz` | `https://igv.org/genomes/data/hg38/cytoBandIdeo.txt.gz` | same URL |
| `hg38_alias.tab` | `https://igv.org/genomes/data/hg38/hg38_alias.tab` | same URL |

Consequence: the rendered whole-genome image has no "Refseq All" gene track
(the live viewer draws it as a dense band at genome scale).

When igv.js is bumped: replace `igv.min.js` and `igv.js.LICENSE`, update `IGV_JS_SHA256` and
`IGV_JS_VERSION` in `purple_plotter.py`, check the new file still contains no `<script`,
`</script` or `<!--` (the renderer refuses it otherwise), re-check `genomes.json` against the
new release's `genomes3.json`, re-run `tests/test_csp.py` (including with `PP_LIVE_NETWORK=1`),
and record the new `--print-script-hashes` values. The CSP hashes are computed automatically. Any request that is not covered makes the
renderer fail with the URL, rather than silently producing a partial image.

## SHA-256 (as committed)

```
c092fd7f0c1d0a3a543d1d826b3ae07a55bc9c8a9818580232ff9c8f13c60865  cytoBandIdeo.txt.gz
046c207ec5b5688f848455f283b7967f252d0ef30c01710dad14d3c76541bafe  genomes.json
dfec81d848cec5fe6236eaa490637515df0f38842429e2cdb588d4a1d1394799  hg38_alias.tab
e1d9152418038457a959e949d99c9caf7ae3e4f87cfbb4ffe7d8b9a54ba1202b  hg38.chrom.sizes
fd69572836ffc3e7e17d19177bcd55c62c74c5c8a3db0d7a5f867759d7f9d443  igv.js.LICENSE
acf4ee99cdc91be884203930db9c395bc86267552933b483da0b609c16ea05aa  igv.min.js
```
