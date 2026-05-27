#!/usr/bin/env python3
"""
pdf/scripts/create.py
Reads JSON from stdin: {task, content, file_url, file_urls, pages, password, options}
Writes NDJSON to stdout: thinking lines + final result line
"""
import base64, io, json, os, re, sys, tempfile, urllib.request

# ── Environment ───────────────────────────────────────────────────────────────
APP_URL = os.environ.get("APP_INTERNAL_URL", "http://v1ron_app:8000")
MCP_KEY = os.environ.get("MCP_API_KEY", "")

# ── Thinking log ──────────────────────────────────────────────────────────────
thinking: list[str] = []

def think(msg: str) -> None:
    thinking.append(msg)
    print(json.dumps({"thinking": msg}), flush=True)

def fail(msg: str) -> None:
    print(json.dumps({"error": msg}))
    sys.exit(1)

# ── File helpers ───────────────────────────────────────────────────────────────
def _internal_url(url: str) -> str | None:
    """Rewrite a public media URL to the internal app URL."""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    if parsed.path.startswith("/media/"):
        return f"{APP_URL}{parsed.path}"
    return None

def fetch_url(url: str, suffix: str = ".pdf") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    candidates = [url]
    alt = _internal_url(url)
    if alt and alt != url:
        candidates.append(alt)
    last_exc: Exception = RuntimeError("No URL to fetch")
    for candidate in candidates:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            if candidate.startswith(APP_URL) and MCP_KEY:
                headers["X-MCP-Key"] = MCP_KEY
            req = urllib.request.Request(candidate, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
                f.write(r.read())
            return path
        except Exception as exc:
            last_exc = exc
            continue
    os.unlink(path)
    raise RuntimeError(f"Failed to fetch PDF (tried {len(candidates)} URL(s)): {last_exc}")

# ── LLM helper ────────────────────────────────────────────────────────────────
def call_llm(messages: list[dict]) -> str:
    req = urllib.request.Request(
        f"{APP_URL}/api/v1/internal/llm/stream",
        data=json.dumps({"messages": messages}).encode(),
        headers={"Content-Type": "application/json", "X-MCP-Key": MCP_KEY},
        method="POST",
    )
    result = ""
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
                    result += obj["delta"]
            except json.JSONDecodeError:
                continue
    return result

def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.MULTILINE).rstrip("` \n")
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError("No JSON found")
    return json.loads(text[start:end])

# ── Page range parser ─────────────────────────────────────────────────────────
def parse_pages(spec: str, total: int) -> list[int]:
    """Return 0-based page indices from a spec like '1-3,5,7-9'."""
    indices = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            indices.extend(range(int(a) - 1, min(int(b), total)))
        else:
            idx = int(part) - 1
            if 0 <= idx < total:
                indices.append(idx)
    return indices

# ── Task: create PDF ──────────────────────────────────────────────────────────
def task_create(content: str, options: dict) -> tuple[bytes, str]:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
    except ImportError:
        fail("reportlab not installed — run: pip install reportlab")

    think("Calling AI to generate PDF content...")
    messages = [
        {"role": "system", "content": "You are a professional document writer. Return only valid JSON."},
        {"role": "user", "content": (
            f"Create a professional document about: {content}\n\n"
            "Return ONLY valid JSON:\n"
            '{"title": "...", "sections": [{"heading": "...", "paragraphs": ["...", "..."]}], "conclusion": "..."}'
        )},
    ]
    try:
        raw = call_llm(messages)
        doc_data = extract_json(raw)
    except Exception as exc:
        think(f"LLM failed ({exc}); using minimal content")
        doc_data = {
            "title": content,
            "sections": [{"heading": "Overview", "paragraphs": [content]}],
            "conclusion": "",
        }

    think("Building PDF...")
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch)
    story = []
    story.append(Paragraph(doc_data.get("title", content), styles["Title"]))
    story.append(Spacer(1, 12))
    for sec in doc_data.get("sections", []):
        story.append(Paragraph(sec.get("heading", ""), styles["Heading2"]))
        for para in sec.get("paragraphs", []):
            story.append(Paragraph(para, styles["Normal"]))
            story.append(Spacer(1, 6))
    if doc_data.get("conclusion"):
        story.append(Paragraph("Conclusion", styles["Heading2"]))
        story.append(Paragraph(doc_data["conclusion"], styles["Normal"]))
    doc.build(story)
    buf.seek(0)
    safe = re.sub(r"[^\w-]", "_", content)[:40]
    return buf.read(), f"{safe}.pdf"

# ── Task: extract text ────────────────────────────────────────────────────────
def task_extract_text(file_url: str, pages_spec: str | None, options: dict) -> tuple[bytes, str]:
    try:
        import pdfplumber
    except ImportError:
        fail("pdfplumber not installed — run: pip install pdfplumber")

    think(f"Downloading PDF from {file_url}...")
    path = fetch_url(file_url)
    try:
        think("Extracting text...")
        lines = []
        with pdfplumber.open(path) as pdf:
            total = len(pdf.pages)
            indices = parse_pages(pages_spec, total) if pages_spec else list(range(total))
            for i in indices:
                page = pdf.pages[i]
                text = page.extract_text() or ""
                lines.append(f"--- Page {i+1} ---\n{text}")
        result = "\n\n".join(lines)
        return result.encode("utf-8"), "extracted_text.txt"
    finally:
        os.unlink(path)

# ── Task: merge PDFs ──────────────────────────────────────────────────────────
def task_merge(file_urls: list[str], options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        fail("pypdf not installed — run: pip install pypdf")

    writer = PdfWriter()
    paths = []
    try:
        for i, url in enumerate(file_urls):
            think(f"Downloading file {i+1}/{len(file_urls)}...")
            path = fetch_url(url.strip())
            paths.append(path)
            reader = PdfReader(path)
            for page in reader.pages:
                writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        return buf.read(), "merged.pdf"
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except Exception:
                pass

# ── Task: split PDF ───────────────────────────────────────────────────────────
def task_split(file_url: str, pages_spec: str | None, options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
        import zipfile
    except ImportError:
        fail("pypdf not installed — run: pip install pypdf")

    think(f"Downloading PDF...")
    path = fetch_url(file_url)
    try:
        reader = PdfReader(path)
        total = len(reader.pages)
        indices = parse_pages(pages_spec, total) if pages_spec else list(range(total))

        if len(indices) == 1:
            # Return single page as PDF
            writer = PdfWriter()
            writer.add_page(reader.pages[indices[0]])
            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            return buf.read(), f"page_{indices[0]+1}.pdf"

        # Return multiple pages as a zip
        import zipfile
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in indices:
                think(f"Extracting page {i+1}...")
                writer = PdfWriter()
                writer.add_page(reader.pages[i])
                page_buf = io.BytesIO()
                writer.write(page_buf)
                zf.writestr(f"page_{i+1:03d}.pdf", page_buf.getvalue())
        zip_buf.seek(0)
        return zip_buf.read(), "split_pages.zip"
    finally:
        os.unlink(path)

# ── Task: rotate pages ────────────────────────────────────────────────────────
def task_rotate(file_url: str, pages_spec: str | None, options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        fail("pypdf not installed — run: pip install pypdf")

    rotation = int(options.get("rotation", 90))
    think(f"Downloading PDF...")
    path = fetch_url(file_url)
    try:
        reader = PdfReader(path)
        total = len(reader.pages)
        rotate_indices = set(parse_pages(pages_spec, total) if pages_spec else range(total))
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i in rotate_indices:
                page.rotate(rotation)
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        return buf.read(), "rotated.pdf"
    finally:
        os.unlink(path)

# ── Task: encrypt ─────────────────────────────────────────────────────────────
def task_encrypt(file_url: str, password: str, options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        fail("pypdf not installed — run: pip install pypdf")

    if not password:
        fail("password is required for encrypt task")
    think("Downloading PDF...")
    path = fetch_url(file_url)
    try:
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        return buf.read(), "encrypted.pdf"
    finally:
        os.unlink(path)

# ── Task: decrypt ─────────────────────────────────────────────────────────────
def task_decrypt(file_url: str, password: str, options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        fail("pypdf not installed — run: pip install pypdf")

    if not password:
        fail("password is required for decrypt task")
    think("Downloading PDF...")
    path = fetch_url(file_url)
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt(password)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        return buf.read(), "decrypted.pdf"
    finally:
        os.unlink(path)

# ── Task: watermark ────────────────────────────────────────────────────────────
def task_watermark(file_url: str, options: dict) -> tuple[bytes, str]:
    try:
        from pypdf import PdfWriter, PdfReader
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        fail("pypdf and reportlab required — run: pip install pypdf reportlab")

    text = options.get("watermark_text", "CONFIDENTIAL")
    think(f"Creating watermark: {text}")

    # Build watermark PDF in memory
    wm_buf = io.BytesIO()
    c = rl_canvas.Canvas(wm_buf, pagesize=letter)
    w, h = letter
    c.setFont("Helvetica-Bold", 48)
    c.setFillColorRGB(0.8, 0.8, 0.8, alpha=0.4)
    c.saveState()
    c.translate(w / 2, h / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, text)
    c.restoreState()
    c.save()
    wm_buf.seek(0)
    wm_page = PdfReader(wm_buf).pages[0]

    think("Downloading PDF and applying watermark...")
    path = fetch_url(file_url)
    try:
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(wm_page)
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        return buf.read(), "watermarked.pdf"
    finally:
        os.unlink(path)

# ── Main ──────────────────────────────────────────────────────────────────────
MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".zip": "application/zip",
}

def main():
    inputs = json.loads(sys.stdin.read())
    task = (inputs.get("task") or "").strip().lower().replace(" ", "_")
    content = (inputs.get("content") or "").strip()
    file_url = (inputs.get("file_url") or "").strip()
    file_urls_raw = (inputs.get("file_urls") or "").strip()
    file_urls = [u.strip() for u in file_urls_raw.split(",") if u.strip()] if file_urls_raw else []
    pages_spec = (inputs.get("pages") or "").strip() or None
    password = (inputs.get("password") or "").strip()

    raw_options = inputs.get("options") or "{}"
    try:
        options = json.loads(raw_options) if isinstance(raw_options, str) else raw_options
    except json.JSONDecodeError:
        options = {}

    # Infer task when the AI didn't set it explicitly
    if not task:
        if file_url or file_urls:
            task = "extract_text"
            think("Task not specified — defaulting to extract_text (file URL present)")
        elif content:
            task = "create"
            think("Task not specified — defaulting to create (content present)")
        else:
            fail("Please specify a task: create, extract_text, merge, split, rotate, encrypt, decrypt, or watermark")

    think(f"Task: {task}")

    if task == "create":
        if not content:
            fail("content is required for task=create")
        file_bytes, filename = task_create(content, options)

    elif task in ("extract_text", "extract"):
        if not file_url:
            fail("file_url is required for task=extract_text")
        file_bytes, filename = task_extract_text(file_url, pages_spec, options)

    elif task == "merge":
        urls = file_urls or ([file_url] if file_url else [])
        if len(urls) < 2:
            fail("At least 2 URLs required for task=merge (use file_urls or file_url)")
        file_bytes, filename = task_merge(urls, options)

    elif task == "split":
        if not file_url:
            fail("file_url is required for task=split")
        file_bytes, filename = task_split(file_url, pages_spec, options)

    elif task == "rotate":
        if not file_url:
            fail("file_url is required for task=rotate")
        file_bytes, filename = task_rotate(file_url, pages_spec, options)

    elif task == "encrypt":
        if not file_url:
            fail("file_url is required for task=encrypt")
        file_bytes, filename = task_encrypt(file_url, password, options)

    elif task == "decrypt":
        if not file_url:
            fail("file_url is required for task=decrypt")
        file_bytes, filename = task_decrypt(file_url, password, options)

    elif task == "watermark":
        if not file_url:
            fail("file_url is required for task=watermark")
        file_bytes, filename = task_watermark(file_url, options)

    else:
        fail(f"Unknown task '{task}'. Valid tasks: create, extract_text, merge, split, rotate, encrypt, decrypt, watermark")

    ext = "." + filename.rsplit(".", 1)[-1]
    mime = MIME.get(ext, "application/octet-stream")

    think("Encoding output...")
    print(json.dumps({
        "filename": filename,
        "content_base64": base64.b64encode(file_bytes).decode(),
        "mime_type": mime,
        "thinking": thinking,
    }))

if __name__ == "__main__":
    main()
