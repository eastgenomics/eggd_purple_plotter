# Bundled assets for headless rendering

Used only by `../render_image.py`, which serves these in place of the network
requests the generated HTML makes, so rendering needs no internet access.
`igv.min.js` is MIT-licensed (igv.js 3.8.3, includes DOMPurify under Apache-2.0/MPL-2.0).
The viewer HTML itself still loads these from their public URLs at view time.

| File | Replaces | Source |
|---|---|---|
| `igv.min.js` | `https://igv.org/web/release/3.8.3/dist/igv.min.js` | same URL, unmodified |
| `genomes.json` | `https://igv.org/genomes/genomes3.json` | **edited**: hg38 entry only; removed `twoBitURL`, `tracks`, `hubs`, MANE/RefSeq URLs; `fastaURL` points at a dummy host and `format` is `chromsizes`, so igv.js reads chromosome sizes only and never requests sequence |
| `hg38.chrom.sizes` | dummy `assets.invalid/hg38.chrom.sizes` | `https://igv.org/genomes/data/hg38/hg38.chrom.sizes` |
| `cytoBandIdeo.txt.gz` | `https://igv.org/genomes/data/hg38/cytoBandIdeo.txt.gz` | same URL |
| `hg38_alias.tab` | `https://igv.org/genomes/data/hg38/hg38_alias.tab` | same URL |

Consequence: the rendered whole-genome image has no "Refseq All" gene track
(the live viewer draws it as a dense band at genome scale).

When the igv.js version in `purple_plotter.py` is bumped, replace `igv.min.js`,
the `CDN_IGV` constant in `render_image.py`, and re-check `genomes.json` against
the new release's `genomes3.json`. Any request that is not covered makes the
renderer fail with the URL, rather than silently producing a partial image.

## SHA-256 (as committed)

```
c092fd7f0c1d0a3a543d1d826b3ae07a55bc9c8a9818580232ff9c8f13c60865  cytoBandIdeo.txt.gz
926567e775420c5e7ad1a8cfdcbd8c48b9f15691e7fa3e5d35856c63d779545e  genomes.json
dfec81d848cec5fe6236eaa490637515df0f38842429e2cdb588d4a1d1394799  hg38_alias.tab
e1d9152418038457a959e949d99c9caf7ae3e4f87cfbb4ffe7d8b9a54ba1202b  hg38.chrom.sizes
acf4ee99cdc91be884203930db9c395bc86267552933b483da0b609c16ea05aa  igv.min.js
```
