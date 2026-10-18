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

SEMI = "Google Sans Flex SemiBold"
MED = "Google Sans Flex Medium"
REG = "Google Sans Flex"


def _face(run, name: str, size_pt: float) -> None:
    """Pin every script slot. A latin-only set still renders as Arial."""
    run.font.size = Pt(size_pt)
    run.font.bold = False
    run.font.italic = False
    run.font.color.rgb = INK
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


def write(slide, lines, *, left=0.32, top=1.5, width=9.35, height=3.85, size=14, font=REG):
    """lines: str, or (text, font, size)."""
    tf = _box(slide, left, top, width, height)
    for i, item in enumerate(lines):
        if isinstance(item, tuple):
            text, face, sz = item
        else:
            text, face, sz = item, font, size
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_before = Pt(0)
        p.space_after = Pt(8)
        run = p.add_run()
        run.text = text
        _face(run, face, sz)
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


def steps(slide) -> None:
    """The process-flow slide asks for a diagram. Six plain steps, template type."""
    labels = ["Drop a file", "Read it", "Write slides", "Speak them", "Film frames", "MP4"]
    left0 = 0.32
    gap = 0.12
    w = 1.48
    top = 2.15
    h = 0.95
    for i, label in enumerate(labels):
        x = left0 + i * (w + gap)
        sh = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(top), Inches(w), Inches(h)
        )
        sh.adjustments[0] = 0.15
        sh.fill.solid()
        sh.fill.fore_color.rgb = RGBColor(0xF8, 0xF9, 0xFA)
        sh.line.color.rgb = RGBColor(0xDA, 0xDC, 0xE0)
        tf = sh.text_frame
        tf.word_wrap = True
        tf.auto_size = None
        try:
            tf._txBody.bodyPr.set("anchor", "ctr")
        except Exception:
            pass
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = label
        _face(run, SEMI, 13)
    write(
        slide,
        [
            "Upload a chapter, or let the queue pick a paper. Then one path to the video.",
        ],
        top=3.35,
        height=1.7,
        size=14,
    )


def shots(slide) -> None:
    write(
        slide,
        ["These are screens from the app we run, not drawings of a future one."],
        top=1.42,
        height=0.4,
        size=13,
    )
    pics = [
        ("docs/screenshots/studio.png", "Studio. Drop the file and run."),
        ("docs/screenshots/voices.png", "Pick a voice before you run."),
        ("docs/screenshots/history.png", "Past runs stay here."),
    ]
    x = 0.4
    for img, cap in pics:
        slide.shapes.add_picture(img, Inches(x), Inches(2.05), Inches(2.9), Inches(1.44))
        write(slide, [cap], left=x, top=3.58, width=2.9, height=0.9, size=12)
        x += 3.15


def main() -> None:
    prs = Presentation(SRC)

    fill_team(prs)

    brief, _ = find(prs, "Brief about the idea")
    write(
        brief,
        [
            "M Sai Teja leads and built this.",
            "You drop a chapter or a set of notes. You get a short video a class can replay. Gemini writes the slides. Gemini 3.8 Live speaks them.",
            "The highlight moves with the sentence, so it feels like someone explaining, not a page being read out.",
        ],
    )

    opp, _ = find(prs, "How different is it")
    write(
        opp,
        [
            ("Different from what exists", SEMI, 14),
            "Slide tools stop at the deck. Read-aloud tools ignore the picture. Neither gives a class a video.",
            ("How it solves the problem", SEMI, 14),
            "One file goes in. A video comes out. The teacher does not record, and does not line the voice up by hand.",
            ("USP", SEMI, 14),
            "The voice and the highlight stay in time. That is the part a normal deck and a normal voiceover both miss.",
        ],
        top=2.32,
        height=3.05,
        size=13,
    )

    feats, _ = find(prs, "List of features")
    write(
        feats,
        [
            "Drop a PDF. Get a video for YouTube or a Short.",
            "Say the age. The wording changes for that class.",
            "Gemini 3.8 Live speaks it, and the highlight follows the voice.",
        ],
        size=13,
    )

    flow, _ = find(prs, "Process flow")
    write(
        flow,
        ["Two ways in. You drop something, or the admin queue picks a paper overnight. After that it is one path."],
        top=1.42,
        height=0.6,
        size=13,
    )
    steps(flow)

    wire, _ = find(prs, "Wireframes")
    wire.shapes.add_picture(
        "docs/screenshots/studio.png", Inches(0.32), Inches(1.5), Inches(6.15), Inches(3.05)
    )
    write(
        wire,
        [
            ("The Studio page", SEMI, 14),
            "File on the left. Format and voice beside it. Run, then play the video on the same page.",
        ],
        left=6.65,
        top=1.5,
        width=3.05,
        height=3.4,
        size=13,
    )

    arch, _ = find(prs, "Architecture diagram")
    write(
        arch,
        [
            "Studio page  →  Cloud Run  →  Gemini  →  MP4",
            "Gemini 3.8 Flash reads the file and writes the slides. Gemini 3.8 Live speaks them. Chrome films the frames. FFmpeg joins the sound.",
            "Each job is copied to Firestore. This pitch page is on Firebase Hosting.",
        ],
        size=14,
    )

    tech, _ = find(prs, "Technologies to be used")
    write(
        tech,
        [
            "Gemini 3.8 Flash writes the slides. Gemini 4 Argon is there when the chapter is dense.",
            "Gemini 3.8 Live is the voice.",
            "Cloud Run hosts the app. Firestore keeps each job. Firebase Hosting serves this pitch.",
        ],
        size=14,
    )

    cost, _ = find(prs, "Estimated implementation cost")
    write(
        cost,
        [
            "No GPU. A normal video on Gemini Flash is a few rupees.",
            "Cloud Run and Firestore stay in the free tier for review traffic.",
        ],
    )

    snap, _ = find(prs, "Snapshots of the prototype")
    shots(snap)

    perf, _ = find(prs, "Prototype Performance")
    write(
        perf,
        [
            "A chapter-length run finishes. Filming the slides is the slow step.",
            "We have not published a benchmark table. Each job does record how long its own steps took.",
        ],
    )

    later, _ = find(prs, "Additional Details")
    write(
        later,
        [
            "Next: handwritten teaching slides, and Manim-style motion when the chapter has a formula.",
            "After that, a few YouTuber looks you can pick, or make your own.",
        ],
    )

    links, shape = find(prs, "GitHub Public Repository")
    # The template box is only tall enough for the three labels. Grow it.
    shape.height = Inches(4.2)
    tf = shape.text_frame
    notes = [
        "GitHub: https://github.com/saitej123/Gemini-Multimodal-Studio",
        "Demo video: still to record (3 to 4 minutes, one real file).",
        "Product: https://multimodal-studio-469528918286.us-central1.run.app",
    ]
    for text in notes:
        p = tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_before = Pt(8)
        p.space_after = Pt(0)
        run = p.add_run()
        run.text = text
        _face(run, REG, 13)

    # Slides 2 (download note), 15 (blank), and 16 (closing art) stay as the template drew them.
    prs.save(OUT)
    print("saved", OUT, "slides", len(prs.slides))


if __name__ == "__main__":
    main()
