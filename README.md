# Resume Auto-Fill Agent — Starter

## Setup
```bash
pip install google-genai playwright --break-system-packages
playwright install chromium
export GEMINI_API_KEY=your_key_here   # free key from https://aistudio.google.com
```

Note: "free" here means Gemini's free tier, which has daily request limits that
vary by model. Check ai.google.dev/gemini-api for current limits before relying
on this for a high-volume job search.

## Fill in your real info
Edit `profile.json` with your actual contact info, experience, education, skills.
Don't put anything here you wouldn't want submitted verbatim.

## Run
```bash
python agent.py "https://boards.greenhouse.io/somecompany/jobs/12345"
```

A visible Chromium window opens, the agent scans the form, fills what it can,
and stops. You review and click submit yourself.

## Design decisions worth understanding (not just copying)

- **No auto-submit tool.** This is intentional, not a limitation to code around.
  Many ATS platforms (Greenhouse, Lever, Workday) restrict automated submissions
  in their terms of use, and you want a human check before anything factual goes
  out. If you later add a submit tool, keep a confirmation step.

- **"Never invent facts" is a system prompt rule, not a technical guarantee.**
  Same principle as the Socratic tutor's "never give solutions" — it's a
  constraint you have to actively test, not assume holds. Try it against a form
  with a question your profile has no data for (e.g. "Describe your experience
  with Kubernetes" when profile.json has no Kubernetes) and see whether it asks
  you instead of guessing.

- **The scan → decide → fill loop is the same core agent loop as the tutor**:
  model + system prompt + tools, just with browser tools instead of a bare chat
  loop. Once you're comfortable with this, the pattern transfers to almost any
  "agent that acts on a UI" project.

## Second tool: the resume tailor (tailor.py)

Separate from the form filler. Give it a job posting, it rewrites resume.md to
match, following rules.md, and outputs a versioned Markdown + PDF.

```bash
pip install requests beautifulsoup4 markdown pypdf --break-system-packages
python tailor.py "https://boards.greenhouse.io/example/jobs/123" --company "Example Co"
```

`pypdf` is used to count PDF pages so the resume can automatically shrink font
size and margins until it fits on one page. If the warning about "still N
pages even at smallest size" ever prints, that's a signal to trim resume.md or
tighten the bullet limits in rules.md rather than shrink further -- text that
small stops being readable.

PDF rendering uses Playwright (already installed for agent.py) instead of
WeasyPrint, since WeasyPrint needs native GTK libraries that are a pain to set
up on Windows. If you installed weasyprint earlier, it's safe to leave it or
uninstall it (`pip uninstall weasyprint`) -- it's no longer imported.

- `resume.md` is the source of truth. Edit it with your real info; the tailor
  can only rearrange and reword what exists there, never add facts.
- `rules.md` is the contract: accuracy rules, keyword strategy, natural
  college-student voice, no em/en dashes. Edit it to tune the output.
- Each run saves `versions/<company>_resume.md` + `.pdf` and git-commits the
  Markdown, so you have a history of exactly what you sent to each company.
- Some job sites block simple fetches. If the URL fails, paste the posting into
  a `.txt` file and pass that path instead of the URL.

Note the `enforce_no_dashes()` function: the no-dash rule lives in rules.md, but
prompts are requests, not guarantees. When a constraint really matters, back it
up in code. That is the same lesson as the tutor's "no direct answers" rule.

## Natural next steps
- Cache generated answers by question text so re-applying to similar roles
  doesn't regenerate (and cost tokens on) the same "why do you want to work here"
  answer every time.
- Add a `get_job_description` tool that specifically extracts the posting text
  (vs. all page text) for better-grounded open-ended answers.
- Swap `headless=False` to `True` once you trust it, for background runs.