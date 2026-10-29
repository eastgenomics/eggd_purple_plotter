// eggd_purple_plotter viewer code. Inlined unchanged into every generated HTML, so it is
// byte-identical for every sample and can be pinned by hash in the page's
// Content-Security-Policy. All per-sample values (track data, locus, file name) come from
// the non-executable JSON block (script element type="application/json", id "pp-data"); nothing
// sample-specific is ever written into this code.
"use strict";
const ppData = JSON.parse(document.getElementById("pp-data").textContent);

const covLog2Bg          = ppData.cov_log2_bg;
const barAmpBg           = ppData.bar_amp_bg;
const barGainBg          = ppData.bar_gain_bg;
const barLossBg          = ppData.bar_loss_bg;
const barHodelBg         = ppData.bar_hodel_bg;
const bafBg              = ppData.baf_bg;
const bafMirBg           = ppData.baf_mir_bg;
const bafBarAmpTopBg     = ppData.baf_bar_amp_top_bg;
const bafBarAmpBotBg     = ppData.baf_bar_amp_bot_bg;
const bafBarGainTopBg    = ppData.baf_bar_gain_top_bg;
const bafBarGainBotBg    = ppData.baf_bar_gain_bot_bg;
const bafBarLossTopBg    = ppData.baf_bar_loss_top_bg;
const bafBarLossBotBg    = ppData.baf_bar_loss_bot_bg;
const bafBarHodelTopBg   = ppData.baf_bar_hodel_top_bg;
const bafBarHodelBotBg   = ppData.baf_bar_hodel_bot_bg;
const cnvkitCnrBg        = ppData.cnvkit_cnr_bg;
const cnvkitSnsBg        = ppData.cnvkit_sns_bg;
const cnvkitGenemetricsBg = ppData.cnvkit_gm_bg;
const segData            = ppData.seg_data;

// Click popups. igv.js builds them as HTML from raw values, so a sample ID such as
// "S<b>x</b>" in a SEG track would become markup (the CSP stops scripts, not markup).
// For the tracks built from this file's embedded data (seg, wig, merged) every value is
// HTML-escaped here, in the same layout igv.js uses. Other tracks (the genome's RefSeq
// annotation, whose popups contain igv.js's own links and separators) keep igv.js's popup.
const PP_DATA_TRACK_TYPES = new Set(["seg", "wig", "merged"]);
function ppEsc(v) {
  return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function ppPopup(track, data) {
  if (!track || !PP_DATA_TRACK_TYPES.has(track.type)) return undefined;   // undefined: igv.js default popup
  if (!Array.isArray(data) || data.length === 0) return false;           // false: no popup
  return data.map(function (d) {
    if (d === "<hr>" || d === "<hr/>") return "<hr>";
    if (d && typeof d === "object" && d.name !== undefined) {
      return '<div title="' + ppEsc(d.value) + '"><span>' + ppEsc(d.name) + "</span>&nbsp;&nbsp;&nbsp;" + ppEsc(d.value) + "</div>";
    }
    const text = d && typeof d === "object" ? (d.html !== undefined ? d.html : JSON.stringify(d)) : d;
    return "<div>" + ppEsc(text) + "</div>";
  }).join("");
}

// Blob URLs whose data is empty (input not supplied). igv.js throws while
// parsing an empty SEG file and aborts createBrowser, leaving a blank viewer,
// so tracks backed by empty data are removed before the browser is created.
const emptyBlobs = new Set();
function blob(txt) {
  const url = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
  if (!txt.trim()) emptyBlobs.add(url);
  return url;
}

function pruneTracks(list) {
  return list.map(function (t) {
    if (t.tracks) {
      const kept = t.tracks.filter(function (c) { return !emptyBlobs.has(c.url); });
      return kept.length ? Object.assign({}, t, { tracks: kept }) : null;
    }
    return emptyBlobs.has(t.url) ? null : t;
  }).filter(Boolean);
}

igv.createBrowser(document.getElementById("igv-div"), {
  genome:    "hg38",
  locus:     new URLSearchParams(location.search).get("locus") || ppData.locus,
  showRuler: true,
  showCursorTrackingGuide: true,
  tracks: pruneTracks([

    // ── 1. Coverage scatter + CN log₂ bars ──────────────────────────────────
    {
      type: "merged", name: "Coverage + CN (log₂)", height: 200,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1, alpha: 0.6,
      tracks: [
        { type: "wig", format: "bedgraph", url: blob(barAmpBg),
           graphType: "bar", color: "#08306b", altColor: "#08306b",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(barGainBg),
           graphType: "bar", color: "#93c4e0", altColor: "#93c4e0",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(barLossBg),
           graphType: "bar", color: "#fca5a5", altColor: "#fca5a5",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(barHodelBg),
           graphType: "bar", color: "#8b0000", altColor: "#8b0000",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(covLog2Bg),
           graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6,
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
      ],
    },

    // ── 2. PURPLE CNV segments (log₂ SEG) ───────────────────────────────────
    {
      name: " ", type: "seg", format: "seg",
      url: blob(segData), height: 10,
      posColorScale: { low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' },
      negColorScale: { low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' },
    },

    // ── 3. BAF scatter + fitted BAF bars ────────────────────────────────────
    {
      type: "merged", name: "BAF", height: 100,
      min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, alpha: 0.6,
      tracks: [
        { type: "wig", format: "bedgraph", url: blob(bafBarAmpTopBg),   graphType: "bar", color: "#08306b", altColor: "#08306b", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarGainTopBg),  graphType: "bar", color: "#93c4e0", altColor: "#93c4e0", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarLossTopBg),  graphType: "bar", color: "#fca5a5", altColor: "#fca5a5", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarHodelTopBg), graphType: "bar", color: "#8b0000", altColor: "#8b0000", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarAmpBotBg),   graphType: "bar", color: "#08306b", altColor: "#08306b", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarGainBotBg),  graphType: "bar", color: "#93c4e0", altColor: "#93c4e0", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarLossBotBg),  graphType: "bar", color: "#fca5a5", altColor: "#fca5a5", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBarHodelBotBg), graphType: "bar", color: "#8b0000", altColor: "#8b0000", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafBg),            graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6, min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
        { type: "wig", format: "bedgraph", url: blob(bafMirBg),         graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6, min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
      ],
    },

    // ── 4. PURPLE CNV segments (log₂ SEG) ───────────────────────────────────
    {
      name: " ", type: "seg", format: "seg",
      url: blob(segData), height: 10,
      posColorScale: { low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' },
      negColorScale: { low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' },
    },

    // ── 5. CNVkit log₂ scatter (.cnr per-target ratios) ─────────────────────
    {
      type: "merged", name: "CNVkit log₂", height: 150,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1,
      tracks: [
        { type: "wig", format: "bedgraph", url: blob(cnvkitCnrBg), graphType: "points", color: "#756bb1", pointSize: 10, min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
      ],
    },

    // ── 6. CNVkit called segments (.call.cns) ────────────────────────────────
    {
      name: "CNVkit segments", type: "seg", format: "seg",
      url: blob(cnvkitSnsBg), height: 50,
      posColorScale: { low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' },
      negColorScale: { low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' },
    },

    // ── 7. CNVkit gene metrics (.genemetrics.csv) ────────────────────────────
    {
      type: "merged", name: "CNVkit genemetrics", height: 80,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1,
      tracks: [
        { type: "wig", format: "bedgraph", url: blob(cnvkitGenemetricsBg), graphType: "bar", color: "#08306b", altColor: "#8b0000", min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" },
      ],
    },

  ]),
}).then(function (b) {
  b.on("trackclick", ppPopup);
  window.igvBrowser = b;
  window.igvReady   = true;
}).catch(function (e) {
  window.igvError = String(e);
  console.error(e);
});

// ── Image export (current view) ─────────────────────────────────────────────
// ppExport is also called by the headless renderer (render_image.py), so the
// downloaded files and the pipeline output come from the same code path.
const ppSampleFile = ppData.sample_file;

// Exported images carry the sample ID, purity/ploidy/status and MSI banner from
// the page header, so an image is never separated from the sample it shows.
// Break txt into lines whose rendered width (measured with the same font the SVG
// text uses) fits in `maxPx`, at spaces where possible, hard-splitting any single
// token that is wider than a line, so long sample IDs are never clipped.
function ppWrap(txt, font, maxPx) {
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = font;
  const fits = function (t) { return ctx.measureText(t).width * 1.05 <= maxPx; };   // 5% safety for font fallback
  const lines = []; let cur = "";
  txt.split(" ").forEach(function (word) {
    let chars = Array.from(word);        // code points, so a surrogate pair is never split
    while (!fits(chars.join("")) && chars.length > 1) {
      if (cur) { lines.push(cur); cur = ""; }
      let n = chars.length - 1;
      while (n > 1 && !fits(chars.slice(0, n).join(""))) n--;
      lines.push(chars.slice(0, n).join("")); chars = chars.slice(n);
    }
    word = chars.join("");
    if (cur && !fits(cur + " " + word)) { lines.push(cur); cur = word; }
    else cur = cur ? cur + " " + word : word;
  });
  if (cur) lines.push(cur);
  return lines.length ? lines : [""];
}

function ppSvg() {
  if (!window.igvBrowser) throw new Error("IGV browser is not ready");
  const NS = "http://www.w3.org/2000/svg";
  const doc  = new DOMParser().parseFromString(window.igvBrowser.toSVG(), "image/svg+xml");
  const root = doc.documentElement;
  const [w, h] = ppSvgSize(root);
  const clean = function (sel) { return document.querySelector(sel).textContent.replace(/\s+/g, " ").trim(); };
  // The IGV SVG has ruler coordinates but not its navigation bar, so state the genome and
  // locus explicitly: an image must identify what it shows once detached from its filename.
  const loci = window.igvBrowser.currentLoci && window.igvBrowser.currentLoci();
  // igv reports the whole genome as "all:1-<genome length>"; label it only when the range really is
  // the whole genome, so a partial "all:<a>-<b>" view keeps its coordinates.
  const glen = window.igvBrowser.genome && window.igvBrowser.genome.getGenomeLength && window.igvBrowser.genome.getGenomeLength();
  const locusText = [].concat(loci || "all").map(function (l) {
    const m = /^all(?::(\d+)-(\d+))?$/.exec(l);
    return m && (!m[1] || (Number(m[1]) <= 1 && glen && Math.abs(Number(m[2]) - glen) <= 1)) ? "all (whole genome)" : l;
  }).join(" | ");
  const where = "Genome hg38 · Locus: " + locusText;
  const FONT = "Arial, sans-serif", maxPx = w - 24;
  const title = ppWrap(clean("#hdr .ttl"),  "bold 14px " + FONT, maxPx);
  const warnEl = document.querySelector("#hdr .warn");          // ploidy assumption, if any
  const meta  = ppWrap(clean("#hdr .meta"), "12px " + FONT,      maxPx)
                  .concat(warnEl ? ppWrap(clean("#hdr .warn"), "12px " + FONT, maxPx) : [])
                  .concat(ppWrap(where,     "12px " + FONT,      maxPx));
  const BANNER = 8 + title.length * 18 + meta.length * 16 + 4;

  const body = doc.createElementNS(NS, "g");
  body.setAttribute("transform", "translate(0," + BANNER + ")");
  while (root.firstChild) body.appendChild(root.firstChild);
  const bg = doc.createElementNS(NS, "rect");
  [["x", 0], ["y", 0], ["width", w], ["height", BANNER], ["fill", "#1a2638"]]
    .forEach(function (kv) { bg.setAttribute(kv[0], kv[1]); });
  root.appendChild(bg);
  let y = 8;
  function add(lines, size, step, fill, weight) {
    lines.forEach(function (txt) {
      y += step;
      const t = doc.createElementNS(NS, "text");
      [["x", 12], ["y", y - 4], ["font-family", FONT], ["font-size", size],
       ["font-weight", weight], ["fill", fill]].forEach(function (kv) { t.setAttribute(kv[0], kv[1]); });
      t.textContent = txt;
      root.appendChild(t);
    });
  }
  add(title, 14, 18, "#e8edf3", "bold");
  add(meta,  12, 16, "#9aafc4", "normal");
  root.appendChild(body);
  root.setAttribute("height", h + BANNER);
  root.setAttribute("viewBox", "0 0 " + w + " " + (h + BANNER));
  return new XMLSerializer().serializeToString(root);
}

function ppSvgSize(root) {
  const vb = (root.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const w = parseFloat(root.getAttribute("width"))  || vb[2];
  const h = parseFloat(root.getAttribute("height")) || vb[3];
  if (!(w > 0 && h > 0)) throw new Error("SVG has no usable size");
  return [w, h];
}

function ppPng(scale) {
  scale = scale || 2;
  const svgText = ppSvg();
  const [w, h] = ppSvgSize(new DOMParser().parseFromString(svgText, "image/svg+xml").documentElement);
  const url = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml;charset=utf-8" }));
  return new Promise(function (resolve, reject) {
    const img = new Image();
    img.onload = function () {
      const c = document.createElement("canvas");
      c.width = Math.round(w * scale); c.height = Math.round(h * scale);
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob(function (blob) { blob ? resolve(blob) : reject(new Error("PNG encode failed")); }, "image/png");
    };
    img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("SVG could not be rasterised")); };
    img.src = url;
  });
}

function ppDownload(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
}

function ppLocusTag() {
  const l = window.igvBrowser && window.igvBrowser.currentLoci && window.igvBrowser.currentLoci();
  const t = String(Array.isArray(l) ? l[0] : (l || "view")).replace(/[^A-Za-z0-9._-]+/g, "_");
  return t || "view";
}

window.ppExport = {
  svg: ppSvg,
  png: function (scale) {
    return ppPng(scale).then(function (blob) {
      return new Promise(function (resolve, reject) {
        const r = new FileReader();
        r.onload  = function () { resolve(String(r.result).split(",")[1]); };
        r.onerror = function () { reject(r.error); };
        r.readAsDataURL(blob);
      });
    });
  },
};

document.getElementById("btn-svg").addEventListener("click", function () {
  try {
    ppDownload(new Blob([ppSvg()], { type: "image/svg+xml" }), ppSampleFile + "." + ppLocusTag() + ".svg");
  } catch (e) { alert("Could not export SVG: " + e.message); }
});
document.getElementById("btn-png").addEventListener("click", function () {
  ppPng(2).then(function (blob) { ppDownload(blob, ppSampleFile + "." + ppLocusTag() + ".png"); })
          .catch(function (e) { alert("Could not export PNG: " + e.message); });
});
