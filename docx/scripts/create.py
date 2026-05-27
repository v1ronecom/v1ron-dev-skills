#!/usr/bin/env python3
"""
docx/scripts/create.py
Reads JSON from stdin: {topic, purpose, audience, tone, section_count}
Writes NDJSON to stdout: thinking lines + final result line
"""
import base64, io, json, os, re, sys, tempfile, urllib.request, urllib.error

try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    print(json.dumps({"error": "python-docx not installed"}))
    sys.exit(1)

# ── Environment ───────────────────────────────────────────────────────────────
APP_URL = os.environ.get("APP_INTERNAL_URL", "http://v1ron_app:8000")
MCP_KEY = os.environ.get("MCP_API_KEY", "")

# ── Thinking log ──────────────────────────────────────────────────────────────
thinking: list[str] = []

def think(msg: str) -> None:
    thinking.append(msg)
    print(json.dumps({"thinking": msg}), flush=True)

# ── File helpers ──────────────────────────────────────────────────────────────
def fetch_file(url: str, allowed_exts: set | None = None) -> str | None:
    if not url:
        return None
    try:
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
        default_ext = "bin" if allowed_exts is None else next(iter(allowed_exts), "bin")
        if allowed_exts and ext not in allowed_exts:
            ext = default_ext
        fd, path = tempfile.mkstemp(suffix=f".{ext}")
        os.close(fd)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r, open(path, "wb") as f:
            f.write(r.read())
        return path
    except Exception:
        return None

def fetch_image(url: str) -> str | None:
    return fetch_file(url, {"jpg", "jpeg", "png", "gif", "bmp", "webp"})

def extract_theme_from_pptx(url: str) -> dict | None:
    path = fetch_file(url, {"pptx"})
    if not path:
        return None
    try:
        from pptx import Presentation
        ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
        tmpl = Presentation(path)
        clr_scheme = tmpl.slide_masters[0]._element.find(f".//{{{ns}}}clrScheme")
        if clr_scheme is None:
            return None
        colors: dict[str, str] = {}
        for child in clr_scheme:
            tag = child.tag.split("}")[-1]
            for sub in child:
                if sub.tag.split("}")[-1] == "srgbClr":
                    colors[tag] = sub.get("val", "000000").upper()
                    break
        primary   = colors.get("dk1") or colors.get("dk2") or "1E293B"
        secondary = colors.get("accent1") or colors.get("accent2") or "6366F1"
        return {"p": primary, "s": secondary, "fh": "Calibri", "fb": "Calibri"}
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass

# ── LLM helpers ───────────────────────────────────────────────────────────────
def _call_llm_stream(messages: list[dict]):
    req = urllib.request.Request(
        f"{APP_URL}/api/v1/internal/llm/stream",
        data=json.dumps({"messages": messages}).encode(),
        headers={"Content-Type": "application/json", "X-MCP-Key": MCP_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                if "delta" in obj:
                    yield obj["delta"]
            except json.JSONDecodeError:
                continue

def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.MULTILINE).rstrip("` \n")
    text = re.sub(r"\n```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError("No JSON object found")
    return json.loads(text[start:end])

_SYSTEM = """You are a professional document writer and subject-matter expert.
You create well-structured, substantive Word documents with real content — never templates or placeholders."""

_USER = """Create a professional Word document about: **{topic}**

Purpose: {purpose}
Audience: {audience}
Tone: {tone}
Sections: {section_count}

Return ONLY valid JSON with this exact structure:
{{
  "title": "Document title",
  "sections": [
    {{
      "heading": "Section heading",
      "paragraphs": [
        "Full paragraph with real details, examples, and substantive information (3-5 sentences)",
        "Another full paragraph with real content"
      ]
    }}
  ],
  "conclusion": "Conclusion paragraph with real content"
}}

CRITICAL RULES:
- Every paragraph must contain REAL, specific information about the topic
- Include concrete details, examples, data points, and actionable insights
- NEVER use placeholder text like "This section discusses..." or "Insert details here"
- NEVER use generic filler like "This is an important topic" without explaining WHY
- Each section should have 2-3 paragraphs
- Match the requested tone ({tone}) for the target audience ({audience})
- Write as if delivering a finished document to a client"""

def _fallback(topic: str, purpose: str, section_count: int, tone: str) -> dict:
    sections = []
    for i in range(min(section_count, 3)):
        heading = "Overview" if i == 0 else ("Key Considerations" if i == 1 else "Next Steps")
        sections.append({
            "heading": heading,
            "paragraphs": [
                f"{topic} encompasses a range of important concepts that warrant careful consideration. "
                f"Understanding the fundamentals provides a foundation for deeper engagement with the subject matter. "
                f"Professionals in this area recognise that a structured approach yields the most reliable outcomes.",
                f"Practical application of {topic} requires attention to both theoretical frameworks and real-world constraints. "
                f"Experience has shown that organisations benefit from balancing innovation with proven methodologies. "
                f"The landscape continues to evolve as new research and best practices emerge."
            ]
        })
    return {
        "title": topic,
        "sections": sections,
        "conclusion": f"{topic} remains a significant area of focus. "
                      f"Continued attention to developments and best practices will support informed decision-making going forward."
    }

def generate_content(topic: str, purpose: str, audience: str, tone: str, section_count: int) -> dict:
    prompt = _USER.format(topic=topic, purpose=purpose, audience=audience, tone=tone, section_count=section_count)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ]
    full_text = ""
    think("Calling AI to generate document content...")

    try:
        for delta in _call_llm_stream(messages):
            full_text += delta
    except Exception as exc:
        think(f"LLM call failed ({exc}); using fallback")
        return _fallback(topic, purpose, section_count, tone)

    think("Processing AI-generated content...")
    try:
        return _extract_json(full_text)
    except Exception as exc:
        think(f"JSON parse failed ({exc}); using fallback")
        return _fallback(topic, purpose, section_count, tone)

# ── python-docx helpers ───────────────────────────────────────────────────────
def _rgb(h: str) -> RGBColor:
    h = h.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def _add_para(doc, text: str, bold: bool = False, italic: bool = False,
              size: float | None = None, color: str | None = None,
              font: str | None = None, alignment=None, space_after: float | None = None):
    p = doc.add_paragraph()
    if alignment is not None:
        p.alignment = alignment
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = _rgb(color)
    if font:
        run.font.name = font
    p.paragraph_format.space_after = Pt(6)
    return p

# ── Build document ────────────────────────────────────────────────────────────
def build_docx(content: dict, theme: dict | None = None, img_path: str | None = None) -> bytes:
    think("Building Word document...")
    doc = Document()

    # Default theme
    t = theme or {"p": "1E293B", "s": "6366F1", "fh": "Calibri", "fb": "Calibri"}

    # Page setup
    section = doc.sections[0]
    section.page_height = Inches(11)
    section.page_width = Inches(8.5)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)

    # Normal style
    style = doc.styles["Normal"]
    style.font.name = t.get("fb", "Calibri")
    style.font.size = Pt(11)

    # Title
    title = doc.add_heading(content.get("title", "Document"), level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.color.rgb = _rgb(t["p"])
        run.font.name = t.get("fh", "Calibri")

    # Sections
    for i, sec in enumerate(content.get("sections", [])):
        heading = sec.get("heading", f"Section {i+1}")
        think(f"Writing section {i+1}: {heading}")
        doc.add_heading(heading, level=1)
        for para_text in sec.get("paragraphs", []):
            _add_para(doc, para_text, font=t.get("fb", "Calibri"))

    # Conclusion
    think("Writing conclusion...")
    doc.add_heading("Conclusion", level=1)
    _add_para(doc, content.get("conclusion", ""), font=t.get("fb", "Calibri"))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()

# ── HTML companion ────────────────────────────────────────────────────────────
def build_html_doc(content: dict, theme: dict | None = None) -> str:
    t = theme or {"p": "1E293B", "s": "6366F1", "fh": "Calibri", "fb": "Calibri"}
    primary = f"#{t['p']}"
    accent  = f"#{t['s']}"
    title   = content.get("title", "Document")
    sections = content.get("sections", [])
    conclusion = content.get("conclusion", "")

    def _esc(s: str) -> str:
        return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    sections_html = ""
    for sec in sections:
        heading = _esc(sec.get("heading", ""))
        paras = "".join(f"<p>{_esc(p)}</p>" for p in sec.get("paragraphs", []))
        sections_html += f"<section><h2>{heading}</h2>{paras}</section>"

    if conclusion:
        sections_html += f"<section><h2>Conclusion</h2><p>{_esc(conclusion)}</p></section>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:Georgia,serif;background:#f8f9fa;color:#1a1a2e;line-height:1.75}}
  header{{background:{primary};color:#fff;padding:60px 40px 40px;text-align:center}}
  header h1{{font-size:clamp(1.6rem,4vw,2.6rem);font-weight:700;letter-spacing:-.5px}}
  .doc-body{{max-width:760px;margin:0 auto;padding:48px 32px 80px}}
  section{{margin-bottom:40px}}
  h2{{font-size:1.25rem;font-weight:700;color:{primary};border-left:4px solid {accent};
      padding-left:12px;margin-bottom:14px}}
  p{{font-size:1rem;margin-bottom:12px;color:#333}}
  footer{{text-align:center;padding:24px;font-size:.8rem;color:#888;
          border-top:1px solid #ddd;margin-top:40px}}
  @media print{{header{{padding:32px 20px}}footer{{display:none}}}}
  @media(max-width:600px){{.doc-body{{padding:28px 16px 48px}}}}
</style>
</head>
<body>
<header><h1>{_esc(title)}</h1></header>
<div class="doc-body">{sections_html}</div>
<footer>Generated by V1RON</footer>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Phase 1: Parse inputs
    inputs = json.loads(sys.stdin.read())
    topic = (inputs.get("topic") or "").strip()
    purpose = inputs.get("purpose", "report")
    audience = inputs.get("audience", "general")
    tone = inputs.get("tone", "professional")
    section_count = min(max(int(inputs.get("section_count") or 3), 2), 6)

    if not topic:
        print(json.dumps({"error": "topic is required"}))
        sys.exit(1)

    think(f"Document: {topic}")
    think(f"Purpose: {purpose} | Audience: {audience} | Tone: {tone}")

    # Phase 2: Collect reference files
    image_urls = inputs.get("image_urls", [])
    images = [u for u in image_urls if any(u.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".gif", ".webp"))]
    templates = [u for u in image_urls if u.lower().endswith(".pptx")]

    ref_img_path = fetch_image(images[0]) if images else None
    theme_override = extract_theme_from_pptx(templates[0]) if templates else None

    # Phase 3: LLM generates content
    content = generate_content(topic, purpose, audience, tone, section_count)

    # Phase 4: Resolve theme
    theme = theme_override or {"p": "1E293B", "s": "6366F1", "fh": "Calibri", "fb": "Calibri"}

    # Phase 5: Build document
    doc_bytes = build_docx(content, theme, ref_img_path)

    # Phase 6: Cleanup + export
    if ref_img_path:
        try:
            os.unlink(ref_img_path)
        except Exception:
            pass

    think("Encoding and finalising...")
    content_base64 = base64.b64encode(doc_bytes).decode()

    think("Generating HTML companion...")
    html_src = build_html_doc(content, theme)
    html_base64 = base64.b64encode(html_src.encode("utf-8")).decode()

    safe = re.sub(r"[^\w-]", "_", topic)[:40]
    print(json.dumps({
        "filename":       f"{safe}.docx",
        "content_base64": content_base64,
        "mime_type":      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "html_filename":  f"{safe}.html",
        "html_base64":    html_base64,
        "thinking":       thinking,
    }))

if __name__ == "__main__":
    main()
