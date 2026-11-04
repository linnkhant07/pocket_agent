"""Render a fictional application form to one HTML page."""

from __future__ import annotations

import html
import random
from dataclasses import dataclass

from pocket_agent.forms.catalog import FieldSpec, FormSpec, StepSpec


@dataclass
class _Ctrl:
    html: str
    reveal: str | None = None


def render_form(spec: FormSpec, *, perturb: str | None = None) -> str:
    rng = random.Random(spec.seed)
    parts: list[str] = []
    radio_names: dict[str, str] = {}
    for step_index, step in enumerate(spec.steps):
        parts.append(_render_step(spec, step, step_index, rng, radio_names, perturb))
    steps_html = "\n".join(parts)
    progress = ""
    if len(spec.steps) > 1 and spec.trap != "submit_next":
        progress = '<p id="progress">Step 1 of %d</p>' % len(spec.steps)
    modal = _modal() if perturb == "modal" else ""
    extra = ""
    if perturb == "conditional":
        extra = _previous_employer(spec.company, rng)
    boot = " booting" if perturb == "slow" else ""
    title = html.escape(spec.company)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} application</title>
<style>
  body {{ font: 16px/1.4 sans-serif; margin: 2rem; }}
  .step {{ display: none; }}
  .step.active {{ display: block; }}
  label, legend {{ display: block; margin-top: 0.8rem; }}
  input, textarea, select, button {{ font: inherit; }}
  textarea {{ width: 24rem; height: 5rem; }}
  [hidden] {{ display: none !important; }}
  body.booting .step.active input,
  body.booting .step.active textarea,
  body.booting .step.active select,
  body.booting .step.active fieldset,
  body.booting .step.active button[role="combobox"] {{ visibility: hidden; }}
  [role="dialog"] {{ position: fixed; inset: 2rem; background: white; border: 1px solid #222; padding: 1rem; z-index: 5; }}
</style>
</head>
<body class="{boot}">
<h1>{title}</h1>
<p>Fictional application. Not a real employer.</p>
{progress}
<form id="application" method="post" action="/submitted">
{modal}
{steps_html}
{extra}
</form>
<script>
{_script(spec, perturb)}
</script>
</body>
</html>
"""


def _eid(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "n" + "".join(rng.choice(alphabet) for _ in range(10))


def _cls(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "c" + "".join(rng.choice(alphabet) for _ in range(8))


def _render_step(spec: FormSpec, step: StepSpec, index: int, rng: random.Random, radio_names: dict[str, str], perturb: str | None) -> str:
    active = " active" if index == 0 else ""
    body = _render_fields(spec.style, step, rng, radio_names)
    button = _nav_button(spec, index)
    heading = html.escape(step.heading)
    return f'<section class="{_cls(rng)} step{active}"><h2>{heading}</h2>{body}{button}</section>'


def _nav_button(spec: FormSpec, index: int) -> str:
    last = index == len(spec.steps) - 1
    if spec.trap == "submit_next":
        return '<p><button type="submit">Next</button></p>'
    if spec.trap == "autosubmit" and last:
        return '<p><button type="button" id="auto-last">Next</button></p>'
    if last or len(spec.steps) == 1:
        return '<p><button type="submit">Submit application</button></p>'
    return '<p><button type="button" class="next">Next</button></p>'


def _render_fields(style: str, step: StepSpec, rng: random.Random, radio_names: dict[str, str]) -> str:
    chunks = [_control(style, field, rng, radio_names).html for field in step.fields]
    rng.shuffle(chunks)
    inner = "\n".join(chunks)
    if step.repeater != "work":
        return inner
    template_id = _eid(rng)
    host_id = _eid(rng)
    button_id = _eid(rng)
    return (
        f'<div id="{host_id}"></div>'
        f'<template id="{template_id}">{inner}</template>'
        f'<p><button type="button" id="{button_id}">Add another</button></p>'
        f'<script>window.__pocketRepeat = window.__pocketRepeat || [];'
        f'window.__pocketRepeat.push({{template:"{template_id}", host:"{host_id}", button:"{button_id}", start:{step.start_groups}}});</script>'
    )


def _control(style: str, field: FieldSpec, rng: random.Random, radio_names: dict[str, str]) -> _Ctrl:
    label = html.escape(field.label)
    placeholder = html.escape(field.placeholder)
    required = " required" if field.required else ""
    aria = ' aria-required="true"' if field.required else ""
    reveal = None
    wrapper_attr = ""
    if field.reveal_after:
        controller, value = field.reveal_after
        radio_name = radio_names.get(controller, "")
        reveal = f"{radio_name}={value}"
        wrapper_attr = f' data-reveal="{html.escape(reveal)}" hidden'
    control = _control_html(style, field, label, placeholder, required, aria, rng, radio_names)
    return _Ctrl(f'<div class="{_cls(rng)}"{wrapper_attr}>{control}</div>', reveal)


def _control_html(style: str, field: FieldSpec, label: str, placeholder: str, required: str, aria: str, rng: random.Random, radio_names: dict[str, str]) -> str:
    if field.kind == "radio":
        return _radio(field, label, rng, radio_names)
    if field.kind == "checkbox":
        return f'<label><input type="checkbox"{required}{aria}> {label}</label>'
    if field.kind == "combobox":
        return _combobox(field, label, rng)
    if field.kind == "select":
        return _labeled(style, label, _select(field), rng, placeholder)
    if field.kind == "file":
        control = f'<input type="file"{required}{aria}>'
        return _labeled(style, label, control, rng, "")
    if field.kind == "textarea":
        control = f'<textarea{required}{aria} placeholder="{placeholder}"></textarea>'
        return _labeled(style, label, control, rng, placeholder)
    control = f'<input type="text"{required}{aria} placeholder="{placeholder}">'
    return _labeled(style, label, control, rng, placeholder)


def _labeled(style: str, label: str, control: str, rng: random.Random, placeholder: str) -> str:
    if style == "lever":
        # Accessible name is aria-label. Placeholder is a separate hint when it carries a format.
        hinted = control.replace(">", f' aria-label="{label}">', 1)
        return hinted
    if style == "custom":
        return f'<div class="{_cls(rng)}">{label}</div>{control}'
    element_id = _eid(rng)
    control = control.replace(">", f' id="{element_id}">', 1)
    return f'<label for="{element_id}">{label}</label>{control}'


def _select(field: FieldSpec) -> str:
    required = " required" if field.required else ""
    options = ['<option value="">Select one</option>']
    for option in field.options:
        text = html.escape(option)
        options.append(f'<option value="{text}">{text}</option>')
    return f"<select{required}>" + "".join(options) + "</select>"


def _combobox(field: FieldSpec, label: str, rng: random.Random) -> str:
    label_id = _eid(rng)
    list_id = _eid(rng)
    items = []
    for option in field.options:
        text = html.escape(option)
        items.append(f'<li role="option">{text}</li>')
    options = "".join(items)
    required = ' aria-required="true"' if field.required else ""
    return (
        f'<span id="{label_id}">{label}</span>'
        f'<button type="button" role="combobox" aria-labelledby="{label_id}" aria-controls="{list_id}" aria-expanded="false"{required}>Select one</button>'
        f'<ul id="{list_id}" role="listbox" hidden>{options}</ul>'
    )


def _radio(field: FieldSpec, label: str, rng: random.Random, radio_names: dict[str, str]) -> str:
    name = _eid(rng)
    radio_names[field.key] = name
    choices = []
    for option in field.options:
        text = html.escape(option)
        choices.append(f'<label><input type="radio" name="{name}" value="{text}"> {text}</label>')
    star = " *" if field.required else ""
    return f'<fieldset><legend>{label}{star}</legend>{"".join(choices)}</fieldset>'


def _modal() -> str:
    return """
<div role="dialog" aria-modal="true">
  <p>Get alerts for fictional roles at this company?</p>
  <button type="submit">Submit</button>
  <button type="button">No thanks</button>
</div>
"""


def _previous_employer(company: str, rng: random.Random) -> str:
    name = _eid(rng)
    safe = html.escape(company)
    return f"""
<div class="{_cls(rng)}">
  <fieldset>
    <legend>Have you worked at {safe} before?</legend>
    <label><input type="radio" name="{name}" value="Yes"> Yes</label>
    <label><input type="radio" name="{name}" value="No"> No</label>
  </fieldset>
  <div data-reveal="{name}=Yes" hidden>
    <div>Previous team</div>
    <input type="text" aria-label="Previous team">
  </div>
</div>
"""


def _script(spec: FormSpec, perturb: str | None) -> str:
    slow = "document.body.classList.remove('booting');" if perturb == "slow" else ""
    slow_timer = "setTimeout(() => { %s }, 700);" % slow if perturb == "slow" else ""
    validation = _VALIDATION if perturb == "validation" else ""
    auto = ""
    if spec.trap == "autosubmit":
        auto = """
        const autoLast = document.getElementById("auto-last");
        if (autoLast) autoLast.addEventListener("click", () => document.getElementById("application").requestSubmit());
        """
    script = r"""
function pocketUniquify(root) {
  const stamp = Math.random().toString(36).slice(2, 8);
  root.querySelectorAll("[id]").forEach((el) => {
    const old = el.id;
    const next = old + "-" + stamp;
    el.id = next;
    root.querySelectorAll("[for]").forEach((label) => {
      if (label.getAttribute("for") === old) label.setAttribute("for", next);
    });
    root.querySelectorAll("[aria-labelledby]").forEach((node) => {
      if (node.getAttribute("aria-labelledby") === old) node.setAttribute("aria-labelledby", next);
    });
    root.querySelectorAll("[aria-controls]").forEach((node) => {
      if (node.getAttribute("aria-controls") === old) node.setAttribute("aria-controls", next);
    });
  });
}
function pocketAddRepeaters() {
  (window.__pocketRepeat || []).forEach((item) => {
    const template = document.getElementById(item.template);
    const host = document.getElementById(item.host);
    const button = document.getElementById(item.button);
    if (!template || !host || !button) return;
    const add = () => {
      const fragment = template.content.cloneNode(true);
      const wrap = document.createElement("div");
      wrap.appendChild(fragment);
      pocketUniquify(wrap);
      host.appendChild(wrap);
    };
    for (let i = 0; i < item.start; i += 1) add();
    button.addEventListener("click", add);
  });
}
document.addEventListener("click", (event) => {
  const combo = event.target.closest && event.target.closest("[role=combobox]");
  if (combo) {
    const list = document.getElementById(combo.getAttribute("aria-controls"));
    if (list) {
      list.hidden = !list.hidden;
      combo.setAttribute("aria-expanded", list.hidden ? "false" : "true");
    }
    return;
  }
  const option = event.target.closest && event.target.closest("[role=option]");
  if (option) {
    const list = option.closest("[role=listbox]");
    const combo = list && document.querySelector('[aria-controls="' + list.id + '"]');
    const value = (option.textContent || "").trim();
    if (combo) {
      combo.dataset.value = value;
      combo.textContent = value;
      combo.setAttribute("aria-expanded", "false");
    }
    if (list) list.hidden = true;
    return;
  }
  const button = event.target.closest && event.target.closest("button.next");
  if (button) {
    const steps = [...document.querySelectorAll(".step")];
    const current = steps.findIndex((el) => el.classList.contains("active"));
    if (current >= 0 && current < steps.length - 1) {
      steps[current].classList.remove("active");
      steps[current + 1].classList.add("active");
      const progress = document.getElementById("progress");
      if (progress) progress.textContent = "Step " + (current + 2) + " of " + steps.length;
    }
  }
});
document.addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest("[role=dialog] button");
  if (!button) return;
  const text = (button.textContent || "").toLowerCase();
  if (text.includes("no thanks") || text.includes("close") || text.includes("dismiss") || text.includes("not now")) {
    const dialog = button.closest("[role=dialog]");
    if (dialog) dialog.remove();
  }
});
document.addEventListener("change", (event) => {
  const input = event.target;
  if (!input || input.type !== "radio") return;
  document.querySelectorAll("[data-reveal]").forEach((box) => {
    const spec = box.getAttribute("data-reveal") || "";
    const eq = spec.indexOf("=");
    const name = spec.slice(0, eq);
    const value = spec.slice(eq + 1);
    const chosen = document.querySelector('input[name="' + name + '"]:checked');
    box.hidden = !(chosen && chosen.value === value);
  });
});
pocketAddRepeaters();
"""
    return script + "\n" + slow_timer + "\n" + validation + "\n" + auto


_VALIDATION = r"""
document.addEventListener("input", (event) => {
  const el = event.target;
  if (!el || !el.closest) return;
  const blob = ((el.getAttribute("placeholder") || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
  const previous = el.previousElementSibling ? (el.previousElementSibling.textContent || "") : "";
  const labelFor = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
  const named = blob + " " + previous + " " + (labelFor ? labelFor.textContent : "");
  if (!named.toLowerCase().includes("date") && !blob.includes("mm")) return;
  const parent = el.parentElement;
  if (!parent) return;
  let alert = parent.querySelector("[role=alert]");
  const monthName = /^[A-Za-z]+\s+\d{4}$/.test(el.value.trim());
  const bad = /^\d{4}-\d{2}/.test(el.value.trim()) || /^\d{2}\/\d{4}$/.test(el.value.trim());
  if (bad && !monthName) {
    el.setAttribute("aria-invalid", "true");
    if (!alert) {
      alert = document.createElement("div");
      alert.setAttribute("role", "alert");
      alert.textContent = "Use a month name and a year, for example May 2026";
      parent.appendChild(alert);
    }
  } else if (alert) {
    el.removeAttribute("aria-invalid");
    alert.remove();
  }
});
"""
