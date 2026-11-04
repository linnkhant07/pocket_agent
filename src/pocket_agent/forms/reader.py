"""Read form controls from a live page. No model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pocket_agent.forms.match import section_of

_EXTRACT = r"""
() => {
  document.querySelectorAll("[data-pocket-uid]").forEach((el) => el.removeAttribute("data-pocket-uid"));
  const textOf = (el) => ((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim();
  const clean = (value) => (value || "").replace(/\*/g, "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    if (!el || el.closest("template")) return false;
    if (el.closest("[hidden]")) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const heading = (el) => {
    let best = "";
    for (const node of document.querySelectorAll("h1,h2,h3")) {
      if (el.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) best = textOf(node);
    }
    return best;
  };
  let seq = 0;
  const stamp = (el) => {
    const id = "pk" + (++seq);
    el.setAttribute("data-pocket-uid", id);
    return id;
  };
  const labelFor = (el) => {
    const aria = el.getAttribute && el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const labelled = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelled) {
      const text = labelled.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(textOf).join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && clean(textOf(lab))) return clean(textOf(lab));
    }
    const wrap = el.closest && el.closest("label");
    if (wrap) {
      const clone = wrap.cloneNode(true);
      clone.querySelectorAll("input,select,textarea,button").forEach((node) => node.remove());
      if (clean(textOf(clone))) return clean(textOf(clone));
    }
    const prev = el.previousElementSibling;
    if (prev && clean(textOf(prev)) && clean(textOf(prev)).length < 180) return clean(textOf(prev));
    const placeholder = el.getAttribute && el.getAttribute("placeholder");
    if (placeholder && !/mm|yyyy/i.test(placeholder)) return clean(placeholder);
    return "";
  };
  const fields = [];
  const consumed = new Set();
  const radios = [...document.querySelectorAll("input[type=radio]")].filter(visible);
  const groups = new Map();
  for (const radio of radios) {
    const name = radio.name || stamp(radio);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(radio);
    consumed.add(radio);
  }
  for (const group of groups.values()) {
    const fieldset = group[0].closest("fieldset");
    const legend = fieldset && fieldset.querySelector("legend");
    const label = legend ? clean(textOf(legend)) : labelFor(group[0]);
    const options = group.map((radio) => ({ label: labelFor(radio) || radio.value, uid: stamp(radio) }));
    const checked = group.find((radio) => radio.checked);
    fields.push({
      uid: options[0].uid,
      order: group[0].getBoundingClientRect().top * 1000 + group[0].getBoundingClientRect().left,
      label,
      kind: "radio",
      required: group.some((radio) => radio.required) || (legend ? legend.textContent.includes("*") : false),
      options,
      placeholder: "",
      heading: heading(group[0]),
      value: checked ? (labelFor(checked) || checked.value) : "",
      input_type: "radio",
    });
  }
  const controls = [...document.querySelectorAll("input, textarea, select, button[role=combobox]")];
  for (const el of controls) {
    if (consumed.has(el) || !visible(el)) continue;
    const type = (el.getAttribute("type") || el.type || "text").toLowerCase();
    if (type === "password") continue;
    if (["hidden", "submit", "button", "reset", "radio", "checkbox"].includes(type) && el.getAttribute("role") !== "combobox") {
      if (type !== "checkbox") continue;
    }
    const label = labelFor(el);
    const placeholder = el.getAttribute("placeholder") || "";
    let kind = "text";
    let options = [];
    let value = "";
    if (el.getAttribute("role") === "combobox") {
      kind = "combobox";
      const list = document.getElementById(el.getAttribute("aria-controls"));
      options = list ? [...list.querySelectorAll("[role=option]")].map((opt) => ({ label: clean(textOf(opt)), uid: stamp(opt) })) : [];
      const raw = clean(el.dataset.value || textOf(el));
      value = /^(select one|select|choose one|choose)$/i.test(raw) ? "" : raw;
    } else if (el.tagName === "SELECT") {
      kind = "select";
      options = [...el.options].map((opt) => clean(opt.text)).filter((text) => text && !/^select one$/i.test(text)).map((text) => ({ label: text, uid: "" }));
      const selected = el.selectedOptions[0];
      value = selected && selected.value ? clean(selected.text) : "";
    } else if (type === "checkbox") {
      kind = "checkbox";
      value = el.checked ? "Yes" : "No";
    } else if (type === "file") {
      kind = "file";
      value = el.files && el.files.length ? "uploaded" : "";
    } else if ((el.getAttribute("aria-autocomplete") || "") === "list") {
      kind = "typeahead";
      value = el.value || "";
    } else if (el.tagName === "TEXTAREA") {
      kind = "textarea";
      value = el.value || "";
    } else {
      kind = "text";
      value = el.value || "";
      if (type === "date" || /date/i.test(label) || /mm|yyyy/i.test(placeholder)) kind = "date";
    }
    fields.push({
      uid: stamp(el),
      order: el.getBoundingClientRect().top * 1000 + el.getBoundingClientRect().left,
      label,
      kind,
      required: el.required || el.getAttribute("aria-required") === "true" || /\*/.test(label),
      options,
      placeholder,
      heading: heading(el),
      value,
      input_type: type,
      list_id: el.getAttribute("aria-controls") || "",
    });
  }
  const buttons = [];
  for (const button of document.querySelectorAll("button")) {
    if (!visible(button) || button.getAttribute("role") === "combobox") continue;
    buttons.push({
      uid: stamp(button),
      text: clean(textOf(button) || button.getAttribute("aria-label") || button.getAttribute("title") || ""),
      type: button.type || "submit",
      onclick: button.getAttribute("onclick") || "",
      heading: heading(button),
      in_dialog: !!button.closest("[role=dialog]"),
    });
  }
  fields.sort((a, b) => a.order - b.order);
  const progressNode = document.getElementById("progress");
  const progress = progressNode ? textOf(progressNode) : "";
  const alerts = [...document.querySelectorAll("[role=alert]")].filter(visible).map(textOf);
  const blob = (document.body.innerText || "").toLowerCase();
  let gate = "";
  if (/captcha|i'm not a robot|i am not a robot|verify you are human/.test(blob)) gate = "captcha";
  else if (document.querySelector('input[type="password"]') || /create an account|create your account|sign in to apply|account already exists|already have an account/.test(blob)) gate = "login";
  return { fields, buttons, progress, alerts, gate };
}
"""


@dataclass
class ExtractedField:
    uid: str
    label: str
    kind: str
    required: bool
    options: list[dict[str, str]]
    placeholder: str
    heading: str
    value: str
    input_type: str
    list_id: str = ""
    section: str = ""
    group: int = 0


@dataclass
class ExtractedButton:
    uid: str
    text: str
    button_type: str
    onclick: str
    heading: str
    in_dialog: bool


@dataclass
class PageSnapshot:
    fields: list[ExtractedField] = field(default_factory=list)
    buttons: list[ExtractedButton] = field(default_factory=list)
    progress: str = ""
    alerts: list[str] = field(default_factory=list)
    gate: str = ""


def extract_page(page) -> PageSnapshot:
    raw = page.evaluate(_EXTRACT)
    fields = [
        ExtractedField(
            uid=item["uid"],
            label=item["label"],
            kind=item["kind"],
            required=bool(item["required"]),
            options=list(item["options"]),
            placeholder=item["placeholder"],
            heading=item["heading"],
            value=item["value"] or "",
            input_type=item["input_type"],
            list_id=item.get("list_id") or "",
        )
        for item in raw["fields"]
        if item["label"]
    ]
    _assign_groups(fields)
    buttons = [
        ExtractedButton(
            uid=item["uid"],
            text=item["text"],
            button_type=item["type"],
            onclick=item["onclick"],
            heading=item["heading"],
            in_dialog=bool(item["in_dialog"]),
        )
        for item in raw["buttons"]
    ]
    return PageSnapshot(
        fields=fields,
        buttons=buttons,
        progress=raw["progress"],
        alerts=list(raw["alerts"]),
        gate=raw.get("gate") or "",
    )


def _assign_groups(fields: list[ExtractedField]) -> None:
    seen: dict[tuple[str, str], int] = {}
    for item in fields:
        item.section = section_of(item.heading)
        label = re.sub(r"\s+", " ", item.label).strip().casefold()
        key = (item.section, label)
        item.group = seen.get(key, 0)
        seen[key] = item.group + 1
