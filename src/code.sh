#!/bin/bash
set -exo pipefail

main() {
    mark-section "Validating inputs"
    if [ -z "${purple_tar:-}" ] && [ -z "${cnvkit_cnr:-}" ]; then
        echo "ERROR: at least one of purple_tar or cnvkit_cnr must be provided" >&2
        exit 1
    fi

    mark-section "Downloading inputs"
    dx-download-all-inputs --parallel

    mark-section "Installing packages"
    python3 -m venv /tmp/ppenv
    /tmp/ppenv/bin/pip install --no-index --no-deps /home/dnanexus/packages/*

    mark-section "Building arguments"
    args=("--sample" "${sample_id}" "--output_dir" "${HOME}/")

    [ -n "${purple_tar:-}" ]         && args+=("--purple_tar"         "$(ls ~/in/purple_tar/*)")  
    [ -n "${qc_report:-}" ]          && args+=("--qc_report"          "$(ls ~/in/qc_report/*)")
    [ -n "${locus:-}" ]              && args+=("--locus"               "${locus}")
    [ -n "${cnvkit_cnr:-}" ]         && args+=("--cnvkit_cnr"          "$(ls ~/in/cnvkit_cnr/*)")
    [ -n "${cnvkit_call_cns:-}" ]    && args+=("--cnvkit_call_cns"     "$(ls ~/in/cnvkit_call_cns/*)")
    [ -n "${cnvkit_genemetrics:-}" ] && args+=("--cnvkit_genemetrics"  "$(ls ~/in/cnvkit_genemetrics/*)")
    [ -n "${msi_report:-}" ]         && args+=("--msi_report"          "$(ls ~/in/msi_report/*)")

    mark-section "Generating IGV bars HTML"
    /tmp/ppenv/bin/python3 /home/dnanexus/purple_plotter/purple_plotter.py "${args[@]}"

    if [ "${genome_image:-false}" = "true" ]; then
        mark-section "Rendering whole-genome image"
        # Playwright + Chromium come from the pp_headless_chromium asset (see asset/)
        if [ ! -x /opt/pp_render/bin/python3 ]; then
            echo "ERROR: genome_image requested but the pp_headless_chromium asset is not installed" >&2
            exit 1
        fi
        PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/pp_render/bin/python3 \
            /home/dnanexus/purple_plotter/render_image.py \
            --html "${HOME}/${sample_id}.igv.bars.html" \
            --out_prefix "${HOME}/${sample_id}.genome"
    fi

    mark-section "Uploading output"
    igv_html=$(dx upload "${HOME}/${sample_id}.igv.bars.html" --brief)
    dx-jobutil-add-output igv_html "${igv_html}" --class=file

    if [ "${genome_image:-false}" = "true" ]; then
        genome_png=$(dx upload "${HOME}/${sample_id}.genome.png" --brief)
        dx-jobutil-add-output genome_png "${genome_png}" --class=file
        genome_svg=$(dx upload "${HOME}/${sample_id}.genome.svg" --brief)
        dx-jobutil-add-output genome_svg "${genome_svg}" --class=file
    fi

    mark-success
}
