"""Playwright helpers for igv.js's own UI (it renders inside a shadow root; locators pierce it)."""


def save_via_igv_menu(page, fmt):
    """Click the toolbar "Save Image" button, then "Save as SVG" / "Save as PNG"; return the Download."""
    page.get_by_text("Save Image", exact=True).first.click()
    with page.expect_download() as dl:
        page.get_by_text(f"Save as {fmt}", exact=True).click()
    return dl.value
