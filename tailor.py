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

MODEL = "gemini-2.5-flash"

PDF_CSS = """
@page { size: letter; margin: 0.7in; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt; line-height: 1.35; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 2pt 0; }
h2 { font-size: 11.5pt; border-bottom: 1px solid #999; margin: 10pt 0 4pt 0; text-transform: uppercase; letter-spacing: 0.5pt; }
ul { margin: 2pt 0 6pt 0; padding-left: 16pt; }
li { margin-bottom: 1pt; }
p { margin: 2pt 0; }
a { color: #1a1a1a; text-decoration: none; }
"""


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
    prompt = (
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


def enforce_no_dashes(text: str) -> str:
    """Hard guardrail: strip em/en dashes even if the model slips one in.

    A rule in the prompt is a request; this is a guarantee. Same lesson as the
    tutor project: prompt rules need a backstop when the constraint really matters.
    """
    return text.replace("\u2014", ", ").replace("\u2013", " to ")


def to_pdf(md_text: str, pdf_path: pathlib.Path):
    import markdown
    from weasyprint import HTML

    html_body = markdown.markdown(md_text)
    html = f"<html><head><style>{PDF_CSS}</style></head><body>{html_body}</body></html>"
    HTML(string=html).write_pdf(str(pdf_path))


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
    to_pdf(tailored_md, pdf_path)

    print("Writing cover letter...")
    cover_rules_md = (base / "cover_rules.md").read_text()
    cover_md = enforce_no_dashes(write_cover_letter(resume_md, cover_rules_md, job_text, args.company))
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
