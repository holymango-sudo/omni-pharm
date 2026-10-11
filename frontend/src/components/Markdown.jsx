import React from "react";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>")
  .replace(/_(.+?)_/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>");

/** Minimal, safe markdown (headings, bullets, bold/italic) for the streamed LLM report. */
export function mdToHtml(md) {
  const out = [];
  let list = 0;
  for (const raw of md.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const m = line.match(/^(\s*)[-*•]\s+(.*)$/) || line.match(/^(\s*)\d+[.)]\s+(.*)$/);
    if (m) {
      const depth = m[1].length >= 2 ? 2 : 1;
      while (list < depth) { out.push("<ul>"); list++; }
      while (list > depth) { out.push("</ul>"); list--; }
      out.push(`<li>${inline(m[2])}</li>`);
      continue;
    }
    while (list > 0) { out.push("</ul>"); list--; }
    const h = line.match(/^\s*#{1,6}\s*(.*)$/);
    if (h) out.push(`<h4>${inline(h[1].replace(/^[#\s]+/, ""))}</h4>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  while (list-- > 0) out.push("</ul>");
  return out.join("");
}

export default function Markdown({ text, cursor }) {
  return <div className="md" dangerouslySetInnerHTML={{ __html: mdToHtml(text) + (cursor ? '<span class="cursor">▌</span>' : "") }} />;
}
