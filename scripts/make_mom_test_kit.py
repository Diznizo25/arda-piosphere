"""Generate the Mom Test field kit deliverables from one markdown source.

    python scripts/make_mom_test_kit.py

Source of truth:  docs/field/mom_test_field_kit.md
Outputs (all in docs/field/):
    ArdaLink_Mom_Test_Field_Kit.docx   the full kit, editable
    ArdaLink_Mom_Test_Field_Kit.pdf    the full kit, read-only
    ArdaLink_Mom_Test_Print_Pack.pdf   what you carry: crib sheet, consent cards, 15
                                       interview sheets, snowball sheet, Swahili guide
    ArdaLink_Mom_Test_Tracker.xlsx     where the 10 interviews become rows and a verdict

Edit the markdown, re-run this, and every format updates together — so the questions
asked in the field cannot drift from the questions we agreed on.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "field" / "mom_test_field_kit.md"
SRC_Q = ROOT / "docs" / "field" / "mom_test_questions_only.md"
OUT = ROOT / "docs" / "field"
TITLE = "Arda Link — Mom Test Field Kit"
VERSION = "v1.0 · Isiolo County"

GREEN = "1F5C3A"
GREY = "F2F2F2"


# --- markdown (the subset this kit actually uses) ------------------------------

def parse_md(text: str) -> list[tuple[str, object]]:
    """-> [('h1'|'h2'|'h3'|'h4'|'p'|'bullets'|'ordered'|'quote'|'hr'|'table', payload)]"""
    blocks: list[tuple[str, object]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            i += 1
            continue
        if line.strip() in ("---", "***", "___"):
            blocks.append(("hr", None))
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") and c for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue
        for marker, kind in (("#### ", "h4"), ("### ", "h3"), ("## ", "h2"), ("# ", "h1")):
            if line.startswith(marker):
                blocks.append((kind, line[len(marker):].strip()))
                break
        else:
            if re.match(r"^[-*] ", line):
                items, i = _gather(lines, i, r"^[-*] ")
                blocks.append(("bullets", items))
                continue
            if re.match(r"^\d+\. ", line):
                items, i = _gather(lines, i, r"^\d+\. ")
                blocks.append(("ordered", items))
                continue
            if line.startswith(">"):
                quotes = []
                while i < len(lines) and lines[i].lstrip().startswith(">"):
                    quotes.append(lines[i].lstrip()[1:].strip())
                    i += 1
                blocks.append(("quote", " ".join(q for q in quotes if q)))
                continue
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^(#|\||>|[-*] |\d+\. |---)", lines[i].lstrip()):
                para.append(lines[i].strip())
                i += 1
            blocks.append(("p", " ".join(para)))
            continue
        i += 1
    return blocks


def _gather(lines: list[str], i: int, pattern: str) -> tuple[list[str], int]:
    """Bullet/numbered run, keeping wrapped continuation lines with their item."""
    items: list[str] = []
    while i < len(lines):
        line = lines[i]
        if re.match(pattern, line):
            items.append(re.sub(pattern, "", line).strip())
        elif items and line.startswith(("  ", "\t")) and line.strip():
            items[-1] = f"{items[-1]} {line.strip()}"
        else:
            break
        i += 1
    return items, i


_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`)")


def inline_pieces(text: str) -> list[tuple[str, str]]:
    """Split inline markdown -> [(style, text)] with style in {'', 'b', 'i', 'code'}."""
    out: list[tuple[str, str]] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(("b", part[2:-2]))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(("code", part[1:-1]))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(("i", part[1:-1]))
        else:
            out.append(("", part))
    return out


def strip_md(text: str) -> str:
    return "".join(piece for _, piece in inline_pieces(text))


def rl(text: str) -> str:
    """reportlab inline markup: escape XML first, then re-apply bold/italic/mono."""
    parts = []
    for style, piece in inline_pieces(text):
        safe = piece.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if style == "b":
            parts.append(f"<b>{safe}</b>")
        elif style == "i":
            parts.append(f"<i>{safe}</i>")
        elif style == "code":
            parts.append(f'<font face="Courier">{safe}</font>')
        else:
            parts.append(safe)
    return "".join(parts)


# --- DOCX ----------------------------------------------------------------------

def render_docx(blocks: list[tuple[str, object]], path: Path, subtitle: str) -> None:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, attr, Cm(1.6))

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(4)

    def style_heading(name: str, size: float, color: str, before: int, after: int):
        st = doc.styles[name]
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor.from_string(color)
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)

    style_heading("Heading 1", 18, GREEN, 0, 8)
    style_heading("Heading 2", 14, GREEN, 14, 6)
    style_heading("Heading 3", 12, "2E5C46", 10, 4)
    style_heading("Heading 4", 11, "2E5C46", 8, 3)

    def add_runs(par, text, size=None, italic=False):
        for style, piece in inline_pieces(text):
            run = par.add_run(piece)
            run.bold = style == "b"
            run.italic = style == "i" or italic
            if style == "code":
                run.font.name = "Consolas"
                run.font.size = Pt((size or 10.5) - 1.0)
            elif size:
                run.font.size = Pt(size)
        return par

    def shade(cell, hexcolor):
        el = OxmlElement("w:shd")
        el.set(qn("w:val"), "clear")
        el.set(qn("w:fill"), hexcolor)
        cell._tc.get_or_add_tcPr().append(el)

    def add_table(rows: list[list[str]]):
        cols = max(len(r) for r in rows)
        rows = [r + [""] * (cols - len(r)) for r in rows]
        size = 8.0 if cols <= 3 else (7.5 if cols <= 5 else 6.8)
        t = doc.add_table(rows=0, cols=cols)
        t.style = "Table Grid"
        t.autofit = True
        for r_i, row in enumerate(rows):
            cells = t.add_row().cells
            for c_i, text in enumerate(row):
                cell = cells[c_i]
                cell.text = ""
                par = cell.paragraphs[0]
                par.paragraph_format.space_after = Pt(1)
                add_runs(par, text, size=size, italic=(r_i == 0))
                if r_i == 0:
                    for run in par.runs:
                        run.bold = True
                    shade(cell, GREY)
        doc.add_paragraph()

    for kind, payload in blocks:
        if kind == "h1":
            p = doc.add_paragraph(style="Heading 1")
            add_runs(p, str(payload))
            sub = doc.add_paragraph()
            run = sub.add_run(f"{subtitle} — rendered from docs/field/mom_test_field_kit.md")
            run.italic = True
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor.from_string("595959")
        elif kind in ("h2", "h3", "h4"):
            p = doc.add_paragraph(style={"h2": "Heading 2", "h3": "Heading 3",
                                         "h4": "Heading 4"}[kind])
            add_runs(p, str(payload))
        elif kind == "p":
            add_runs(doc.add_paragraph(), str(payload))
        elif kind == "bullets":
            for item in payload:  # type: ignore[union-attr]
                add_runs(doc.add_paragraph(style="List Bullet"), item)
        elif kind == "ordered":
            for item in payload:  # type: ignore[union-attr]
                add_runs(doc.add_paragraph(style="List Number"), item)
        elif kind == "quote":
            par = doc.add_paragraph()
            par.paragraph_format.left_indent = Cm(0.8)
            add_runs(par, str(payload), italic=True)
        elif kind == "table":
            add_table(payload)  # type: ignore[arg-type]
        elif kind == "hr":
            par = doc.add_paragraph()
            par.paragraph_format.space_before = Pt(2)
            run = par.add_run("—" * 40)
            run.font.color.rgb = RGBColor.from_string("BFBFBF")

    # Footer with a real page-number field (Word renders it; silent if it cannot).
    try:
        footer = sec.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(f"{TITLE} · {VERSION} · page ")
        run.font.size = Pt(8)
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), "PAGE")
        footer._p.append(fld)
    except Exception:  # noqa: BLE001
        pass

    doc.save(str(path))


# --- PDF -----------------------------------------------------------------------

def _pdf_styles():
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle

    base = ParagraphStyle("body", fontName="Helvetica", fontSize=9.3, leading=12.4,
                          spaceAfter=4, alignment=TA_JUSTIFY)
    return {
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=17,
                             leading=20, spaceAfter=6, alignment=0),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=13,
                             leading=16, spaceBefore=10, spaceAfter=5, alignment=0),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=11,
                             leading=14, spaceBefore=8, spaceAfter=3, alignment=0),
        "h4": ParagraphStyle("h4", parent=base, fontName="Helvetica-BoldOblique",
                             fontSize=10, leading=13, spaceBefore=6, spaceAfter=2,
                             alignment=0),
        "p": base,
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=12, bulletIndent=2,
                                 spaceAfter=2),
        "ordered": ParagraphStyle("ordered", parent=base, leftIndent=15, bulletIndent=2,
                                  spaceAfter=2),
        "quote": ParagraphStyle("quote", parent=base, leftIndent=14, rightIndent=8,
                                fontName="Helvetica-Oblique", spaceBefore=2,
                                spaceAfter=5),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.5, leading=9.2,
                               spaceAfter=0, alignment=0),
        "cellhead": ParagraphStyle("cellhead", parent=base, fontName="Helvetica-Bold",
                                   fontSize=7.5, leading=9.2, spaceAfter=0, alignment=0),
        "cellsm": ParagraphStyle("cellsm", parent=base, fontSize=6.6, leading=8.0,
                                 spaceAfter=0, alignment=0),
        "headsm": ParagraphStyle("headsm", parent=base, fontName="Helvetica-Bold",
                                 fontSize=6.6, leading=8.0, spaceAfter=0, alignment=0),
        "foot": ParagraphStyle("foot", parent=base, fontSize=7.6, leading=9,
                               alignment=TA_CENTER),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold",
                                fontSize=20, leading=24, alignment=TA_CENTER,
                                spaceAfter=4),
    }


def render_pdf(blocks: list[tuple[str, object]], path: Path, subtitle: str,
               title: str = TITLE) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, LongTable, Paragraph,
                                    SimpleDocTemplate, Spacer, TableStyle)

    st = _pdf_styles()
    green = colors.HexColor("#" + GREEN)
    for key in ("h1", "h2", "h3"):
        st[key].textColor = green

    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title, author="Arda Link",
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.4 * cm, bottomMargin=1.4 * cm)

    def decorate(canv, d):
        canv.saveState()
        canv.setFont("Helvetica", 7.4)
        canv.setFillColor(colors.HexColor("#595959"))
        canv.drawString(1.5 * cm, 0.9 * cm, f"{title} · {VERSION}")
        canv.drawRightString(A4[0] - 1.5 * cm, 0.9 * cm, f"page {d.page}")
        canv.setStrokeColor(colors.HexColor("#D9D9D9"))
        canv.line(1.5 * cm, 1.2 * cm, A4[0] - 1.5 * cm, 1.2 * cm)
        canv.restoreState()

    flow: list = [Paragraph(rl(title), st["title"]),
                  Paragraph(rl(subtitle), st["foot"]),
                  Spacer(1, 6),
                  HRFlowable(width="100%", color=green, thickness=1.2),
                  Spacer(1, 6)]

    for kind, payload in blocks:
        if kind == "h1":
            continue  # the title block above already carries it
        if kind in ("h2", "h3", "h4"):
            flow.append(Paragraph(rl(str(payload)), st[kind]))
        elif kind == "p":
            flow.append(Paragraph(rl(str(payload)), st["p"]))
        elif kind == "bullets":
            for item in payload:  # type: ignore[union-attr]
                flow.append(Paragraph(rl(item), st["bullet"], bulletText="•"))
        elif kind == "ordered":
            for n, item in enumerate(payload, 1):  # type: ignore[union-attr]
                flow.append(Paragraph(rl(item), st["ordered"], bulletText=f"{n}."))
        elif kind == "quote":
            flow.append(Paragraph(rl(str(payload)), st["quote"]))
        elif kind == "hr":
            flow.append(Spacer(1, 3))
            flow.append(HRFlowable(width="100%", color=colors.HexColor("#D9D9D9")))
            flow.append(Spacer(1, 4))
        elif kind == "table":
            flow.append(_pdf_table(payload, st, LongTable, TableStyle, Paragraph, colors))

    doc.build(flow, onFirstPage=decorate, onLaterPages=decorate)


def _pdf_table(rows, st, LongTable, TableStyle, Paragraph, colors):
    cols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (cols - len(r)) for r in rows]
    cell_style = st["cell"] if cols <= 5 else st["cellsm"]
    head_style = st["cellhead"] if cols <= 5 else st["headsm"]
    data = [[Paragraph(rl(c), head_style if i == 0 else cell_style)
             for c in row] for i, row in enumerate(rows)]
    t = LongTable(data, colWidths=_column_widths(rows, cols), repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + GREY)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _column_widths(rows, cols):
    """Give the wordier columns more room: width follows average text length."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    total = A4[0] - 3.0 * cm
    averages = []
    for c in range(cols):
        lengths = [len(str(r[c])) for r in rows[:60] if c < len(r)]
        averages.append(max(6.0, sum(lengths) / max(1, len(lengths))))
    scale = total / sum(averages)
    return [min(total * 0.5, a * scale) for a in averages]


# --- PRINT PACK (what you carry into the field) --------------------------------

CRIB = [
    ("h2", "Mom Test crib sheet — read this in the vehicle"),
    ("p", "**Talk about his life, not our idea.** Nobody tells you your baby is ugly."),
    ("p", "**Ask about the past, never the future.** \"Would you use it?\" detects "
          "nothing. \"When did you last decide where to move?\" is data."),
    ("p", "**Stories, not opinions.** When he says \"usually\", go back to the last time."),
    ("p", "**Numbers or it did not happen.** Siku ngapi? Kilometa ngapi? Shilingi ngapi? "
          "Wanyama wangapi? Lita ngapi?"),
    ("p", "**Compliments are junk food.** Thank him, then straight back to facts."),
    ("p", "**Money questions are about the past.** When did you last pay for advice — to "
          "whom, and how much?"),
    ("p", "**Never pitch.** More than 30 seconds about our product means you are not "
          "learning."),
    ("p", "**Ask for something at the end.** Time, an introduction, a test. That is the "
          "only answer that costs him anything, so it is the only one that counts."),
    ("h3", "Three phrases that kill an interview"),
    ("bullets", ["\"Would you…\" → \"When did you last…\"",
                 "\"Do you think…\" → \"What did you do…\"",
                 "\"We are building…\" → \"Tell me more about that day…\""]),
    ("h3", "If he asks what we are building"),
    ("quote", "We are testing whether a phone can help with water and grazing decisions. "
              "But I would rather hear about your last dry season than talk about it. "
              "Can we go back to that?"),
    ("h3", "The close — ask for something"),
    ("bullets", ["May I come back or call you in two weeks? (time)",
                 "May I send one message from your phone, now, while you watch? "
                 "(micro-commitment)",
                 "Who else should I talk to — and who would disagree with you? "
                 "(introductions)",
                 "Can we test this at your water point with the committee? (advancement)"]),
    ("h3", "Notes discipline"),
    ("p", "Verbatim, in his language, with numbers. Tag every line: **FACT / NUMBER / "
          "QUOTE / WORKAROUND / PAYMENT / TRUST-BREAK / COMMITMENT / BLOCKER**. Three "
          "numbers minimum, or the interview was too polite."),
]

CONSENT_SW = (
    "**Kukubali (sema kwa sauti)** — Habari. Jina langu ni ___, natoka Arda Link. "
    "Tunatengeneza huduma ya simu kwa wachungaji, na tunataka kujua matatizo yenu "
    "halisi. Hili ni mahojiano, si mradi wa msaada, na **hakuna malipo ya fedha**. "
    "Huenda tusitengeneze kitu kabisa. Nitakuuliza maswali kama dakika 45. Naomba "
    "kuandika na kurekodi, ili nisikose ulichosema. Unaweza kusimama, au kuruka swali "
    "lolote, wakati wowote. Jina lako halitatumika popote bila ruhusa yako. Tukianza?")

CONSENT_EN = (
    "**Consent (read aloud)** — My name is ___, from Arda Link. We are building a phone "
    "service for pastoralists, and we want to understand your real problems. This is an "
    "interview, not a relief programme, and **there is no payment**. We may not build "
    "anything at all. I will ask questions for about 45 minutes. I would like to write "
    "and record, so I do not miss what you said. You can stop, or skip any question, at "
    "any time. Your name will not be used anywhere without your permission. May we start?")

FIELDS = ["Date · time start / end", "Place · Ward · Interview # 01–10",
          "Interviewer · writer · consent Y/N · recorder Y/N",
          "Initials · sex · age band · language · household size",
          "Who holds the phone · WhatsApp Y/N · reads or listens",
          "Species and rough counts · herd size",
          "Main water point · walk time · distance band (near / mid / far)",
          "Recent move Y/N · which month · quota filled"]

AREAS = [("Quotes (verbatim — tag: FACT / NUMBER / QUOTE / WORKAROUND / PAYMENT / "
          "TRUST-BREAK / COMMITMENT / BLOCKER)", 6),
         ("Numbers (3 minimum: siku / km / shilingi / wanyama / lita)", 4),
         ("Workarounds he uses today · what he has actually paid for", 3),
         ("Trust-breaks (a time advice failed him, and what he did)", 2),
         ("Product tests T1 find · T2 understand · T3 one-tap · T4 voice/text · "
          "T5 map · T6 weight · T7 uncertainty · T8 forward", 2),
         ("Commitments (what exactly, with a date) · introductions given", 3),
         ("Hypotheses: D1–D4 · U1–U4 · F1–F3 · V1–V4 · I1–I2 · A1–A4 = Y / N / ? "
          "+ evidence id", 2),
         ("My one line to the team tonight", 2)]


def _blank_lines(label: str, rows: int, st, Table, TableStyle, Paragraph, colors,
                 row_h: float = 17.0):
    """A labelled block of ruled writing lines, kept to a fixed height."""
    t = Table([[Paragraph(rl(label), st["h4"])]] + [[""] for _ in range(rows)],
              rowHeights=[None] + [row_h] * rows)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), 0.35,
                            colors.HexColor("#BFBFBF")),
                           ("TOPPADDING", (0, 0), (-1, -1), 1),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def build_print_pack(md_blocks: list[tuple[str, object]], path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, LongTable, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    st = _pdf_styles()
    green = colors.HexColor("#" + GREEN)
    for key in ("h1", "h2", "h3"):
        st[key].textColor = green
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            title="Arda Link — field print pack", author="Arda Link",
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.3 * cm)

    def decorate(canv, d):
        canv.saveState()
        canv.setFont("Helvetica", 7.2)
        canv.setFillColor(colors.HexColor("#595959"))
        canv.drawString(1.5 * cm, 0.85 * cm,
                        "Arda Link · field print pack · crib sheet · consent · "
                        "interview sheets · Swahili guide")
        canv.drawRightString(A4[0] - 1.5 * cm, 0.85 * cm, f"page {d.page}")
        canv.restoreState()

    def emit(blocks) -> None:
        for kind, payload in blocks:
            if kind in ("h2", "h3", "h4"):
                flow.append(Paragraph(rl(str(payload)), st[kind]))
            elif kind == "p":
                flow.append(Paragraph(rl(str(payload)), st["p"]))
            elif kind == "bullets":
                for item in payload:  # type: ignore[union-attr]
                    flow.append(Paragraph(rl(item), st["bullet"], bulletText="•"))
            elif kind == "ordered":
                for n, item in enumerate(payload, 1):  # type: ignore[union-attr]
                    flow.append(Paragraph(rl(item), st["ordered"], bulletText=f"{n}."))
            elif kind == "quote":
                flow.append(Paragraph(rl(str(payload)), st["quote"]))
            elif kind == "hr":
                flow.append(Spacer(1, 4))
                flow.append(HRFlowable(width="100%", color=colors.HexColor("#D9D9D9")))
                flow.append(Spacer(1, 4))

    flow: list = []
    emit(CRIB)
    flow.append(PageBreak())
    flow.append(Paragraph("Consent card — read this out loud, every time", st["h2"]))
    flow.append(Paragraph(rl(CONSENT_SW), st["p"]))
    flow.append(Spacer(1, 10))
    flow.append(HRFlowable(width="100%", color=colors.HexColor("#D9D9D9")))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(rl(CONSENT_EN), st["p"]))
    flow.append(Spacer(1, 12))
    flow.append(Paragraph("Consent given (Y/N) _______   Recorder on (Y/N) _______   "
                          "Date ___________   Interview # _______", st["p"]))

    half = (len(FIELDS) + 1) // 2
    field_rows = [[Paragraph(rl(FIELDS[i]), st["cell"]),
                   Paragraph(rl(FIELDS[i + half]), st["cell"])
                   if i + half < len(FIELDS) else Paragraph("", st["cell"])]
                  for i in range(half)]

    for n in range(1, 16):
        flow.append(PageBreak())
        flow.append(Paragraph(f"Interview sheet #{n:02d}", st["h2"]))
        head = Table(field_rows, colWidths=[(A4[0] - 3.0 * cm) / 2.0] * 2,
                     rowHeights=[20.0] * half)
        head.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.35,
                                   colors.HexColor("#BFBFBF")),
                                  ("TOPPADDING", (0, 0), (-1, -1), 3),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        flow.append(head)
        flow.append(Spacer(1, 4))
        for label, rows in AREAS:
            flow.append(_blank_lines(label, rows, st, Table, TableStyle, Paragraph,
                                     colors))
            flow.append(Spacer(1, 3))

    _print_pack_tail(flow, md_blocks, st, colors, A4, cm, LongTable, Table, TableStyle,
                     Paragraph, HRFlowable, PageBreak, Spacer)
    doc.build(flow, onFirstPage=decorate, onLaterPages=decorate)


def _print_pack_tail(flow, md_blocks, st, colors, A4, cm, LongTable, Table, TableStyle,
                     Paragraph, HRFlowable, PageBreak, Spacer) -> None:
    """Snowball sheet + quota check + the Swahili guide (from the markdown)."""
    flow.append(PageBreak())
    flow.append(Paragraph("Snowball sheet — who else, and who disagrees", st["h2"]))
    flow.append(Paragraph("After every interview: \"who else should I talk to — and who "
                          "would disagree with you?\" A dissenter is worth three agreeable "
                          "friends.", st["p"]))
    flow.append(_blank_lines("Name · place · why them · introduced by", 10, st, Table,
                             TableStyle, Paragraph, colors))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph("Quota check (tick as you go)", st["h3"]))
    quota = [["Quota", "Target", "Booked", "Done"],
             ["Women", "3", "", ""], ["Farthest from water", "3", "", ""],
             ["Recent / mid-move", "2", "", ""], ["Camel keepers", "2", "", ""],
             ["Shoats only / dominant", "3", "", ""], ["Cattle dominant", "4", "", ""],
             ["Daily WhatsApp", "2", "", ""], ["Never WhatsApp", "2", "", ""],
             ["Share a phone", "2", "", ""], ["Bad health event", "1–2", "", ""],
             ["Wards (>=2) / water points (>=3)", "—", "", ""],
             ["Ecosystem informants", "5", "", ""]]
    qt = LongTable([[Paragraph(rl(c), st["cellhead"] if i == 0 else st["cell"])
                     for c in row] for i, row in enumerate(quota)],
                   colWidths=[8.0 * cm, 2.2 * cm, 3.0 * cm, 3.0 * cm])
    qt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.35,
                             colors.HexColor("#BFBFBF")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + GREY))]))
    flow.append(qt)

    flow.append(PageBreak())
    for kind, payload in _extract_appendix_a(md_blocks):
        if kind in ("h2", "h3", "h4"):
            flow.append(Paragraph(rl(str(payload)), st[kind]))
        elif kind == "p":
            flow.append(Paragraph(rl(str(payload)), st["p"]))
        elif kind == "ordered":
            for n, item in enumerate(payload, 1):  # type: ignore[union-attr]
                flow.append(Paragraph(rl(item), st["ordered"], bulletText=f"{n}."))
        elif kind == "bullets":
            for item in payload:  # type: ignore[union-attr]
                flow.append(Paragraph(rl(item), st["bullet"], bulletText="•"))
        elif kind == "quote":
            flow.append(Paragraph(rl(str(payload)), st["quote"]))


def _extract_appendix_a(blocks: list[tuple[str, object]]) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    started = False
    for kind, payload in blocks:
        if kind in ("h2", "h3") and "Appendix A" in str(payload):
            started = True
            out.append(("h2", "Swahili spoken guide (Appendix A)"))
            continue
        if started and kind == "h2" and "Appendix B" in str(payload):
            break
        if started:
            out.append((kind, payload))
    return out


# --- XLSX TRACKER -----------------------------------------------------------------

HYPOTHESES = [
    ("D1", "The water and grazing decision is frequent, hard and expensive",
     ">=6/10 describe a specific episode with a month, a place and a cost",
     "<4/10 describe any specific loss", 6),
    ("D2", "Information is the binding constraint, not money, water or land",
     "They pay or travel to find out, and act on what they learn",
     "They know and still cannot act", 6),
    ("D3", "The loss is big enough to matter to a household",
     "Named losses in animals, milk or shillings in the last 12 months",
     "Losses are rare and small", 6),
    ("D4", "It is a recurring decision, not once a year",
     "Weekly-to-monthly cadence in >=6/10", "Once a season", 6),
    ("U1", "A herder can find and use a WhatsApp service without training",
     ">=7/10 complete the task unaided", "<=4/10 complete the task", 7),
    ("U2", "One tap is a price he will pay to receive something",
     ">=6/10 report once, or agree to a test", "Most refuse", 6),
    ("U3", "Voice beats text for the people who matter most",
     "Voice chosen by the least literate and the most remote", "Text dominates", 6),
    ("U4", "Our messages are understood the first time",
     ">=8/10 restate the advice correctly", "Common misreadings", 8),
    ("F1", "A kg estimate is credible and useful",
     "A reference weight exists and a use is named", "No reference weights anywhere", 6),
    ("F2", "Rain-onset language survives being wrong",
     "He tolerates estimate wording and keeps using it", "He punishes any miss", 6),
    ("F3", "The map is readable and its age is understood",
     "He finds his place and reads the date / kadirio", "He reads the map as today", 6),
    ("S1", "He already passes information on, or will", ">=6/10 describe a specific time they passed on water or pest news",
     "Nobody has ever reported anything to anyone", 6),
    ("S2", "One tap is a price he will pay for the value he gets back",
     ">=6/10 complete a report unaided, or agree to a test", "Most refuse", 6),
    ("S3", "His reports are good enough to sell",
     "Place names, status and timing are what a buyer would accept", "Too coarse or too late", 6),
    ("S4", "He keeps contributing without being paid", 
     "He names a concrete gain and is still reporting two weeks later", "One report, then silence", 6),
    ("V1", "An institution already pays to collect this kind of data",
     "A named buyer with a real current spend (fuel, staff, surveys, M&E)",
     "Nobody spends anything on it", 1),
    ("V2", "That spend is reachable: a budget line, an owner, a cycle",
     "A line item, a named officer, a date when it is decided",
     "Only good intentions, no line", 1),
    ("V3", "The chief, the agrovet and the water committee will carry it",
     ">=3 credible distribution offers, with names", "Nobody carries it", 3),
    ("V4", "No gatekeeper blocks it",
     "Nobody objects or claims ownership", "A veto appears", 6),
    ("I1", "Early information reaches the household, not only the herd owner",
     "Women and phone-sharers describe concrete household gains",
     "The beneficiary never sees the message", 6),
    ("I2", "The change is fewer wasted walks, less milk drop, fewer dead animals",
     "Two or more describable in numbers", "Only it feels useful", 6),
    ("A1", "A pastoralist decides where to move, and is able to move", "—",
     "The decision is collective, or animals are held by a relative", 6),
    ("A2", "He wants advice from a phone more than his current sources", "—",
     "He trusts the chief or radio, and says a phone cannot be checked", 6),
    ("A3", "Our data is good enough at the decision moment", "—",
     "He corrects most of what we show him", 6),
    ("A4", "We can reach him without an NGO or the county", "—",
     "Distribution needs a gatekeeper we do not have", 6),
]

HYP_TESTS = {
    "D1": "B4-B9, C10-C15, D16-D21", "D2": "C11-C12, E23-E26, H38",
    "D3": "D19, H37, K51", "D4": "B4, C10, D16",
    "U1": "T1, T3, G36", "U2": "T3, M57", "U3": "T4, G35", "U4": "T2",
    "F1": "T6", "F2": "T7, I42", "F3": "T5",
    "S1": "L52-L55", "S2": "T3, L53, M57", "S3": "L54, T2, T3",
    "S4": "K48, K51, T4, the 2-week call",
    "V1": "informant C2, C5, J46", "V2": "informant C2.4, C5.2, C5.4",
    "V3": "M58, M59, informants", "V4": "informants, I44",
    "I1": "K48-K51, G32", "I2": "C12, D19, K49",
    "A1": "B4-B9", "A2": "F27-F31, I43", "A3": "T2, T5, T6", "A4": "informants, M58",
}

SCREEN_COLS = ["#", "Initials", "Sex", "Age band", "Ward", "Water point",
               "Walk time (min)", "Distance band", "Species", "Herd size",
               "Who holds the phone", "WhatsApp (Y/N)", "Reads / listens",
               "Shares phone (Y/N)", "Bad health event (Y/N)", "Quota filled",
               "Interviewer", "Date booked", "Status"]

LOG_COLS = ["#", "Date", "Place", "Ward", "Start", "End", "Minutes", "Consent (Y/N)",
            "Recorder (Y/N)", "Who present", "T1 unaided find", "T2 misreadings",
            "T3 one-tap report", "T4 voice / text", "T5 map verdict",
            "T6 reference weight", "T7 uncertainty", "T8 forward path",
            "Callback agreed (Y/N)", "Introduction given (Y/N)", "Test agreed (Y/N)",
            "My one line tonight"]

TAGS = ["FACT", "NUMBER", "QUOTE", "WORKAROUND", "PAYMENT", "TRUST-BREAK", "COMMITMENT",
        "BLOCKER"]

HOW_TO = [
    "Mom Test field tracker — where the 10 interviews become rows, and a verdict.",
    "",
    "1. 01_Screener: book the interviews (screening facts + which quota each one fills).",
    "2. 02_Hypotheses: after each interview mark Y / N / ? for every hypothesis id, and "
    "record the evidence id from 04_Quotes.",
    "3. 03_Interview_Log: one row per visit, including the product-test outcomes (T1-T8).",
    "4. 04_Quotes: verbatim quotes only, each tagged and tied to a hypothesis.",
    "5. 05_Numbers: every number he gave - minimum three per interview.",
    "6. 06_WhoIsWho: complete the user / beneficiary / payer / gatekeeper map.",
    "7. 07_Gate: fills itself from the other sheets. Read the verdict before deciding "
    "what to build next.",
    "",
    "Rule of the whole round: unprompted mentions count, prompted agreement does not. "
    "Numbers or it did not happen. The script is docs/field/mom_test_field_kit.md "
    "(section 4) - and never pitch.",
]


WHO_ROWS = [
    ("Herder / owner — user, decision maker", "", "",
     "Not to lose animals; know where to move", "Wasted walks, dead animals, milk drop",
     "One-tap report from the point", "Never pays"),
    ("Young herder — user, daily with the animals", "", "",
     "Water today, no wasted walking", "Hours, km, heat",
     "The most reports, most often", "Never pays"),
    ("Woman in the manyatta — user + beneficiary", "", "",
     "Milk, children's food, water, milk price", "Walks to water, milk loss, sick shoats",
     "Queue and shoats observations", "Never pays"),
    ("Household / children / elderly — beneficiary", "", "", "Food, milk, income stability",
     "Nutrition when herds fail", "Nothing — he is why the herder never pays", "Never pays"),
    ("Water point committee / WRUA — gatekeeper", "", "", "A working point, no fights",
     "Queue conflict, breakdowns", "Confirms repairs, validates reports", "No (may hold a fund)"),
    ("Agrovet — channel + trust anchor", "", "", "Footfall, correct drug use",
     "Wrong products bought, no follow-up", "Distribution; pest observations at the counter",
     "Maybe — footfall/stock data"),
    ("County livestock / water dept — BUYER", "", "",
     "Surveillance, service delivery, fewer outbreaks",
     "Collecting ward ground truth by vehicle and phone", "Nothing (or it reviews our wording)",
     "YES — county budget line"),
    ("NDMA ward officer — BUYER / data partner", "", "",
     "Timely ward ground truth on water and pasture", "Fuel, staff time, late reports",
     "Nothing (it is a consumer)", "YES — monitoring / early-warning line"),
    ("NGO / CBO project — BUYER (M&E)", "", "", "Reach, evidence for funders",
     "Expensive last-mile data collection", "Distribution and field access",
     "YES — per ward per month"),
    ("Insurer / research institute — buyer, later", "", "",
     "Underwriting data; longitudinal herd data", "Sample surveys, scarce baselines",
     "Nothing", "Later — needs 6+ months of records"),
    ("Chief / ward admin — distribution", "", "", "Order, service, being seen to help",
     "Nothing", "Baraza announcement, signboard", "No"),
]


def _sheet(wb, name, widths, freeze="A2"):
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(name)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = float(w)
    ws.freeze_panes = freeze
    return ws


def _header(ws, cols, row=1):
    from openpyxl.styles import Alignment, Font, PatternFill

    fill = PatternFill("solid", fgColor="D9E8DF")
    for i, name in enumerate(cols, 1):
        cell = ws.cell(row=row, column=i, value=name)
        cell.font = Font(bold=True, size=9)
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def build_tracker(path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    how = wb.active
    how.title = "00_How_to_use"
    how.column_dimensions["A"].width = 112
    how["A1"] = "Arda Link — Mom Test tracker"
    how["A1"].font = Font(bold=True, size=14)
    for i, line in enumerate(HOW_TO, 3):
        how.cell(row=i, column=1, value=line).alignment = Alignment(wrap_text=True)

    ws = _sheet(wb, "01_Screener", [4, 10, 6, 9, 14, 26, 9, 11, 16, 9, 16, 9, 11, 9,
                                    9, 30, 12, 12, 12])
    _header(ws, SCREEN_COLS)
    for r in range(2, 17):
        ws.cell(row=r, column=1, value=r - 1)

    hyp_cols = (["ID", "Hypothesis", "TRUE if", "FALSE -> kill/pivot", "T", "How tested"]
                + [f"I{i:02d}" for i in range(1, 16)] + ["Y count", "N count", "Verdict"])
    ws = _sheet(wb, "02_Hypotheses", [5, 46, 34, 34, 4, 20] + [4] * 15 + [8, 8, 15])
    _header(ws, hyp_cols)
    for i, (hid, hypo, true_if, false_if, t) in enumerate(HYPOTHESES):
        r = i + 2
        ws.cell(row=r, column=1, value=hid).font = Font(bold=True)
        ws.cell(row=r, column=2, value=hypo)
        ws.cell(row=r, column=3, value=true_if)
        ws.cell(row=r, column=4, value=false_if)
        ws.cell(row=r, column=5, value=t)
        ws.cell(row=r, column=6, value=HYP_TESTS.get(hid, "interviews 1-10"))
        # I01..I15 = the ten pastoralist interviews and the five informants.
        ws.cell(row=r, column=21, value=f'=COUNTIF(F{r}:T{r},"Y")')
        ws.cell(row=r, column=22, value=f'=COUNTIF(F{r}:T{r},"N")')
        ws.cell(row=r, column=23,
                value=f'=IF(U{r}+V{r}=0,"",IF(U{r}>=E{r},"SUPPORTED",'
                      f'IF(V{r}>=16-E{r},"CONTRADICTED","UNKNOWN")))')
        for c in range(2, 24):
            ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    yn = DataValidation(type="list", formula1='"Y,N,?"', allow_blank=True)
    ws.add_data_validation(yn)
    yn.add(f"F2:T{len(HYPOTHESES) + 1}")

    ws = _sheet(wb, "03_Interview_Log", [4, 11, 18, 12, 7, 7, 8] + [8] * 4 + [30] * 8
                + [10] * 3 + [40])
    _header(ws, LOG_COLS)
    for r in range(2, 17):
        ws.cell(row=r, column=1, value=r - 1)
    yn2 = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(yn2)
    yn2.add("H2:I16")
    yn2.add("S2:U16")

    ws = _sheet(wb, "04_Quotes", [4, 10, 9, 14, 62, 40, 8])
    _header(ws, ["#", "Interview #", "Section", "Tag", "Quote (verbatim)",
                 "Translation (if not English/Swahili)", "Hypothesis id"])
    tag_dv = DataValidation(type="list", formula1='"' + ",".join(TAGS) + '"',
                            allow_blank=True)
    ws.add_data_validation(tag_dv)
    tag_dv.add("D2:D200")

    ws = _sheet(wb, "05_Numbers", [10, 34, 10, 12, 10, 8, 14])
    _header(ws, ["Interview #", "Item", "Value", "Unit", "Question #", "Hyp id", "Tag"])

    ws = _sheet(wb, "06_WhoIsWho", [34, 18, 12, 30, 30, 9, 9, 18, 26, 12, 12])
    _header(ws, ["Role", "Who (real name)", "Ward", "What they want",
                 "What it costs them today", "Gives (data)", "Pays?",
                 "Contact", "Next step", "Owner", "Date"])
    for i, row in enumerate(WHO_ROWS, start=2):
        for c, value in enumerate(row, start=1):
            ws.cell(row=i, column=c, value=value)

    # --- the gate: it fills itself from the sheets above -------------------------
    ws = _sheet(wb, "07_Gate", [46, 12, 12, 44], freeze="A4")
    ws["A1"] = "Decision gate — read this before deciding what to build next"
    ws["A1"].font = Font(bold=True, size=13)
    _header(ws, ["Metric", "Value", "Threshold", "Where it comes from"], row=3)
    metrics = [
        ("Interviews completed (10 pastoralists + 5 informants)",
         "=COUNTA('03_Interview_Log'!A2:A16)", "15", "03_Interview_Log"),
        ("A specific costly decision described (D1)", "='02_Hypotheses'!U2", ">=6",
         "hypothesis D1"),
        ("He already reports, or agrees to (S1)", "='02_Hypotheses'!U13", ">=6",
         "hypothesis S1"),
        ("One tap completed unaided or accepted (S2)", "='02_Hypotheses'!U14", ">=6",
         "hypothesis S2"),
        ("His reports are good enough to sell (S3)", "='02_Hypotheses'!U15", ">=6",
         "hypothesis S3"),
        ("A buyer with a real current spend (V1)", "='02_Hypotheses'!U17", ">=1",
         "hypothesis V1 - informant interviews"),
        ("A budget line, owner and cycle (V2)", "='02_Hypotheses'!U18", ">=1",
         "hypothesis V2 - informant interviews"),
        ("Commitments accepted (callback / intro / test)",
         "=COUNTIF('03_Interview_Log'!S2:U16,\"Y\")", ">=4", "Appendix M / section 4 close"),
    ]
    for i, (name, formula, threshold, source) in enumerate(metrics, start=4):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=formula)
        ws.cell(row=i, column=3, value=threshold)
        ws.cell(row=i, column=4, value=source)
    ws["A13"] = "VERDICT"
    ws["A13"].font = Font(bold=True, size=12)
    ws["B13"] = ('=IF(B4<10,"NOT ENOUGH INTERVIEWS YET",'
                 'IF(B5<4,"REFRAME / STOP - fewer than 4 of 10 can describe a specific, '
                 'costly decision",'
                 'IF(AND(B5>=6,B6>=6,B7>=6,B9>=1,B10>=1,B11>=4),"GO - the problem, the '
                 'supply habit, the buyer and the commitments all clear the bar",'
                 'IF(B9<1,"NO BUYER YET - the service may be real, but no institution '
                 'currently pays to collect this data: re-read section 8 before building '
                 'more","PIVOT - the problem or the buyer is there but the supply or the '
                 'segment is wrong: re-read section 8"))))')
    ws["B13"].font = Font(bold=True)
    ws["A15"] = "Problem clustering (one row per problem; unprompted mentions only)"
    ws["A15"].font = Font(bold=True, size=11)
    _header(ws, ["Problem (in their words)", "Unprompted /10",
                 "Cost today (their numbers)", "What they do now", "Does our product "
                 "address it?", "Verdict"], row=16)
    ws["A24"] = "Decisions log"
    ws["A24"].font = Font(bold=True, size=11)
    _header(ws, ["Decision", "Evidence (interview #, quote or number)", "Owner",
                 "By when"], row=25)
    for r in range(26, 40):
        ws.cell(row=r, column=1)
    wb.save(str(path))


# --- MAIN ----------------------------------------------------------------------

def main() -> int:
    if not SRC.exists():
        print(f"missing source: {SRC}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    md = SRC.read_text(encoding="utf-8")
    blocks = parse_md(md)

    kit_docx = OUT / "ArdaLink_Mom_Test_Field_Kit.docx"
    kit_pdf = OUT / "ArdaLink_Mom_Test_Field_Kit.pdf"
    pack_pdf = OUT / "ArdaLink_Mom_Test_Print_Pack.pdf"
    tracker = OUT / "ArdaLink_Mom_Test_Tracker.xlsx"

    render_docx(blocks, kit_docx, f"{VERSION} · 10 pastoralist interviews "
                                 f"+ 5 ecosystem informants")
    print(f"wrote {kit_docx.name}  ({kit_docx.stat().st_size / 1024:.0f} KB)")

    render_pdf(blocks, kit_pdf, f"{VERSION} · 10 pastoralist interviews "
                                f"+ 5 ecosystem informants")
    inside = sum(1 for k, _ in blocks if k == "table")
    print(f"wrote {kit_pdf.name}  ({kit_pdf.stat().st_size / 1024:.0f} KB, "
          f"{len(blocks)} blocks, {inside} tables)")

    build_print_pack(blocks, pack_pdf)
    print(f"wrote {pack_pdf.name}  ({pack_pdf.stat().st_size / 1024:.0f} KB, "
          f"15 interview sheets + crib sheet + consent + Swahili guide)")

    build_tracker(tracker)
    print(f"wrote {tracker.name}  ({tracker.stat().st_size / 1024:.0f} KB, "
          f"{len(HYPOTHESES)} hypotheses tracked)")

    # The one simple document: the questions, Swahili + English, nothing else.
    if SRC_Q.exists():
        q_blocks = parse_md(SRC_Q.read_text(encoding="utf-8"))
        q_title = "Arda Link — Maswali ya Mom Test kwa Wachungaji"
        q_sub = ("Kiswahili (kuuliza) + English (reference) · maswali 60 · "
                 "dakika 40–60 · docs/field/mom_test_questions_only.md")
        q_pdf = OUT / "ArdaLink_Mom_Test_Questions.pdf"
        q_docx = OUT / "ArdaLink_Mom_Test_Questions.docx"
        render_pdf(q_blocks, q_pdf, q_sub, title=q_title)
        render_docx(q_blocks, q_docx, q_sub)
        print(f"wrote {q_pdf.name} + {q_docx.name}  "
              f"({q_pdf.stat().st_size / 1024:.0f} / {q_docx.stat().st_size / 1024:.0f} KB, "
              f"questions only)")
    else:
        print(f"note: {SRC_Q.name} not found, skipped the questions-only document")
    return 0


if __name__ == "__main__":
    sys.exit(main())










