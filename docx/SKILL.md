---
name: docx
description: >
  Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files).
  Triggers include: any mention of 'Word doc', 'word document', '.docx', or requests to produce professional
  documents with formatting like tables of contents, headings, page numbers, or letterheads. Also use when
  extracting or reorganizing content from .docx files, inserting or replacing images in documents, performing
  find-and-replace in Word files, working with tracked changes or comments, or converting content into a
  polished Word document. If the user asks for a 'report', 'memo', 'letter', 'template', or similar
  deliverable as a Word or .docx file, use this skill. Do NOT use for PDFs, spreadsheets, Google Docs,
  or general coding tasks unrelated to document generation.
category: productivity
version: "1.0.0"
inputs:
  topic:
    type: string
    required: true
    description: "The document topic, title, or subject matter"
  purpose:
    type: string
    required: false
    description: "Purpose: report, memo, letter, proposal, summary, training (default: report)"
  audience:
    type: string
    required: false
    description: "Target audience: executives, technical, general, clients, internal"
  tone:
    type: string
    required: false
    description: "Tone: formal, professional, casual, persuasive (default: professional)"
  section_count:
    type: integer
    required: false
    description: "Number of sections to generate, 2-6 (default: 3)"
run: python scripts/create.py
output: file
---

# DOCX creation, editing, and analysis

## Overview

A .docx file is a ZIP archive containing XML files. This skill uses the internal LLM to generate
topic-specific, substantive content — not templates or placeholders.

## Architecture

The script follows a 5-phase pipeline:
1. Parse inputs from stdin JSON
2. Call internal LLM to generate structured document content
3. Build the .docx file using python-docx
4. Encode and output as base64

## LLM-Driven Content

The LLM returns a JSON plan with `title`, `sections` (each with `heading` and `paragraphs`), and `conclusion`.
Each paragraph contains real, substantive text — never placeholder sentences.

## Critical python-docx Rules

1. **No `#` prefix in hex colors** — `"1E3A5F"` not `"#1E3A5F"`
2. **Use `Inches()` for all positions** — never raw EMU integers
3. **Always set `tf.word_wrap = True`** when text may overflow
4. **Use `Pt()` for font sizes**
5. **Blank slide/layout not applicable** — use `Document()` for new docs
