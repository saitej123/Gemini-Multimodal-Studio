"""Fill the AI Builder Cup template without changing its type.

Starts from the official template. Does not delete slides, does not restyle
titles, and does not introduce Arial. New text uses the embedded faces:

  Google Sans Flex SemiBold   headings we add
  Google Sans Flex Medium     already on the template titles
  Google Sans Flex            body, same as "Team name:" on slide 1

The theme default is Arial. Every new run sets latin/ea/cs/sym or PowerPoint
falls back to that default.
"""
from __future__ import annotations

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

SRC = "Template/Submission Template _ AI Builder Cup.pptx"
OUT = "Template/Out/Multimodal Studio - AI Builder Cup.pptx"

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
INK = RGBColor(0x1F, 0x1F, 0x1F)
SOFT = RGBColor(0x3C, 0x40, 0x43)
MUTED = RGBColor(0x5F, 0x63, 0x68)
LINE = RGBColor(0xDA, 0xDC, 0xE0)
PAPER = RGBColor(0xF8, 0xF9, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
# Google palette: solid accent + its pale tint for card fills.
BLUE, BLUE_T = RGBColor(0x42, 0x85, 0xF4), RGBColor(0xE8, 0xF0, 0xFE)
RED, RED_T = RGBColor(0xEA, 0x43, 0x35), RGBColor(0xFC, 0xE8, 0xE6)
YELLOW, YELLOW_T = RGBColor(0xF9, 0xAB, 0x00), RGBColor(0xFE, 0xF7, 0xE0)
GREEN, GREEN_T = RGBColor(0x1E, 0x8E, 0x3E), RGBColor(0xE6, 0xF4, 0xEA)
PALETTE = [(BLUE, BLUE_T), (RED, RED_T), (YELLOW, YELLOW_T), (GREEN, GREEN_T)]

SEMI = "Google Sans Flex SemiBold"
MED = "Google Sans Flex Medium"
REG = "Google Sans Flex"


def _face(run, name: str, size_pt: float, color=None) -> None:
    """Pin every script slot. A latin-only set still renders as Arial."""
    run.font.size = Pt(size_pt)
    run.font.bold = False
    run.font.italic = False
    run.font.color.rgb = color or INK
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    for tag in ("latin", "ea", "cs", "sym"):
        el = rPr.find(f"{A}{tag}")
        if el is None:
            el = etree.SubElement(rPr, f"{A}{tag}")
        el.set("typeface", name)


def _box(slide, left, top, width, height):
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    return tf


def find(prs, needle: str):
    for slide in prs.slides:
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            blob = "\n".join(p.text for p in sh.text_frame.paragraphs)
            if needle in blob:
                return slide, sh
    raise KeyError(needle)


def fill_team(prs) -> None:
    """Keep the template's own runs and faces. Only fill the blanks it left."""
    _, shape = find(prs, "Team name:")
    tf = shape.text_frame
    # 0 Team Details (SemiBold 21), 2 name, 3 leader, 4 problem — all Google Sans Flex 18.
    tf.paragraphs[2].runs[0].text = "Team name: Multimodal Studio"
    tf.paragraphs[3].runs[0].text = "Team leader name: M Sai Teja"
    tf.paragraphs[4].runs[0].text = (
        "Problem statement: A teacher still rebuilds a chapter as slides, then records it. "
        "The voice and the picture do not move together, so students get a page read aloud."
    )



_WARN: list[str] = []


def _fits(text: str, width_in: float, size_pt: float, height_in: float, label: str) -> None:
    """Rough fit check (no renderer here): ~0.5 em per character, 1.22 line height."""
    import math
    cpl = max(1, int(width_in * 72 / (size_pt * 0.52)))
    lines = sum(max(1, math.ceil(len(part) / cpl)) for part in text.split("\n"))
    need = lines * size_pt * 1.22 / 72
    if need > height_in:
        _WARN.append(f"{label}: needs {need:.2f}in, has {height_in:.2f}in ({text[:40]!r})")


def _style_shape(sh, fill, line=None, radius=0.08):
    sh.shadow.inherit = False
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(0.75)
    if radius is not None:
        try:
            sh.adjustments[0] = radius
        except Exception:
            pass


def card(slide, x, y, w, h, *, title="", body="", accent=BLUE, tint=PAPER, num=None,
         title_size=14, body_size=12, anchor="t", pad=0.16, line=LINE, title_color=None):
    """Rounded card: optional number badge, SemiBold title, regular body."""
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    _style_shape(sh, tint, line, 0.07)
    tf = sh.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = tf.margin_right = Inches(pad)
    tf.margin_top = Inches(pad * 0.8 + (0.42 if num is not None else 0))
    tf.margin_bottom = Inches(pad * 0.6)
    tf._txBody.bodyPr.set("anchor", anchor)
    first = True
    inner_w = w - 2 * pad
    avail = h - (pad * 1.4 + (0.42 if num is not None else 0))
    if title:
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(4 if body else 0)
        r = p.add_run()
        r.text = title
        _face(r, SEMI, title_size, title_color)
        first = False
    if body:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(0)
        r = p.add_run()
        r.text = body
        _face(r, REG, body_size, SOFT)
    _fits((title + "\n" if title else "") + body, inner_w, max(title_size, body_size) if not body else body_size, avail, f"card[{title or body[:20]}]")
    if num is not None:
        badge(slide, x + pad, y + pad * 0.7, str(num), accent)
    return sh


def badge(slide, x, y, text, color, d=0.32):
    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    _style_shape(sh, color, None, None)
    tf = sh.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf._txBody.bodyPr.set("anchor", "ctr")
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    _face(r, SEMI, 12, WHITE)
    return sh


def pill(slide, x, y, w, h, text, tint=BLUE_T, color=INK, size=12, face=MED):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    _style_shape(sh, tint, None, 0.5)
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.12)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    tf._txBody.bodyPr.set("anchor", "ctr")
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    _face(r, face, size, color)
    _fits(text, w - 0.24, size, h - 0.06, f"pill[{text[:20]}]")
    return sh


def label(slide, x, y, w, h, text, size=12, face=REG, color=SOFT, align=PP_ALIGN.LEFT):
    tf = _box(slide, x, y, w, h)
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    _face(r, face, size, color)
    _fits(text, w, size, h, f"label[{text[:20]}]")
    return tf


def arrow(slide, x1, y1, x2, y2, color=MUTED):
    """Straight connector with an arrowhead at (x2, y2)."""
    c = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(1.5)
    ln = c.line._get_or_add_ln()
    tail = etree.SubElement(ln, f"{A}tailEnd")
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")
    return c


def picture(slide, path, x, y, w, h):
    pic = slide.shapes.add_picture(path, Inches(x), Inches(y), Inches(w), Inches(h))
    pic.line.color.rgb = LINE
    pic.line.width = Pt(1)
    return pic


def strip(slide, text, y=4.95, h=0.42):
    """One-line takeaway under the content."""
    pill(slide, 0.32, y, 9.36, h, text, tint=INK, color=WHITE, size=12, face=MED)


# ------------------------------------------------------------------ slides ----

def s_brief(slide) -> None:
    label(slide, 0.32, 1.42, 9.36, 0.9, "Drop a chapter. Get a lesson students keep watching.", size=26, face=SEMI, color=INK)
    label(slide, 0.32, 2.12, 9.36, 0.5,
          "Six real looks, hand-drawn motion, and a highlight that follows the voice. It plays like a teacher at the board, not a deck read aloud.",
          size=13, color=MUTED)
    cols = [
        ("You give", "A PDF, a few notes, or just a topic. Pick the class age, the video shape and a look.", BLUE, BLUE_T),
        ("Studio does", "Writes short slides sized to the content, speaks each one, draws the strokes on and films it all.", RED, RED_T),
        ("Class gets", "A short MP4 for YouTube, a Short or a Reel. Replay it, share it, no recording by the teacher.", GREEN, GREEN_T),
    ]
    for i, (t, b, c, tint) in enumerate(cols):
        card(slide, 0.32 + i * 3.16, 2.78, 3.04, 1.95, title=t, body=b, accent=c, tint=tint, num=i + 1,
             title_size=15, body_size=12, line=None)
    label(slide, 0.32, 4.92, 9.36, 0.3, "Built by M Sai Teja (team leader) with Anil Kumar.", size=11, color=MUTED)


def s_opportunity(slide) -> None:
    items = [
        ("Different from what exists", "Slide tools stop at the deck. Read-aloud tools ignore the picture. Neither gives a class a finished video that looks made for the lesson.", BLUE, BLUE_T),
        ("How it fixes ed-tech drop-off", "Students leave static decks and flat read-alouds. Here the look changes with the lesson, the screen moves with the voice, and the pen draws as it explains.", RED, RED_T),
        ("USP", "Voice, highlight and hand-drawn strokes move together, point by point. Each template is built differently, not just recoloured.", GREEN, GREEN_T),
    ]
    for i, (t, b, c, tint) in enumerate(items):
        card(slide, 0.32 + i * 3.16, 2.45, 3.04, 2.75, title=t, body=b, accent=c, tint=tint, num=i + 1,
             title_size=14, body_size=12, line=None)


def s_features(slide) -> None:
    feats = [
        ("One file in, one video out", "PDF, notes or a topic. No slide editing, no recording."),
        ("Pitched at the class", "Pick the age and what students already know. The wording follows."),
        ("Six real templates", "Whiteboard, notebook, poster, editorial, broadcast, bento. Own layout, font and motion each."),
        ("Fits the content", "Type is sized per slide: short ideas go big, dense ones shrink. The whole frame is used."),
        ("Voice, highlight and pen in time", "Gemini 3.8 Live speaks. The highlight moves point by point and lines draw on as it is explained."),
        ("Ready for YouTube", "Landscape, Shorts, Reels or square from the same run."),
    ]
    for i, (t, b) in enumerate(feats):
        c, tint = PALETTE[i % 4]
        r, col = divmod(i, 3)
        card(slide, 0.32 + col * 3.16, 1.5 + r * 1.78, 3.04, 1.66, title=t, body=b, accent=c, tint=tint, num=i + 1,
             title_size=13, body_size=11.5, line=None, pad=0.14)


def s_flow(slide) -> None:
    steps = [
        ("Drop a file", "PDF, notes or topic"),
        ("Read it", "Gemini reads each page"),
        ("Write slides", "Flash picks the look"),
        ("Speak them", "Gemini 3.8 Live"),
        ("Film frames", "Chrome, incl. drawn lines"),
        ("MP4", "FFmpeg joins sound"),
    ]
    w, gap, x0, y = 1.4, 0.192, 0.32, 1.75
    for i, (t, sub) in enumerate(steps):
        c, tint = PALETTE[i % 4]
        x = x0 + i * (w + gap)
        card(slide, x, y, w, 1.55, title=t, body=sub, accent=c, tint=tint, num=i + 1, title_size=13, body_size=11, line=None, pad=0.12)
        if i < len(steps) - 1:
            arrow(slide, x + w + 0.01, y + 0.78, x + w + gap - 0.01, y + 0.78)
    label(slide, 0.32, 3.62, 9.36, 0.3, "Two ways in", size=14, face=SEMI, color=INK)
    pill(slide, 0.32, 3.98, 4.6, 0.5, "A teacher drops a file in the Studio", tint=BLUE_T)
    pill(slide, 5.08, 3.98, 4.6, 0.5, "The admin queue picks a paper overnight", tint=GREEN_T)
    label(slide, 0.32, 4.7, 9.36, 0.5, "After that it is one path, and the finished video lands back on the same page.", size=12, color=MUTED)


def s_wireframe(slide) -> None:
    picture(slide, "docs/screenshots/studio.png", 0.32, 1.5, 6.1, 3.03)
    notes = [
        ("File on the left", "Drop the chapter, or type a topic.", BLUE),
        ("Shape and voice", "Pick the class age, format, look and a Gemini voice.", RED),
        ("Run and watch", "The video plays on the same page.", GREEN),
    ]
    y = 1.5
    for i, (t, b, c) in enumerate(notes):
        card(slide, 6.62, y, 3.06, 0.98, title=t, body=b, accent=c, tint=PAPER, title_size=12.5, body_size=11, pad=0.14, line=LINE)
        badge(slide, 9.68 - 0.42, y + 0.1, str(i + 1), c, d=0.28)
        y += 1.06
    label(slide, 0.32, 4.7, 6.1, 0.4, "Screen from the running app.", size=11, color=MUTED)


def s_arch(slide) -> None:
    box = lambda x, y, w, h, t, b, tint, c: card(slide, x, y, w, h, title=t, body=b, accent=c, tint=tint,
                                                  title_size=13, body_size=11, pad=0.14, line=None)
    box(0.32, 1.7, 2.3, 1.2, "Studio page", "The teacher's browser. No login on the demo.", BLUE_T, BLUE)
    # Cloud Run container
    cont = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(3.45), Inches(1.5), Inches(3.1), Inches(3.55))
    _style_shape(cont, PAPER, LINE, 0.04)
    label(slide, 3.6, 1.58, 2.8, 0.3, "Cloud Run", size=14, face=SEMI, color=INK)
    for j, (t, b) in enumerate([("FastAPI jobs", "Runs the steps in order"), ("Chrome frames", "Films slides and drawn lines"), ("FFmpeg", "Joins picture and sound")]):
        card(slide, 3.6, 2.0 + j * 1.0, 2.8, 0.88, title=t, body=b, accent=GREEN, tint=WHITE, title_size=12, body_size=10.5, pad=0.12, line=LINE)
    box(7.38, 1.7, 2.3, 1.2, "Gemini", "3.8 Flash reads and writes. 3.8 Live speaks.", RED_T, RED)
    box(7.38, 3.2, 2.3, 0.95, "Firestore", "A copy of every job.", YELLOW_T, YELLOW)
    box(0.32, 3.2, 2.3, 0.95, "MP4", "Comes back to the teacher.", GREEN_T, GREEN)
    arrow(slide, 2.65, 2.3, 3.43, 2.3)
    arrow(slide, 6.57, 2.3, 7.36, 2.3)
    arrow(slide, 6.57, 3.67, 7.36, 3.67)
    arrow(slide, 3.43, 3.67, 2.65, 3.67)
    label(slide, 7.38, 4.4, 2.3, 0.6, "Firebase Hosting serves this pitch page.", size=10.5, color=MUTED)


def s_tech(slide) -> None:
    techs = [
        ("Gemini 3.8 Flash", "Reads the file and writes the slides."),
        ("Gemini 4 Argon", "Steps in when a chapter is dense."),
        ("Gemini 3.8 Live", "The voice for every slide."),
        ("Cloud Run", "Hosts the app and the video work."),
        ("Firestore", "Keeps a copy of each job."),
        ("Firebase Hosting", "Serves this pitch page."),
    ]
    for i, (t, b) in enumerate(techs):
        c, tint = PALETTE[[0, 0, 1, 2, 2, 3][i]]
        r, col = divmod(i, 3)
        card(slide, 0.32 + col * 3.16, 1.5 + r * 1.62, 3.04, 1.48, title=t, body=b, accent=c, tint=tint, title_size=14, body_size=12, line=None)
    label(slide, 0.32, 4.75, 9.36, 0.5, "Inside the container: FastAPI, Playwright and FFmpeg. The six looks and the drawn animation are plain HTML, CSS and a small script, so no extra model is called.", size=11.5, color=MUTED)


def s_cost(slide) -> None:
    items = [
        ("No GPU", "Every model call goes to Gemini. The looks and drawn animation are plain CSS, so they add no cost."),
        ("Free tier", "Cloud Run and Firestore stay free at review traffic."),
        ("A few rupees", "One normal video on Gemini Flash costs a few rupees."),
    ]
    for i, (t, b) in enumerate(items):
        c, tint = PALETTE[[0, 3, 2][i]]
        card(slide, 0.32 + i * 3.16, 1.75, 3.04, 2.5, title=t, body=b, accent=c, tint=tint, title_size=22, body_size=13, line=None, pad=0.2)


def s_shots(slide) -> None:
    picture(slide, "docs/screenshots/looks-sheet.png", 0.32, 1.45, 9.36, 3.52)
    label(slide, 0.32, 5.0, 9.36, 0.3,
          "Top row: whiteboard, broadcast, poster. Bottom row: editorial, bento, notebook. Real frames from our renderer.",
          size=11, color=MUTED)


def s_perf(slide) -> None:
    items = [
        ("Slowest step", "Filming the slides in Chrome takes the longest."),
        ("Run side by side", "Slide writing and voice clips go out a few at a time, not one by one."),
        ("Fit at run time", "Each slide measures itself and takes the biggest type that still fits, so nothing clips."),
        ("Honest status", "Sample decks checked in 16:9, 9:16 and square. A full MP4 on the new looks is still to confirm."),
    ]
    for i, (t, b) in enumerate(items):
        c, tint = PALETTE[[1, 0, 3, 2][i]]
        r, col = divmod(i, 2)
        card(slide, 0.32 + col * 4.76, 1.5 + r * 1.85, 4.6, 1.7, title=t, body=b, accent=c, tint=tint, num=i + 1,
             title_size=14, body_size=12.5, line=None)


def s_future(slide) -> None:
    items = [
        ("Built: six unique looks", "Whiteboard, notebook, poster, editorial, broadcast and bento. Structure, font and motion differ, not only colour."),
        ("Built: hand-drawn motion", "Underlines and doodles draw themselves, and a marker sweeps under the point being explained."),
        ("Next: Manim-style motion", "Animated formulas and graphs when the chapter has maths."),
        ("Next: questions mid-video", "A pause with a question after each section, so students answer instead of only watching."),
    ]
    for i, (t, b) in enumerate(items):
        c, tint = PALETTE[[3, 3, 0, 1][i]]
        r, col = divmod(i, 2)
        card(slide, 0.32 + col * 4.76, 1.5 + r * 1.85, 4.6, 1.7, title=t, body=b, accent=c, tint=tint, num=i + 1,
             title_size=14, body_size=12.5, line=None)


def s_links(prs) -> None:
    _, shape = find(prs, "GitHub Public Repository")
    shape.height = Inches(4.2)
    tf = shape.text_frame
    links = {
        "GitHub Public Repository": "https://github.com/saitej123/Gemini-Multimodal-Studio",
        "Demo Video Link": "Still to record: 3 to 4 minutes, one real file.",
        "Final Product Link": "https://multimodal-studio-469528918286.us-central1.run.app",
    }
    for p in list(tf.paragraphs):
        for key, val in links.items():
            if p.text.strip().startswith(key):
                newp = etree.SubElement(tf._txBody, f"{A}p")
                p._p.addnext(newp)
                from pptx.text.text import _Paragraph
                para = _Paragraph(newp, p._parent)
                para.alignment = PP_ALIGN.LEFT
                para.space_after = Pt(6)
                run = para.add_run()
                run.text = val
                _face(run, REG, 13, BLUE if val.startswith("http") else MUTED)
                if val.startswith("http"):
                    run.hyperlink.address = val


def main() -> None:
    prs = Presentation(SRC)
    fill_team(prs)

    for needle, fn in [
        ("Brief about the idea", s_brief),
        ("How different is it", s_opportunity),
        ("List of features", s_features),
        ("Process flow", s_flow),
        ("Wireframes", s_wireframe),
        ("Architecture diagram", s_arch),
        ("Technologies to be used", s_tech),
        ("Estimated implementation cost", s_cost),
        ("Snapshots of the prototype", s_shots),
        ("Prototype Performance", s_perf),
        ("Additional Details", s_future),
    ]:
        slide, _ = find(prs, needle)
        fn(slide)
    s_links(prs)

    prs.save(OUT)
    print("saved", OUT, "slides", len(prs.slides))
    for w in _WARN:
        print("FIT WARNING:", w)


if __name__ == "__main__":
    main()
