# Prototype assignment: Pocket Agent

## Part A: Problem statement and technical insight

### Revised problem statement

College students applying for software engineering internships and new-grad positions want to complete applications efficiently, but inconsistent hiring platforms force them to repeatedly enter information, correct autofill errors, and navigate application steps. They want less manual work without losing control over the accuracy of their information or important application decisions.

This revision reflects our research with five participant groups. Jack described repeated account creation and unsupported school selections; Yucheng sometimes abandoned applications with too much manual entry; and David and Huntington described repeatedly clicking Next and reactivating autofill. Daphne's preference for writing personalized responses herself also showed that maximum automation is not every applicant's goal.

### Revised technical insight

Job applications can be treated as workflows rather than isolated forms. Combining reusable applicant facts, automated browser actions, and requests for help at unresolved steps can reduce repeated interaction while preserving applicant control. The prototype uses rules for known information and browser navigation, with selective AI assistance for written responses, instead of requiring an AI model to decide every action.

This makes our original insight testable: can the system carry an application through multiple interactions without requiring the applicant to restart autofill on each step? It does not assume that a general AI browser agent can already handle every employer website reliably.

### Core scenario

A student configures a profile and resume, chooses a job application, and starts the filler in interactive mode. Pocket Agent opens the application in Chromium, fills known information, uploads the resume, and advances through supported application steps. When a required fact is missing, it asks the student, fills the response, and continues. Eligible written questions can receive an AI-generated draft based on profile and resume evidence. The intended outcome is a filled application that the student reviews and submits manually.

The scenario puts our insight into practice by joining form entry, navigation, and exception handling into one run. The prototype is still limited by site-specific controls and upload/navigation detection; reaching Review must be demonstrated on the selected application rather than assumed for every site.

## Part B: Working core scenario

### Implementation

We built a Python application with a command-line interface and a local browser interface. The application reads a structured YAML profile and extracts text from a PDF resume. It uses these facts and saved answers to fill supported text fields, dropdowns, radio buttons, dates, and resume uploads. It can advance recognized Next/Continue controls and pause for user assistance.

Known answers are filled without an AI request. GPT or Claude can draft eligible written answers, while missing personal decisions are directed to the applicant. Explicit demographic answers saved in the profile are reused. Salary research can propose the midpoint of a sourced new-grad annual base-salary range when no reusable target is provided. AI request limits, caching, and request logs make this behavior visible and help control costs.

The final submission remains manual. General browser interaction uses Playwright and coded rules; this prototype is not a general-purpose AI browser agent. Authentication, CAPTCHA, unsupported widgets, and upload-state detection can still interrupt a run. In testing, an Ashby upload appeared in the browser while the filler reported that the upload had not completed; fixing this mismatch is part of our remaining work.

### Repository and run instructions

- Repository: https://github.com/linnkhant07/pocket_agent
- Installation and run instructions: https://github.com/linnkhant07/pocket_agent/blob/main/README.md
- Walkthrough video: https://youtu.be/UoI8HH7bIS8

The README explains dependency installation, Chromium setup, profile/resume configuration, interactive operation, optional GPT/Claude configuration, and cost controls. A blank `profile.example.yaml` provides a starting structure without requiring personal information in the repository.

**Before submission:** confirm that course staff can access the repository and video, that the README link uses the correct default branch, and that all new implementation files are committed. Run the documented scenario from a fresh checkout. Repository access and a working fresh installation have not been established by this write-up.

## Part C: Cognitive walkthroughs

Skipped for this assignment per the professor's Slack instruction. We are not claiming to have conducted two new cognitive walkthroughs for this prototype assignment. The interviews discussed above were conducted for Assignment 2.

## Part D: Design decisions log

### Decision 1: Use rules first and AI selectively

**Decision:** Fill known fields from the profile and saved answers, and reserve hosted AI calls for eligible written answers and salary research. Apply a default limit of three API requests per CLI run and cache identical responses for 24 hours.

**Alternative rejected:** Ask an AI model to interpret or confirm every field and control the entire browser workflow.

**Reason:** Our design goal is to reduce repetitive effort without adding unnecessary cost or supervision. During development, we found that AI was being asked to confirm some rule-based answers even though the rule's answer would be used regardless. We removed those calls. This also addresses Ivan's and Daphne's concerns about correcting or checking automated output: known facts should not be regenerated unnecessarily. Broader browser planning remains a possible future direction, not a demonstrated capability of this prototype.

### Decision 2: Ask for missing facts and keep final submission manual

**Decision:** Ask the applicant for missing personal facts and decisions, reuse explicitly saved answers, and stop before submitting an application.

**Alternative rejected:** Guess unresolved answers and automatically submit once the form appears complete.

**Reason:** Our research showed different preferences for control. Daphne preferred personally writing tailored responses, while Jack wanted less interaction but retained control over important fields. Asking at unresolved steps and preserving final review reduces repeated entry without assuming that every applicant wants the same degree of automation. The current prototype offers a common review boundary; more granular approval preferences remain future work.

### Decision 3: Test a bounded application workflow before broad platform support

**Decision:** Evaluate the prototype on selected application forms and report where it stops, rather than presenting all application platforms as supported.

**Alternative rejected:** Use successful basic autofill as evidence that the whole application process works across employers.

**Reason:** Assignment 2 testing showed that standard fields were easier than nonstandard dropdowns, date controls, and Workday-style navigation. The prototype's purpose is to test those workflow limitations. A visible upload does not necessarily mean our code recognized completion, and an application that stopped is not the same as one ready for review. We need to measure the whole scenario, including interventions and errors.

## Part E: Plan to finish

### Tech stack

| Component | Technology and role |
| --- | --- |
| Application logic and CLI | Python 3.11+ and argparse |
| Browser automation | Playwright and Chromium; reads controls, fills fields, uploads files, and executes supported navigation |
| Applicant information | YAML profile parsed with PyYAML; PDF text extracted with pypdf |
| Local browser interface | FastAPI, Uvicorn, HTML/CSS/JavaScript, and WebSockets |
| Persistence | SQLite for answer memory, the job/application tracker, and cached AI responses |
| Hosted AI | HTTP requests through httpx to OpenAI or Anthropic; credentials supplied through environment variables |
| Salary research | Provider-hosted web search, subject to model/account support; validates returned citations and range metadata before filling |
| Optional Workday account support | Python keyring integration with macOS Keychain |
| Job discovery | Public SimplifyJobs listings, with optional Greenhouse and Lever board APIs |
| Verification | pytest, mocked provider responses, and local fictional browser forms |

The core filling demonstration starts with an application URL and does not depend on job discovery. Local-model chat, training, and evaluation utilities remain in the repository but are not required for this scenario.

### Ranked risks and backup plans

| Priority | Risk | Backup plan |
| --- | --- | --- |
| 1 | Incorrect personal information or unsupported generated claims | Check filled values against the profile, ask for missing facts, and require manual final review. Disable AI for a rules-only demonstration if needed. |
| 2 | Site-specific uploads, widgets, or navigation stop the workflow | Fix the selected demonstration flow first, retain manual recovery, and keep a second application plus a recording available. Address the Ashby upload-state mismatch explicitly. |
| 3 | A job closes or its application changes before feedback | Recheck the exact posting before presenting and prepare another accessible form. A local fictional form can demonstrate mechanics, clearly labeled as a simulation. |
| 4 | API outages, unsupported search, rate limits, or unexpected cost | Use request limits and caching; stop additional paid attempts after failure and fall back to user answers or `--model none`. |
| 5 | A fresh installation is missing files or configuration | Commit required implementation files, test from a fresh checkout, and follow the README using the blank profile template. Keep keys and personal files outside Git. |

### Timeline and ownership

The following is a proposed division of work; confirm ownership with both members before submitting.

| Target | Deliverable | Proposed owner |
| --- | --- | --- |
| November 20 | Verify a fresh checkout and fix upload detection for the chosen demonstration flow | Khant |
| November 25 | Run a small, recorded comparison across selected applications; record correct fields, interventions, time, and whether Review was reached | Khant: fixes; Linn: test records and analysis |
| November 30 | Rehearse the December 1 demonstration, update the README and decisions log, and verify repository/video access | Both; Linn leads documentation |
| December 1 feedback session | Demonstrate the core scenario and collect feedback on correctness, interaction, and control | Both |
| December 2 | Prioritize and implement the highest-impact feedback | Khant: implementation; Linn: validation and documentation |
| December 3 feedback session | Present the revised workflow, measured results, and remaining limitations | Both |

Success will be assessed using the measures identified in Assignment 2: whether the application reaches review, field correctness, elapsed time, number of human interventions, and common failures. No completion-rate or time-saving claim should be made until those results are collected.

## Final questions

### 1. What did each member contribute?

- **Khant:** Led prototype implementation and debugging, including profile/resume integration, browser filling, interactive prompts, hosted-AI integration, request logging and cost controls, and setup documentation, with AI coding assistance.
- **Linn:** [Add Linn's actual contributions to this prototype assignment. Do not reuse the earlier assignment's contribution statement unless it also applies here.]

### 2. How much time did each member spend, and how much time did the group work together?

- **Khant:** Approximately 6 hours.
- **Linn:** [Add hours.]
- **Group work together:** Approximately 10 hours [confirm whether this means elapsed shared working time or combined person-hours, and whether it overlaps the individual totals].

### 3. Did you use a generative AI tool? Which one and for what?

Yes. We used OpenAI Codex/ChatGPT to inspect and modify code, develop tests, debug issues, refine the prototype's design, and draft or revise documentation and this submission. AI assistance was used in implementation as well as writing.

The application integrates GPT and Claude APIs for selected written responses and salary research. Supporting a provider does not mean it was used in every demonstration; known fields can be completed without any AI call. Automated provider tests use mocked responses, so those tests do not establish live model performance.
