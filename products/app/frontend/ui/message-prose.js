/* OH safe prose/MathML renderer, adapted locally for RH classic/offline loading.
 * Model text is only textContent or a restricted public link. Raw HTML is never
 * inserted; financial numbers and research terminology are not normalized. */
(function () {
"use strict";
function createAssistantFormula(source, display = false) {
  // A deliberately small offline MathML vocabulary. Unknown notation remains
  // visible as its original text; model content never becomes HTML or script.
  if (source.length > 4000) return null;
  const namespace = "http://www.w3.org/1998/Math/MathML";
  const node = (tag, ...children) => {
    const element = document.createElementNS(namespace, tag);
    for (const child of children) element.append(typeof child === "string" ? document.createTextNode(child) : child);
    return element;
  };
  const symbols = { Delta:"Δ", Gamma:"Γ", Theta:"Θ", Pi:"Π", Sigma:"Σ", Omega:"Ω", delta:"δ", gamma:"γ", theta:"θ", rho:"ρ", sigma:"σ", mu:"μ", pi:"π", alpha:"α", beta:"β", lambda:"λ", tau:"τ", epsilon:"ε", le:"≤", leq:"≤", ge:"≥", geq:"≥", ne:"≠", neq:"≠", cdot:"⋅", times:"×", pm:"±", infty:"∞", in:"∈", notin:"∉", partial:"∂", approx:"≈", to:"→", sum:"∑", prod:"∏", int:"∫" };
  const tokens = source.match(/\\[a-zA-Z]+|\\.|\d+(?:\.\d+)?|[^\s]/g) || [];
  let index = 0, depth = 0;
  const atom = () => {
    if (++depth > 32) throw new Error("Formula nesting limit");
    const token = tokens[index++];
    let element;
    if (token === "{") {
      element = row("}");
    } else if (token === "\\frac" || token === "\\dfrac" || token === "\\tfrac") {
      element = node("mfrac", atom(), atom());
    } else if (token === "\\sqrt") {
      element = node("msqrt", atom());
    } else if (["\\mathcal", "\\mathbb"].includes(token)) {
      // Font commands consume one atom, while scripts keep their own structure.
      element = node("mstyle", atom());
      element.setAttribute("mathvariant", token === "\\mathbb" ? "double-struck" : "script");
    } else if (["\\text", "\\mathrm", "\\operatorname", "\\mathbf"].includes(token)) {
      const content = atom();
      element = node("mtext", content.textContent);
    } else if (token?.startsWith("\\")) {
      const command = token.slice(1);
      if (symbols[command]) element = node(/[a-zA-Z]/.test(command[0]) && /^(?:Delta|Gamma|Theta|Pi|Sigma|Omega|delta|gamma|theta|rho|sigma|mu|pi|alpha|beta|lambda|tau|epsilon)$/.test(command) ? "mi" : "mo", symbols[command]);
      else if (["max", "min", "log", "ln", "exp", "sin", "cos", "Pr"].includes(command)) element = node("mi", command);
      else if (["left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr"].includes(command)) element = atom();
      else if ([",", ";", "!", " ", "quad", "qquad"].includes(command)) element = node("mspace");
      else if (["{", "}", "|", "%", "_"].includes(command)) element = node("mo", command);
      else throw new Error("Unsupported formula command");
    } else if (!token || token === "}") {
      throw new Error("Unbalanced formula");
    } else {
      element = node(/^\d/.test(token) ? "mn" : /^[a-zA-Z\u0370-\u03ff]$/.test(token) ? "mi" : "mo", token);
    }
    depth -= 1;
    return element;
  };
  const row = closing => {
    const element = node("mrow");
    while (index < tokens.length && tokens[index] !== closing) {
      let base = atom(), sub = null, sup = null;
      while (tokens[index] === "_" || tokens[index] === "^") {
        const script = tokens[index++], value = atom();
        if (script === "_") { if (sub) throw new Error("Duplicate subscript"); sub = value; }
        else { if (sup) throw new Error("Duplicate superscript"); sup = value; }
      }
      if (sub && sup) base = node("msubsup", base, sub, sup);
      else if (sub || sup) base = node(sub ? "msub" : "msup", base, sub || sup);
      element.append(base);
    }
    if (closing && tokens[index++] !== closing) throw new Error("Unbalanced formula");
    return element;
  };
  try {
    const math = node("math", row(null));
    math.setAttribute("display", display ? "block" : "inline");
    math.setAttribute("aria-label", source);
    return math;
  } catch { return null; }
}

function appendAssistantText(container, text) {
  // RH keeps original research numbers and terms; no OH business normalization.
  text = String(text ?? "");
  // Render a small, safe prose vocabulary. Model HTML never enters innerHTML.
  const inline = (parent, value) => {
    const tokens = String(value).split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+\$(?!\d)|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\))/g);
    for (const token of tokens) {
      const link = /^\[([^\]\n]+)\]\(([^\s)]+)\)$/.exec(token);
      if (link && /^(?:https?:\/\/|#[A-Za-z][\w-]*$)/i.test(link[2])) {
        const anchor = document.createElement("a");
        anchor.textContent = link[1]; anchor.setAttribute("href", link[2]);
        if (!link[2].startsWith("#")) { anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; }
        parent.append(anchor); continue;
      }
      const formula = /^(?:\$\$[\s\S]+\$\$|\$[^$\n]+\$|\\\([\s\S]+\\\)|\\\[[\s\S]+\\\])$/.test(token);
      const display = token.startsWith("$$") || token.startsWith("\\[");
      const trim = token.startsWith("$") && !display ? 1 : 2;
      const math = formula ? createAssistantFormula(token.slice(trim, -trim), display) : null;
      if (math) { parent.append(math); continue; }
      const tag = token.startsWith("**") && token.endsWith("**") ? "strong" : token.startsWith("`") && token.endsWith("`") ? "code" : "span";
      const node = document.createElement(tag);
      if (tag === "strong") inline(node, token.slice(2, -2));
      else node.textContent = tag === "code" ? token.slice(1, -1) : token;
      parent.append(node);
    }
  };
  const lines = String(text).split("\n");
  let paragraph = [], list = null;
  const flush = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    inline(p, paragraph.join("\n"));
    container.append(p);
    paragraph = [];
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (line === "$$" || line === "\\[") {
      flush(); list = null;
      const closing = line === "$$" ? "$$" : "\\]", values = [];
      let end = index + 1;
      while (end < lines.length && lines[end].trim() !== closing) values.push(lines[end++]);
      if (end < lines.length) {
        const math = createAssistantFormula(values.join("\n").trim(), true);
        if (math) container.append(math);
        else { const fallback = document.createElement("p"); fallback.textContent = [line, ...values, closing].join("\n"); container.append(fallback); }
        index = end; continue;
      }
      // No closing delimiter: preserve the remaining text instead of swallowing it.
      paragraph.push(line); continue;
    }
    if (line.startsWith("```")) {
      flush(); list = null;
      const code = document.createElement("code"), pre = document.createElement("pre"), values = [];
      while (++index < lines.length && !lines[index].trim().startsWith("```")) values.push(lines[index]);
      code.textContent = values.join("\n"); pre.append(code); container.append(pre);
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index+1] || "")) {
      flush(); list = null;
      const wrap = document.createElement("div"), table = document.createElement("table");
      wrap.className = "message-prose__table";
      wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "回答中的数据表");
      const head = document.createElement("thead"), body = document.createElement("tbody");
      table.append(head, body);
      // A literal escaped pipe or a code-span pipe is cell content, not a
      // column boundary. Keep its original meaning when constructing the DOM.
      const cells = value => {
        const result = [], input = value.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "");
        let cell = "", code = false;
        for (let cursor = 0; cursor < input.length; cursor++) {
          const character = input[cursor];
          if (character === "\\" && input[cursor + 1] === "|") { cell += "|"; cursor++; }
          else if (character === "`") { code = !code; cell += character; }
          else if (character === "|" && !code) { result.push(cell); cell = ""; }
          else cell += character;
        }
        result.push(cell); return result;
      };
      const addRow = (value, tag, section) => {
        const row = document.createElement("tr");
        for (const cell of cells(value)) { const item = document.createElement(tag); if (tag === "th") item.scope = "col"; inline(item, cell.trim()); row.append(item); }
        section.append(row);
      };
      addRow(line, "th", head); index += 1;
      while (index+1 < lines.length && lines[index+1].includes("|")) addRow(lines[++index], "td", body);
      wrap.append(table); container.append(wrap); continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    const bullet = /^(?:([-*+])|(\d+)[.)])\s+(.+)$/.exec(line);
    if (heading) {
      flush(); list = null;
      const title = document.createElement(heading[1].length < 3 ? "h3" : "h4");
      inline(title, heading[2]); container.append(title);
    } else if (bullet) {
      flush();
      const tag = bullet[2] ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag) { list = document.createElement(tag); container.append(list); }
      const item = document.createElement("li");
      if (bullet[2]) item.value = Number(bullet[2]);
      inline(item, bullet[3]); list.append(item);
    } else if (!line || /^[-*_]{3,}$/.test(line)) {
      flush(); list = null;
    } else {
      list = null; paragraph.push(line.replace(/^>\s?/, ""));
    }
  }
  flush();
}

function renderHTML(text) {
  const container = document.createElement("div");
  appendAssistantText(container, text);
  return container.innerHTML;
}
window.RHMessageProse = Object.freeze({append: appendAssistantText, renderHTML});
})();

