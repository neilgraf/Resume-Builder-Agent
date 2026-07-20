"""
Browser tools: the agent's 'hands'.

These wrap Playwright so the agent loop can:
  1. scan_form_fields()  -> see what's on the page
  2. fill_field(...)     -> type/select an answer into a field
  3. get_page_text()     -> read job description / question context

Run `playwright install chromium` once before first use.
"""

from playwright.sync_api import sync_playwright

_JS_SCAN_FIELDS = """
() => {
  const fields = [];
  const inputs = document.querySelectorAll('input, textarea, select');
  inputs.forEach((el, i) => {
    if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

    // Try to find an associated label
    let label = '';
    if (el.labels && el.labels.length) {
      label = el.labels[0].innerText.trim();
    } else if (el.getAttribute('aria-label')) {
      label = el.getAttribute('aria-label');
    } else if (el.placeholder) {
      label = el.placeholder;
    } else if (el.closest('label')) {
      label = el.closest('label').innerText.trim();
    }

    // Give every field a stable selector we can fill later
    if (!el.id) el.id = `agent-field-${i}`;

    const field = {
      selector: `#${el.id}`,
      label: label || '(no label found)',
      tag: el.tagName.toLowerCase(),
      type: el.type || null,
      current_value: el.value || null,
    };

    if (el.tagName.toLowerCase() === 'select') {
      field.options = Array.from(el.options).map(o => o.value || o.text);
    }

    fields.push(field);
  });
  return fields;
}
"""


class BrowserSession:
    def __init__(self, headless: bool = False):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)
        self.page = self.browser.new_page()

    def open(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded")

    def scan_form_fields(self) -> list[dict]:
        """Return every visible form field on the page with a best-guess label."""
        return self.page.evaluate(_JS_SCAN_FIELDS)

    def fill_field(self, selector: str, value: str) -> str:
        """Fill a text/textarea field, or select an option in a <select>."""
        el = self.page.query_selector(selector)
        if el is None:
            return f"error: no element found for selector {selector}"

        tag = el.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            el.select_option(label=value)
        else:
            el.fill(value)
        return f"filled {selector} with: {value[:60]}"

    def get_page_text(self) -> str:
        """Grab visible page text, useful for reading the job description."""
        return self.page.inner_text("body")[:6000]  # capped to keep prompts small

    def close(self):
        self.browser.close()
        self._pw.stop()
