"""Repeatable offline demo. All applicant data and employers are fictional."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pocket_agent.forms.catalog import FieldSpec, FormSpec, StepSpec
from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import eval_profile, seed_memory
from pocket_agent.forms.server import FormServer
from pocket_agent.resume_pdf import write_simple_pdf

DEMO = FormSpec(
    id="prototype-demo", company="Pocket Agent Demo (fictional)", style="greenhouse", split="heldout",
    steps=(
        StepSpec("Contact Information", (
            FieldSpec("first_name", "First Name"), FieldSpec("last_name", "Last Name"),
            FieldSpec("email", "Email"), FieldSpec("phone", "Phone Number"),
            FieldSpec("phone_device_type", "Phone Device Type", kind="select", options=("Mobile", "Landline")),
            FieldSpec("phone_extension", "Phone Extension", required=False, expect="skip"),
            FieldSpec("how_did_you_hear", "How Did You Hear About Us?", kind="select", options=("Internet - LinkedIn", "Referral")),
            FieldSpec("resume", "Resume", kind="file"),
        )),
        StepSpec("Education", (
            FieldSpec("school", "School"), FieldSpec("degree", "Degree"),
            FieldSpec("field", "Field of Study"),
        )),
        StepSpec("Work Experience", (
            FieldSpec("work.title", "Job Title"), FieldSpec("work.org", "Company"),
            FieldSpec("work.description", "Job Description", kind="textarea"),
        ), repeater="work"),
        StepSpec("Review", ()),
    ),
)


def run_demo(*, runs: int = 1, headless: bool = False, out: Path | None = None) -> int:
    from playwright.sync_api import sync_playwright

    if runs < 1:
        raise ValueError("runs must be positive")
    server = FormServer((DEMO,))
    server.start()
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="pocket-demo-") as folder, sync_playwright() as pw:
            root = Path(folder)
            resume = root / "Jordan_Hale_Resume.pdf"
            write_simple_pdf(resume, ["Jordan Hale", "jordan.hale@example.com", "Fictional demo resume"])
            profile = eval_profile()
            profile["form_answers"] = {"phone_device_type": "Mobile", "how_did_you_hear": "Internet - LinkedIn"}
            for index in range(runs):
                browser = pw.chromium.launch(headless=headless)
                memory = Memory(root / f"answers-{index}.sqlite")
                try:
                    seed_memory(memory, profile, resume_path=str(resume))
                    page = browser.new_page()
                    questions = []
                    def ask(item):
                        questions.append(item.label)
                        return UserAnswer("", skip=True)
                    run = fill_form(page, server.url(DEMO.id), memory, resume, ask)
                    # Check DOM values independently of the filler outcome counts.
                    digits = lambda value: "".join(character for character in value if character.isdigit())
                    checks = {
                        "first_name": page.get_by_label("First Name", exact=True).input_value() == "Jordan",
                        "last_name": page.get_by_label("Last Name", exact=True).input_value() == "Hale",
                        "email": page.get_by_label("Email", exact=True).input_value() == profile["email"],
                        "phone": digits(page.get_by_label("Phone Number", exact=True).input_value()) == digits(profile["phone"]),
                        "degree": page.get_by_label("Degree", exact=True).input_value() == profile["education"][0]["degree"],
                        "field_of_study": page.get_by_label("Field of Study", exact=True).input_value() == profile["education"][0]["field"],
                        "job_titles": page.get_by_label("Job Title", exact=True).evaluate_all("els => els.map(el => el.value)") == [job["title"] for job in profile["experience"]],
                        "job_descriptions": page.get_by_label("Job Description", exact=True).evaluate_all("els => els.map(el => el.value)") == [" ".join(job["highlights"]) for job in profile["experience"]],
                        "phone_type": page.get_by_label("Phone Device Type", exact=True).input_value() == "Mobile",
                        "referral": page.get_by_label("How Did You Hear About Us?", exact=True).input_value() == "Internet - LinkedIn",
                        "school": page.get_by_label("School", exact=True).input_value() == profile["education"][0]["school"],
                        "resume": page.get_by_label("Resume", exact=True).evaluate("el => el.files.length") == 1,
                        "work_history": page.get_by_label("Company", exact=True).evaluate_all("els => els.map(el => el.value)") == [job["org"] for job in profile["experience"]],
                        "extension_blank": page.get_by_label("Phone Extension", exact=True).input_value() == "",
                    }
                    passed = all(checks.values()) and run.status == "review" and not (run.error or run.gate or run.unresolved or questions or run.submits)
                    results.append({"run": index + 1, "passed": passed, "status": run.status, "current_step": run.current_step,
                                    "error": run.error, "unresolved": run.unresolved, "questions": questions, "submits": run.submits,
                                    "filled": sum(item.action == "fill" for item in run.outcomes), "checks": checks})
                    print(json.dumps(results[-1]))
                    if not headless and index == runs - 1:
                        print("Local fictional demo complete. Inspect the browser; close its window to exit.")
                        page.wait_for_event("close", timeout=0)
                finally:
                    memory.close()
                    browser.close()
    finally:
        server.stop()
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"scenario": "local fictional demo", "runs": results}, indent=2))
    return 0 if all(item["passed"] for item in results) else 2
