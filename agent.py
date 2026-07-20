"""
Resume auto-fill agent -- Gemini version.

Same architecture as the Claude version: model + system prompt + browser tools,
looped manually so we control exactly when tools run and can stop cleanly.

Setup:
    pip install google-genai playwright --break-system-packages
    playwright install chromium
    export GEMINI_API_KEY=your_key_here   # free key from Google AI Studio

Usage:
    python agent.py "https://boards.greenhouse.io/example/jobs/12345"
"""

import json
import sys
from google import genai
from google.genai import types
from browser_tools import BrowserSession

MODEL = "gemini-2.5-flash"  # free-tier eligible; check ai.google.dev/gemini-api for current models & limits
MAX_TURNS = 15

SYSTEM_PROMPT = """You are a careful job-application assistant. You fill out job \
application forms using ONLY facts from the candidate's profile JSON provided to you.

Rules:
- NEVER invent facts (dates, employers, skills, degrees) not present in the profile.
- For simple factual fields (name, email, phone, dates, links), copy directly from the profile.
- For open-ended questions (e.g. "Why do you want to work here?", "Describe a challenge you overcame"),
  write a concise, specific, professional answer grounded in the profile's experience and, when
  available, the job description text on the page. 3-5 sentences unless the field looks short (e.g. a
  single-line input).
- If a required field has no matching profile data (e.g. a question about a topic not in the
  profile), do NOT guess. Instead, stop and clearly tell the user what info you need.
- Never click submit or any button. Your job ends once fields are filled.
- Work through fields one scan at a time. After filling what you can, summarize what you filled
  and flag anything you skipped and why.
"""

# --- Tool declarations (Gemini's equivalent of Claude's `tools` list) ---

scan_form_fields_decl = types.FunctionDeclaration(
    name="scan_form_fields",
    description="Scan the current page and return all visible form fields with labels.",
    parameters={"type": "object", "properties": {}},
)

get_page_text_decl = types.FunctionDeclaration(
    name="get_page_text",
    description="Get the visible text of the page (e.g. job description) for context.",
    parameters={"type": "object", "properties": {}},
)

fill_field_decl = types.FunctionDeclaration(
    name="fill_field",
    description="Fill a form field with a value. For <select> fields, value must match one of its options.",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector returned by scan_form_fields"},
            "value": {"type": "string"},
        },
        "required": ["selector", "value"],
    },
)

TOOLS = types.Tool(function_declarations=[scan_form_fields_decl, get_page_text_decl, fill_field_decl])


def run_tool(session: BrowserSession, name: str, args: dict) -> dict:
    if name == "scan_form_fields":
        return {"result": session.scan_form_fields()}
    if name == "get_page_text":
        return {"result": session.get_page_text()}
    if name == "fill_field":
        return {"result": session.fill_field(args["selector"], args["value"])}
    return {"error": f"unknown tool {name}"}


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent.py <job_application_url>")
        sys.exit(1)

    url = sys.argv[1]
    profile = json.load(open("profile.json"))

    client = genai.Client()  # reads GEMINI_API_KEY from env
    session = BrowserSession(headless=False)
    session.open(url)

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOLS],
        # We call tools ourselves (via Playwright), so disable Gemini's automatic
        # function execution -- it can only auto-run plain Python functions anyway.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=(
                f"Here is the candidate's profile:\n{json.dumps(profile, indent=2)}\n\n"
                "Please scan the current application page and fill in what you can."
            ))],
        )
    ]

    for _ in range(MAX_TURNS):
        response = client.models.generate_content(model=MODEL, contents=contents, config=config)
        candidate = response.candidates[0]
        contents.append(candidate.content)  # record the model's turn (role="model")

        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]

        for part in candidate.content.parts:
            if part.text:
                print(f"\n[agent] {part.text}")

        if not function_calls:
            break  # agent is done, or asking you a question

        response_parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            print(f"[tool] {fc.name}({args})")
            result = run_tool(session, fc.name, args)
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))

        contents.append(types.Content(role="tool", parts=response_parts))

    print("\n--- Done. Review the form in the browser window, then submit it yourself. ---")
    input("Press Enter to close the browser...")
    session.close()


if __name__ == "__main__":
    main()
