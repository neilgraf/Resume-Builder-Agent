"""
Resume tailor -- reads a job posting, tailors resume.md to it, outputs a PDF.

Pipeline:
  1. Fetch job description text from the URL (or read from a local .txt file).
  2. Load resume.md (source of truth) and rules.md (the tailoring contract).
  3. Ask Gemini to produce a tailored resume that follows every rule.
  4. Save versions/<company>_resume.md, render it to PDF, and git-commit it
     so you keep a history of exactly what you sent where.

Setup (in addition to the main README setup):
    pip install requests beautifulsoup4 markdown weasyprint --break-system-packages

Usage:
    python tailor.py "https://boards.greenhouse.io/example/jobs/12345" --company "Example Co"
    python tailor.py job_posting.txt --company "Example Co"   # pasted-text fallback
"""

import argparse
import datetime
import pathlib
import re
import subprocess
import sys

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash"  # current Gemini model as of mid-2026; check ai.google.dev/gemini-api for current models & limits


def fetch_job_text(source: str) -> str:
    """Get job description text from a URL or a local text file."""
    if pathlib.Path(source).exists():
        return pathlib.Path(source).read_text()
    resp = requests.get(source, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
    return text[:12000]


def tailor(resume_md: str, rules_md: str, job_text: str) -> str:
    client = genai.Client()  # reads GEMINI_API_KEY
    prompt = (
        f"JOB DESCRIPTION:\n{job_text}\n\n"
        f"CURRENT RESUME (the only source of facts you may use):\n{resume_md}\n\n"
        "Tailor the resume to this job. Follow every rule."
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=rules_md),
    )
    return response.text


def write_cover_letter(resume_md: str, cover_rules_md: str, job_text: str, company: str) -> str:
    client = genai.Client()
    today_str = datetime.date.today().strftime("%B %d, %Y")
    prompt = (
        f"TODAY'S DATE (use this exact date in the letter header, do not guess or use any other date): {today_str}\n\n"
        f"COMPANY: {company}\n\n"
        f"JOB DESCRIPTION:\n{job_text}\n\n"
        f"CANDIDATE RESUME (the only source of facts about the candidate):\n{resume_md}\n\n"
        "Write the cover letter. Follow every rule."
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=cover_rules_md),
    )
    return response.text


def fix_markdown_lists(text: str) -> str:
    """Guarantee a blank line before any list block.

    Python-Markdown (and most Markdown parsers) only start a real <ul> if the
    list is preceded by a blank line. If the model's output skips that blank
    line after a job title, the parser treats it all as one paragraph and the
    bullets end up mashed together with literal " - " text. This is a code
    guarantee, not a prompt request, same reasoning as enforce_no_dashes().
    """
    lines = text.split("\n")
    fixed = []
    for i, line in enumerate(lines):
        is_bullet = line.strip().startswith(("- ", "* "))
        prev_is_blank_or_bullet = (
            i == 0 or not lines[i - 1].strip() or lines[i - 1].strip().startswith(("- ", "* "))
        )
        if is_bullet and not prev_is_blank_or_bullet:
            fixed.append("")  # insert the missing blank line
        fixed.append(line)
    return "\n".join(fixed)


def enforce_no_dashes(text: str) -> str:
    """Hard guardrail: strip em/en dashes even if the model slips one in.

    A rule in the prompt is a request; this is a guarantee. Same lesson as the
    tutor project: prompt rules need a backstop when the constraint really matters.
    """
    return text.replace("\u2014", ", ").replace("\u2013", " to ")


def render_html(md_text: str, font_size: float = 10.5, margin_in: float = 0.65) -> str:
    import markdown

    css = f"""
    body {{ font-family: Georgia, 'Times New Roman', serif; font-size: {font_size}pt; line-height: 1.32; color: #1a1a1a; max-width: 7.2in; margin: 0 auto; }}
    h1 {{ font-size: {font_size + 7}pt; margin: 0 0 3pt 0; }}
    h2 {{ font-size: {font_size + 1.5}pt; border-bottom: 1px solid #999; margin: 10pt 0 4pt 0; padding-bottom: 2pt; text-transform: uppercase; letter-spacing: 0.5pt; }}
    p {{ margin: 3pt 0; }}
    p strong {{ font-size: {font_size + 0.5}pt; }}
    ul {{ margin: 3pt 0 8pt 0; padding-left: 16pt; }}
    li {{ margin-bottom: 2pt; }}
    a {{ color: #1a1a1a; text-decoration: none; }}
    """
    # nl2br: without this, a single line break inside a paragraph collapses into
    # a space, which is exactly what squished the education section together.
    html_body = markdown.markdown(md_text, extensions=["nl2br"])
    return f"<html><head><style>{css}</style></head><body>{html_body}</body></html>"


def to_pdf(md_text: str, pdf_path: pathlib.Path, one_page: bool = False):
    from playwright.sync_api import sync_playwright

    if not one_page:
        html = render_html(md_text)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_content(html)
            page.pdf(path=str(pdf_path), format="Letter", margin={"top": "0.65in", "bottom": "0.65in", "left": "0.65in", "right": "0.65in"})
            browser.close()
        return

    # Shrink-to-fit: try progressively smaller font/margins until it's one page.
    # This is a backstop -- the real fix is keeping resume.md and rules.md
    # tight enough that this rarely has to kick in.
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # fallback for older installs

    attempts = [(10.5, 0.65), (10, 0.6), (9.5, 0.55), (9, 0.5), (8.5, 0.45)]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for font_size, margin_in in attempts:
            html = render_html(md_text, font_size, margin_in)
            page.set_content(html)
            page.pdf(path=str(pdf_path), format="Letter", margin={
                "top": f"{margin_in}in", "bottom": f"{margin_in}in",
                "left": f"{margin_in}in", "right": f"{margin_in}in",
            })
            page_count = len(PdfReader(str(pdf_path)).pages)
            if page_count <= 1:
                browser.close()
                return
        browser.close()
    print(f"  Warning: still {page_count} pages even at smallest size. Trim resume.md content or rules.md bullet limits.")


def git_commit(paths: list[pathlib.Path], message: str):
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        subprocess.run(["git", "init"], check=False)
    subprocess.run(["git", "add", *[str(p) for p in paths]], check=False)
    subprocess.run(["git", "commit", "-m", message], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Job posting URL, or path to a .txt file of the posting")
    parser.add_argument("--company", required=True, help="Company name, used for filenames")
    args = parser.parse_args()

    base = pathlib.Path(__file__).parent
    resume_md = (base / "resume.md").read_text()
    rules_md = (base / "rules.md").read_text()

    print("Fetching job description...")
    job_text = fetch_job_text(args.source)

    print("Tailoring resume...")
    output = enforce_no_dashes(tailor(resume_md, rules_md, job_text))
    output = fix_markdown_lists(output)

    # Split the resume from the agent's notes
    if "===NOTES===" in output:
        tailored_md, notes = output.split("===NOTES===", 1)
    else:
        tailored_md, notes = output, "(no notes returned)"

    slug = re.sub(r"[^a-z0-9]+", "_", args.company.lower()).strip("_")
    versions = base / "versions"
    versions.mkdir(exist_ok=True)
    md_path = versions / f"{slug}_resume.md"
    pdf_path = versions / f"{slug}_resume.pdf"

    md_path.write_text(tailored_md.strip() + "\n")
    print("Rendering resume PDF...")
    to_pdf(tailored_md, pdf_path, one_page=True)

    print("Writing cover letter...")
    cover_rules_md = (base / "cover_rules.md").read_text()
    cover_md = enforce_no_dashes(write_cover_letter(resume_md, cover_rules_md, job_text, args.company))
    cover_md = fix_markdown_lists(cover_md)
    cover_md_path = versions / f"{slug}_cover_letter.md"
    cover_pdf_path = versions / f"{slug}_cover_letter.pdf"
    cover_md_path.write_text(cover_md.strip() + "\n")
    to_pdf(cover_md, cover_pdf_path)

    stamp = datetime.date.today().isoformat()
    git_commit([md_path, cover_md_path], f"Tailored resume + cover letter for {args.company} ({stamp})")

    print(f"\nSaved: {md_path}\nSaved: {pdf_path}")
    print(f"Saved: {cover_md_path}\nSaved: {cover_pdf_path}\n")
    print("--- Agent notes (review before sending!) ---")
    print(notes.strip())
    print("\nOpen the PDF, read every line, and confirm it is all true before you use it.")


if __name__ == "__main__":
    main()