import { marked } from "marked";

const ALLOWED_TAGS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "ul",
  "ol",
  "li",
  "pre",
  "code",
  "blockquote",
  "strong",
  "em",
  "a",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "hr",
  "br",
];

const ALLOWED_ATTR = ["href", "title", "rel", "target"];

type DomPurifyInstance = typeof import("dompurify").default;
let domPurifyPromise: Promise<DomPurifyInstance> | undefined;

function loadDOMPurify(): Promise<DomPurifyInstance> {
  domPurifyPromise ??= import("dompurify").then((mod) => mod.default ?? mod);
  return domPurifyPromise;
}

interface ElementWithSetHTML extends HTMLElement {
  setHTML?: (html: string, options?: { sanitizer?: { elements: string[]; attributes: string[] } }) => void;
}

export async function renderSafeMarkdownInto(target: HTMLElement, markdown: string): Promise<void> {
  const rawHtml = marked.parse(markdown, { async: false, gfm: true, breaks: true });
  const el = target as ElementWithSetHTML;

  if ("setHTML" in Element.prototype && typeof el.setHTML === "function") {
    el.setHTML(rawHtml, {
      sanitizer: {
        elements: ALLOWED_TAGS,
        attributes: ALLOWED_ATTR,
      },
    });
    return;
  }

  const DOMPurify = await loadDOMPurify();
  el.innerHTML = DOMPurify.sanitize(rawHtml, {
    ALLOWED_TAGS: [...ALLOWED_TAGS, "#text"],
    ALLOWED_ATTR,
    ALLOW_ARIA_ATTR: false,
    ALLOW_DATA_ATTR: false,
    KEEP_CONTENT: false,
  });
}
