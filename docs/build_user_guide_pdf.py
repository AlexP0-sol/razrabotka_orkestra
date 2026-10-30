"""Генератор подробного PDF-руководства пользователя ORCHESTRA на русском языке (тёмная тема)."""
# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# --- Палитра премиальной тёмной темы (Dark Obsidian) ---
BG_PAGE = colors.HexColor("#0B0F17")
BG_HEADER = colors.HexColor("#080B11")
BG_SURFACE = colors.HexColor("#131B29")
BG_SURFACE_ALT = colors.HexColor("#172234")
BG_TABLE_HEAD = colors.HexColor("#1E2D45")
BG_CODE = colors.HexColor("#06090F")

BORDER_SUBTLE = colors.HexColor("#243247")
BORDER_ACCENT = colors.HexColor("#38BDF8")

TXT_PRIMARY = colors.HexColor("#E2E8F0")
TXT_BRIGHT = colors.HexColor("#F8FAFC")
TXT_MUTED = colors.HexColor("#94A3B8")
TXT_ACCENT = colors.HexColor("#38BDF8")
TXT_CODE = colors.HexColor("#67E8F9")
TXT_GREEN = colors.HexColor("#34D399")
TXT_AMBER = colors.HexColor("#FBBF24")
TXT_RED = colors.HexColor("#F87171")
TXT_PURPLE = colors.HexColor("#A78BFA")

CALLOUT_THEMES: dict[str, tuple[colors.Color, colors.Color, str]] = {
    "nuance": (colors.HexColor("#17152B"), TXT_PURPLE, "ВАЖНЫЙ НЮАНС"),
    "warn": (colors.HexColor("#231C10"), TXT_AMBER, "ВНИМАНИЕ"),
    "danger": (colors.HexColor("#261318"), TXT_RED, "БЕЗОПАСНОСТЬ И ДЕНЬГИ"),
    "tip": (colors.HexColor("#0E231D"), TXT_GREEN, "ПРАКТИЧЕСКИЙ СОВЕТ"),
}

PAGE_W, PAGE_H = A4
MARGIN_X = 16 * mm
MARGIN_TOP = 18 * mm
MARGIN_BOTTOM = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X


def register_fonts() -> None:
    """Зарегистрировать системные Unicode-шрифты DejaVu с полной поддержкой кириллицы."""
    font_dir = Path("/usr/share/fonts/truetype/dejavu")
    pdfmetrics.registerFont(TTFont("DVSans", str(font_dir / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DVSansBold", str(font_dir / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DVMono", str(font_dir / "DejaVuSansMono.ttf")))
    pdfmetrics.registerFont(TTFont("DVMonoBold", str(font_dir / "DejaVuSansMono-Bold.ttf")))


class NumberedDarkCanvas(pdfcanvas.Canvas):
    """Холст с двухпроходным подсчётом `Стр. X из Y` на бесшовном тёмном фоне."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._saved_page_states: list[dict[str, object]] = []

    def showPage(self) -> None:  # noqa: N802
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(total_pages)
            super().showPage()
        super().save()

    def _draw_page_number(self, total_pages: int) -> None:
        page_num = self._pageNumber
        if page_num == 1:
            return
        self.saveState()
        self.setFont("DVSans", 8.2)
        self.setFillColor(TXT_MUTED)
        self.drawRightString(
            PAGE_W - MARGIN_X,
            8.5 * mm,
            f"Стр. {page_num} из {total_pages}",
        )
        self.restoreState()


class DarkManualDocTemplate(BaseDocTemplate):
    """Документ с полностраничной тёмной заливкой и автогенерацией PDF-закладок (Outline)."""

    def __init__(self, filename: str) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=MARGIN_X,
            rightMargin=MARGIN_X,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title="ORCHESTRA v0.1.0 — Полное руководство пользователя (Dark Edition)",
            author="ORCHESTRA Engineering",
            subject="Подробная инструкция по настройке, запуску и всем нюансам ORCHESTRA",
        )
        self.current_section = "01. Как устроен ORCHESTRA и где хранятся файлы проектов"
        self._bookmark_idx = 0

        cover_frame = Frame(
            MARGIN_X,
            MARGIN_BOTTOM,
            CONTENT_W,
            PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
            id="cover_frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        body_frame = Frame(
            MARGIN_X,
            MARGIN_BOTTOM,
            CONTENT_W,
            PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
            id="body_frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=2 * mm,
            bottomPadding=2 * mm,
        )
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[cover_frame], onPage=self._paint_cover_bg),
                PageTemplate(id="body", frames=[body_frame], onPage=self._paint_body_bg),
            ]
        )

    def afterFlowable(self, flowable: object) -> None:  # noqa: N802
        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            if style_name == "H1":
                plain = getattr(flowable, "_bookmark_title", flowable.getPlainText())
                self.current_section = plain
                key = f"sec_{self._bookmark_idx}"
                self._bookmark_idx += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(plain, key, level=0, closed=False)
            elif style_name == "H2":
                plain = getattr(flowable, "_bookmark_title", flowable.getPlainText())
                key = f"sub_{self._bookmark_idx}"
                self._bookmark_idx += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(plain, key, level=1, closed=False)

    def _paint_cover_bg(self, canv: pdfcanvas.Canvas, _doc: BaseDocTemplate) -> None:
        canv.saveState()
        canv.setFillColor(BG_PAGE)
        canv.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

        # Декоративные геометрические акценты обложки
        canv.setFillColor(colors.HexColor("#0F172A"))
        canv.rect(0, PAGE_H - 92 * mm, PAGE_W, 92 * mm, stroke=0, fill=1)

        canv.setStrokeColor(BORDER_ACCENT)
        canv.setLineWidth(2.2)
        canv.line(MARGIN_X, PAGE_H - 92 * mm, PAGE_W - MARGIN_X, PAGE_H - 92 * mm)

        canv.setStrokeColor(colors.HexColor("#1E293B"))
        canv.setLineWidth(0.6)
        for offset in range(1, 8):
            y_pos = PAGE_H - (offset * 11 * mm)
            canv.line(0, y_pos, PAGE_W, y_pos)

        canv.setFillColor(TXT_MUTED)
        canv.setFont("DVSans", 8.2)
        canv.drawString(
            MARGIN_X,
            10 * mm,
            "ORCHESTRA v0.1.0 (ТЗ v1.2.1)  •  Автономная мультиагентная фабрика ПО",
        )
        canv.drawRightString(PAGE_W - MARGIN_X, 10 * mm, "Dark Theme Edition  •  RU")
        canv.restoreState()

    def _paint_body_bg(self, canv: pdfcanvas.Canvas, _doc: BaseDocTemplate) -> None:
        canv.saveState()
        # Полная бесшовная тёмная заливка страницы от края до края
        canv.setFillColor(BG_PAGE)
        canv.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

        # Верхняя плашка колонтитула
        canv.setFillColor(BG_HEADER)
        canv.rect(0, PAGE_H - 13 * mm, PAGE_W, 13 * mm, stroke=0, fill=1)
        canv.setStrokeColor(BORDER_SUBTLE)
        canv.setLineWidth(0.6)
        canv.line(MARGIN_X, PAGE_H - 13 * mm, PAGE_W - MARGIN_X, PAGE_H - 13 * mm)

        canv.setFont("DVSansBold", 8.2)
        canv.setFillColor(TXT_ACCENT)
        canv.drawString(MARGIN_X, PAGE_H - 8.5 * mm, "ORCHESTRA  //  РУКОВОДСТВО ОПЕРАТОРА")

        canv.setFont("DVSans", 8.0)
        canv.setFillColor(TXT_MUTED)
        sec_title = self.current_section
        if len(sec_title) > 58:
            sec_title = sec_title[:55] + "..."
        canv.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 8.5 * mm, sec_title)

        # Нижняя разделительная черта колонтитула
        canv.setStrokeColor(BORDER_SUBTLE)
        canv.line(MARGIN_X, 13 * mm, PAGE_W - MARGIN_X, 13 * mm)
        canv.setFont("DVSans", 7.8)
        canv.setFillColor(TXT_MUTED)
        canv.drawString(
            MARGIN_X,
            8.5 * mm,
            "100% локальное хранение проектов (./runtime)  •  Безопасный контур",
        )
        canv.restoreState()


def build_styles() -> dict[str, ParagraphStyle]:
    """Сформировать типографическую шкалу для тёмной темы."""
    return {
        "CoverBadge": ParagraphStyle(
            "CoverBadge",
            fontName="DVMonoBold",
            fontSize=9.5,
            leading=13,
            textColor=TXT_ACCENT,
            spaceAfter=6,
        ),
        "CoverTitle": ParagraphStyle(
            "CoverTitle",
            fontName="DVSansBold",
            fontSize=27,
            leading=33,
            textColor=TXT_BRIGHT,
            spaceAfter=8,
        ),
        "CoverSub": ParagraphStyle(
            "CoverSub",
            fontName="DVSans",
            fontSize=12.5,
            leading=18,
            textColor=colors.HexColor("#CBD5E1"),
            spaceAfter=14,
        ),
        "H1": ParagraphStyle(
            "H1",
            fontName="DVSansBold",
            fontSize=15.5,
            leading=21,
            textColor=TXT_BRIGHT,
            spaceBefore=10,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "H2": ParagraphStyle(
            "H2",
            fontName="DVSansBold",
            fontSize=11.8,
            leading=16,
            textColor=TXT_ACCENT,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "H3": ParagraphStyle(
            "H3",
            fontName="DVSansBold",
            fontSize=10.2,
            leading=14,
            textColor=TXT_GREEN,
            spaceBefore=6,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "Body": ParagraphStyle(
            "Body",
            fontName="DVSans",
            fontSize=9.4,
            leading=14.0,
            textColor=TXT_PRIMARY,
            alignment=TA_JUSTIFY,
            spaceAfter=5,
        ),
        "Bullet": ParagraphStyle(
            "Bullet",
            fontName="DVSans",
            fontSize=9.3,
            leading=13.6,
            leftIndent=12,
            firstLineIndent=-8,
            textColor=TXT_PRIMARY,
            alignment=TA_LEFT,
            spaceAfter=3.5,
        ),
        "Code": ParagraphStyle(
            "Code",
            fontName="DVMono",
            fontSize=8.2,
            leading=11.8,
            textColor=TXT_CODE,
            alignment=TA_LEFT,
        ),
        "CalloutTitle": ParagraphStyle(
            "CalloutTitle",
            fontName="DVSansBold",
            fontSize=9.0,
            leading=12.5,
            spaceAfter=2,
        ),
        "CalloutBody": ParagraphStyle(
            "CalloutBody",
            fontName="DVSans",
            fontSize=8.9,
            leading=13.0,
            textColor=TXT_PRIMARY,
        ),
        "TblHead": ParagraphStyle(
            "TblHead",
            fontName="DVSansBold",
            fontSize=8.5,
            leading=11.5,
            textColor=TXT_ACCENT,
        ),
        "TblCell": ParagraphStyle(
            "TblCell",
            fontName="DVSans",
            fontSize=8.3,
            leading=11.6,
            textColor=TXT_PRIMARY,
        ),
        "TblMono": ParagraphStyle(
            "TblMono",
            fontName="DVMono",
            fontSize=7.9,
            leading=11.0,
            textColor=TXT_CODE,
        ),
        "TOCRow": ParagraphStyle(
            "TOCRow",
            fontName="DVSans",
            fontSize=9.4,
            leading=14.2,
            textColor=TXT_PRIMARY,
        ),
    }


def c_inline(text: str) -> str:
    """Обернуть фрагмент в моноширинный циановый инлайн-код."""
    return f'<font name="DVMono" color="#67E8F9">{escape(text)}</font>'


def b_inline(text: str) -> str:
    """Полужирный яркий акцент внутри абзаца."""
    return f'<font name="DVSansBold" color="#F8FAFC">{escape(text)}</font>'


def h1(num: str, title: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    """Создать заголовок H1 с акцентным номером и чистой меткой для PDF-закладки."""
    markup = f'<font color="#38BDF8">{escape(num)}.</font> {escape(title)}'
    p = Paragraph(markup, styles["H1"])
    p._bookmark_title = f"{num}. {title}"  # type: ignore[attr-defined]
    return p


def h2(title: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    """Создать подзаголовок H2 с меткой для PDF-закладки."""
    p = Paragraph(escape(title), styles["H2"])
    p._bookmark_title = title  # type: ignore[attr-defined]
    return p


def bullet(html_text: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    """Создать маркированный пункт с голубым маркером."""
    return Paragraph(f'<font color="#38BDF8">▸</font> {html_text}', styles["Bullet"])


def code_block(
    lines: list[str], styles: dict[str, ParagraphStyle], title: str = ""
) -> KeepTogether:
    """Отрисовать тёмный терминальный блок кода с заголовком и цветной левой границей."""
    escaped_lines = [escape(line).replace(" ", "&nbsp;") for line in lines]
    body_html = "<br/>".join(escaped_lines)
    rows: list[list[object]] = []
    if title:
        hdr = Paragraph(
            f'<font name="DVMonoBold" color="#94A3B8" size="7.8">{escape(title)}</font>',
            styles["Code"],
        )
        rows.append([hdr])
    rows.append([Paragraph(body_html, styles["Code"])])

    tbl = Table(rows, colWidths=[CONTENT_W])
    style_cmds: list[tuple[object, ...]] = [
        ("BACKGROUND", (0, 0), (-1, -1), BG_CODE),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER_SUBTLE),
        ("LINEBEFORE", (0, 0), (0, -1), 2.6, BORDER_ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if title:
        style_cmds.extend(
            [
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#0F1623")),
                ("LINEBELOW", (0, 0), (0, 0), 0.5, BORDER_SUBTLE),
                ("TOPPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 0), (0, 0), 4),
            ]
        )
    tbl.setStyle(TableStyle(style_cmds))
    return KeepTogether([Spacer(1, 2), tbl, Spacer(1, 5)])


def callout(
    kind: str,
    title_extra: str,
    body_html: str,
    styles: dict[str, ParagraphStyle],
) -> KeepTogether:
    """Создать цветную информационную врезку (Нюанс / Внимание / Безопасность / Совет)."""
    bg_col, accent_col, prefix = CALLOUT_THEMES[kind]
    hex_str = accent_col.hexval().replace("0x", "#")
    full_title = f"{prefix}: {title_extra}" if title_extra else prefix
    head_p = Paragraph(
        f'<font name="DVSansBold" color="{hex_str}">● {escape(full_title)}</font>',
        styles["CalloutTitle"],
    )
    body_p = Paragraph(body_html, styles["CalloutBody"])
    tbl = Table([[head_p], [body_p]], colWidths=[CONTENT_W])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_col),
                ("BOX", (0, 0), (-1, -1), 0.7, BORDER_SUBTLE),
                ("LINEBEFORE", (0, 0), (0, -1), 3.0, accent_col),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                ("TOPPADDING", (0, 1), (0, 1), 1),
                ("BOTTOMPADDING", (0, 1), (0, 1), 6),
            ]
        )
    )
    return KeepTogether([Spacer(1, 3), tbl, Spacer(1, 5)])


def dark_table(
    headers: list[str],
    rows: list[list[str]],
    col_widths_mm: list[float],
    styles: dict[str, ParagraphStyle],
    mono_cols: set[int] | None = None,
) -> Table:
    """Построить таблицу в тёмной теме с зебра-строками и переносом слов."""
    mono_set = mono_cols or set()
    data: list[list[Paragraph]] = [[Paragraph(escape(h), styles["TblHead"]) for h in headers]]
    for row in rows:
        rendered_row: list[Paragraph] = []
        for idx, cell in enumerate(row):
            st = styles["TblMono"] if idx in mono_set else styles["TblCell"]
            rendered_row.append(Paragraph(cell.replace("\n", "<br/>"), st))
        data.append(rendered_row)

    widths = [w * mm for w in col_widths_mm]
    tbl = Table(data, colWidths=widths, repeatRows=1)
    cmds: list[tuple[object, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_TABLE_HEAD),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER_SUBTLE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_SUBTLE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for r_idx in range(1, len(data)):
        bg = BG_SURFACE if r_idx % 2 == 1 else BG_SURFACE_ALT
        cmds.append(("BACKGROUND", (0, r_idx), (-1, r_idx), bg))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def draw_pipeline_diagram() -> Drawing:
    """Векторная схема боевого конвейера ORCHESTRA в тёмной теме."""
    w = CONTENT_W
    h = 62 * mm
    d = Drawing(w, h)
    d.add(
        Rect(0, 0, w, h, fillColor=BG_CODE, strokeColor=BORDER_SUBTLE, strokeWidth=0.8, rx=4, ry=4)
    )

    stages = [
        ("1. Вход ТЗ", "CLI / файл .md", "#38BDF8"),
        ("2. R&D (S1)", "Проверка домена", "#818CF8"),
        ("3. Архитектор", "DAG + Контракты", "#A78BFA"),
        ("4. Рой агентов", "Код <= 300 стр.", "#34D399"),
        ("5. Судья (Judge)", "AST+Ruff+Pytest", "#FBBF24"),
        ("6. Git + Опыт", "Коммит & Дневник", "#F472B6"),
    ]
    box_w = 25.5 * mm
    box_h = 23 * mm
    gap = 4.2 * mm
    start_x = 4.5 * mm
    y = 26 * mm

    d.add(
        String(
            start_x,
            h - 6.8 * mm,
            "СКВОЗНОЙ КОНВЕЙЕР СБОРКИ ПРОЕКТА (S0 -> S14)",
            fontName="DVSansBold",
            fontSize=8.5,
            fillColor=TXT_ACCENT,
        )
    )

    for idx, (title, sub, hex_c) in enumerate(stages):
        x = start_x + idx * (box_w + gap)
        col = colors.HexColor(hex_c)
        d.add(
            Rect(
                x,
                y,
                box_w,
                box_h,
                fillColor=BG_SURFACE,
                strokeColor=col,
                strokeWidth=1.1,
                rx=3,
                ry=3,
            )
        )
        d.add(
            String(
                x + box_w / 2,
                y + 14.0 * mm,
                title,
                fontName="DVSansBold",
                fontSize=7.4,
                fillColor=TXT_BRIGHT,
                textAnchor="middle",
            )
        )
        d.add(
            String(
                x + box_w / 2,
                y + 6.2 * mm,
                sub,
                fontName="DVSans",
                fontSize=6.8,
                fillColor=TXT_MUTED,
                textAnchor="middle",
            )
        )
        if idx < len(stages) - 1:
            ax1 = x + box_w + 0.6 * mm
            ax2 = x + box_w + gap - 0.8 * mm
            ay = y + box_h / 2
            d.add(Line(ax1, ay, ax2, ay, strokeColor=TXT_ACCENT, strokeWidth=1.1))
            d.add(
                Polygon(
                    points=[ax2, ay, ax2 - 2.0 * mm, ay + 1.2 * mm, ax2 - 2.0 * mm, ay - 1.2 * mm],
                    fillColor=TXT_ACCENT,
                    strokeColor=TXT_ACCENT,
                )
            )

    # Петля возврата при ошибках судьи (<= 2 ретраев) — выше нижней строки результата
    rx1 = start_x + 4 * (box_w + gap) + box_w / 2
    rx2 = start_x + 3 * (box_w + gap) + box_w / 2
    loop_y = 14.5 * mm
    d.add(Line(rx1, y, rx1, loop_y, strokeColor=TXT_AMBER, strokeWidth=0.9))
    d.add(Line(rx1, loop_y, rx2, loop_y, strokeColor=TXT_AMBER, strokeWidth=0.9))
    d.add(Line(rx2, loop_y, rx2, y, strokeColor=TXT_AMBER, strokeWidth=0.9))
    d.add(
        String(
            (rx1 + rx2) / 2,
            loop_y + 2.0 * mm,
            "Дефекты -> до 2 авто-исправлений",
            fontName="DVSans",
            fontSize=6.8,
            fillColor=TXT_AMBER,
            textAnchor="middle",
        )
    )

    d.add(
        Line(start_x, 10.5 * mm, w - start_x, 10.5 * mm, strokeColor=BORDER_SUBTLE, strokeWidth=0.5)
    )
    d.add(
        Circle(
            start_x + 2 * mm,
            5.8 * mm,
            1.4 * mm,
            fillColor=TXT_GREEN,
            strokeColor=TXT_GREEN,
        )
    )
    d.add(
        String(
            start_x + 5.5 * mm,
            4.7 * mm,
            "Результат: готовый локальный Git-репозиторий в ./runtime/projects/<id>/workspace/",
            fontName="DVSans",
            fontSize=7.4,
            fillColor=TXT_PRIMARY,
        )
    )
    return d


def build_story(styles: dict[str, ParagraphStyle]) -> list[object]:
    """Собрать все 13 подробных разделов руководства пользователя."""
    s: list[object] = []

    # ==================== ОБЛОЖКА ====================
    s.append(Spacer(1, 10 * mm))
    s.append(Paragraph("AUTONOMOUS MULTI-AGENT SOFTWARE FACTORY  //  v0.1.0", styles["CoverBadge"]))
    s.append(Paragraph("ORCHESTRA: Полное руководство пользователя", styles["CoverTitle"]))
    s.append(
        Paragraph(
            "Исчерпывающая практическая инструкция на русском языке: установка, интерактивная "
            "настройка ключей и модели, постановка ТЗ, управление пулом API, Web-дашборд, "
            "Telegram-бридж, крипто-домен и все скрытые технические нюансы эксплуатации.",
            styles["CoverSub"],
        )
    )
    s.append(Spacer(1, 18 * mm))

    cover_cards = [
        [
            f"{b_inline('100% локальное хранение')}<br/>"
            "Весь написанный код, тесты, коммиты, граф задач и дневники агентов хранятся "
            "только у вас на компьютере в папке ./runtime/.",
            f"{b_inline('Детерминированный Судья')}<br/>"
            "Ни один узел не считается готовым «на слово» модели: код проверяется AST, "
            "Ruff, Pytest и доменными правилами до exit_code == 0.",
        ],
        [
            f"{b_inline('Умный пул API-ключей')}<br/>"
            "Работает даже с 1 ключом, а при нескольких — балансирует нагрузку и мгновенно "
            "переключается на резервный ключ при ошибке HTTP 429.",
            f"{b_inline('Мастер setup_env.py')}<br/>"
            "Поочерёдный ввод модели и нужного числа ключей в терминале: лишние поля "
            "и слоты пропускаются обычным нажатием пробела.",
        ],
    ]
    c_tbl = dark_table(
        ["Ключевые возможности ORCHESTRA", "Архитектурные гарантии и удобство"],
        cover_cards,
        [89, 89],
        styles,
    )
    s.append(c_tbl)
    s.append(Spacer(1, 10 * mm))

    toc_preview = [
        ["01", "Как устроен ORCHESTRA и где хранятся файлы проектов"],
        ["02", "Установка на Windows 11, Linux, macOS и в Docker"],
        ["03", "Интерактивная настройка через setup_env.py (правило пробела)"],
        ["04", "Пул API-ключей, лимиты HTTP 429 и выбор LLM-модели"],
        ["05", "Самодиагностика (doctor) и офлайн-проверка (demo)"],
        ["06", "Как правильно писать ТЗ и запускать сборку (orchestra run)"],
        ["07", "Полный справочник всех команд CLI"],
        ["08", "Веб-консоль оператора (serve) и ответы на эскалации"],
        ["09", "Telegram-бридж оператора: настройка и нюансы безопасности"],
        ["10", "Накопление опыта: Скиллы (/skills/) и Таланты (/agent_talents/)"],
        ["11", "Крипто-домен: мультибиржевая база, Decimal и защита LIVE-режима"],
        ["12", "Бюджеты токенов, тайм-ауты и автовосстановление после сбоев"],
        ["13", "Справочник переменных .env и решение типовых проблем (FAQ)"],
    ]
    s.append(
        dark_table(
            ["№", "Содержание руководства (доступно также в боковых закладках PDF)"],
            toc_preview,
            [14, 164],
            styles,
            mono_cols={0},
        )
    )

    s.append(NextPageTemplate("body"))
    s.append(PageBreak())

    # ==================== РАЗДЕЛ 1 ====================
    s.append(h1("01", "Как устроен ORCHESTRA и где хранятся файлы проектов", styles))
    s.append(
        Paragraph(
            "ORCHESTRA — это локальная автономная фабрика разработки программного обеспечения "
            "на собственном асинхронном движке Python (asyncio) без внешних тяжёлых фреймворков "
            "вроде LangChain или CrewAI. Вместо хаотичного «общего чата ботов» система работает "
            "как строгое конструкторское бюро: Мета-Архитектор строит ациклический граф задач "
            f"({c_inline('MentalMap')}), замораживает публичные интерфейсы ({c_inline('ContractSignature')}), "
            "раздаёт изолированные файлы профильным агентам, а приёмку ведёт независимый "
            f"детерминированный Судья ({c_inline('IndependentJudge')}).",
            styles["Body"],
        )
    )
    s.append(Spacer(1, 3))
    s.append(draw_pipeline_diagram())
    s.append(Spacer(1, 4))

    s.append(h2("1.1. Где именно лежат файлы написанных проектов (приватность 100%)", styles))
    s.append(
        Paragraph(
            f"Все результаты работы оркестратора сохраняются {b_inline('исключительно локально на вашем компьютере')} "
            f"внутри рабочей директории {c_inline('./runtime/')} (либо в другой папке на вашем диске, "
            f"которую вы укажете в переменной {c_inline('ORCHESTRA_RUNTIME_DIR')}). "
            "Ни один файл сгенерированного проекта никогда автоматически не загружается в облако "
            "или чужой GitHub-репозиторий.",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "razrabotka_orkestra/                 # Папка самой программы ORCHESTRA",
                "├── .env                             # Ваши локальные ключи (в .gitignore, права 0600)",
                "├── setup_env.py                     # Интерактивный мастер ввода модели и ключей",
                "└── runtime/                         # ВСЕ ДАННЫЕ И НАПИСАННЫЕ ПРОЕКТЫ (в .gitignore!)",
                "    ├── projects/",
                "    │   └── <project_id>/            # Папка конкретного заказанного вами проекта",
                "    │       ├── workspace/           # <-- ГОТОВЫЙ КОД ПРОЕКТА + свой локальный .git!",
                "    │       ├── mental_map.json      # Граф узлов, статусы и чексумма SHA-256",
                "    │       ├── mental_map.json.1.bak# Ротируемые резервные копии состояния (до 5 шт.)",
                "    │       ├── events.jsonl         # Неизменяемый журнал всех шагов (хеш-цепочка)",
                "    │       └── rpc_messages.jsonl   # Аудит межагентных запросов",
                "    ├── agent_talents/<role_id>/     # Накопленный опыт профессий и DISCOVERY_DIARY.md",
                "    ├── skills/                      # Исполняемые скиллы (manifest.json + тесты)",
                "    ├── cache/                       # Кэш capability-проб моделей и состояния пула",
                "    └── docs_cache/                  # Локальный офлайн-кэш документации для R&D",
            ],
            styles,
            title="Структура локального хранения на вашем диске",
        )
    )

    s.append(
        callout(
            "nuance",
            "Два разных Git-репозитория — не перепутайте их!",
            f"У самого оркестратора есть свой корневой {c_inline('.git')}, а у каждого написанного им "
            f"проекта внутри {c_inline('runtime/projects/<id>/workspace/.git')} автоматически создаётся "
            f"{b_inline('собственный отдельный Git-репозиторий')} на ветке {c_inline('orchestra/<id>')}. "
            "Благодаря этому генерируемый агентами код никогда не засоряет и не ломает код самого "
            "оркестратора. Когда проект готов, вы можете просто скопировать папку "
            f"{c_inline('workspace/')} куда угодно или зайти в неё и выполнить "
            f"{c_inline('git remote add origin <ваш_URL> && git push')}.",
            styles,
        )
    )

    s.append(h2("1.2. Что вообще передаётся по сети?", styles))
    s.append(
        bullet(
            f"{b_inline('В офлайн-режиме (--mock / demo)')}: {b_inline('0 байт в интернет')}. "
            "Никакие внешние серверы не вызываются, даже подсчёт токенов работает офлайн.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('В боевом режиме (openai_compat)')}: по HTTPS отправляются только промпты "
            f"к выбранному вами LLM-серверу (например, {c_inline('integrate.api.nvidia.com')}). "
            "Перед логированием все секреты вырезаются потоковым фильтром "
            f"{c_inline('SecretRedactor')}.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('Telegram (по умолчанию ВЫКЛЮЧЕН)')}: соединяется с {c_inline('api.telegram.org')} "
            "только если вы сами ввели токен бота и свой числовой User ID.",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 2 ====================
    s.append(h1("02", "Установка на Windows 11, Linux, macOS и в Docker", styles))
    s.append(
        Paragraph(
            "Для работы требуется Python версии 3.11 или 3.12 и установленный Git (>= 2.35). "
            "Все зависимости строго зафиксированы точными версиями в файлах "
            f"{c_inline('requirements.txt')} и {c_inline('requirements-dev.txt')}.",
            styles["Body"],
        )
    )

    s.append(h2("2.1. Установка на Linux и macOS", styles))
    s.append(
        code_block(
            [
                "git clone https://github.com/AlexP0-sol/razrabotka_orkestra.git",
                "cd razrabotka_orkestra",
                "git checkout arena/77f5f735-razrabotka-orkestra",
                "",
                "# Создаёт виртуальное окружение .venv и ставит все пакеты:",
                "make install",
            ],
            styles,
            title="Терминал Linux / macOS",
        )
    )

    s.append(h2("2.2. Установка на Windows 11 (PowerShell) — важные нюансы", styles))
    s.append(
        Paragraph(
            "На стандартной Windows утилита make часто не установлена по умолчанию, поэтому "
            "используйте прямые команды PowerShell ниже. Оркестратор изначально спроектирован "
            "под полноценную нативную работу на Windows 11:",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "git clone https://github.com/AlexP0-sol/razrabotka_orkestra.git",
                "cd razrabotka_orkestra",
                "git checkout arena/77f5f735-razrabotka-orkestra",
                "",
                "# 1. Создаём виртуальное окружение Python 3.11 / 3.12:",
                "py -3.11 -m venv .venv",
                "",
                "# 2. Разрешаем активацию скриптов в текущем окне PowerShell и активируем .venv:",
                "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass",
                ".\\.venv\\Scripts\\Activate.ps1",
                "",
                "# 3. Устанавливаем зависимости и сам пакет orchestra:",
                "python -m pip install --upgrade pip",
                "pip install -r requirements-dev.txt -e .",
            ],
            styles,
            title="Windows 11 PowerShell",
        )
    )
    win_sample_path = r"C:\dev\orchestra"
    s.append(
        callout(
            "nuance",
            "Встроенные механизмы защиты под Windows (WIN-01..WIN-22)",
            "1) Если антивирус Windows Defender кратковременно блокирует файл при сохранении "
            f"карты проекта, атомарная запись автоматически выполняет до 5 повторов с задержкой. "
            "2) Зависшие дочерние процессы в песочнице убиваются всем деревом через нативные "
            f"{c_inline('Win32 Job Objects')}. "
            "3) Поскольку файловая система NTFS нечувствительна к регистру, движок патчей "
            f"заранее блокирует попытку создать {c_inline('src/Core.py')}, если уже существует "
            f"{c_inline('src/core.py')} ({c_inline('CaseCollisionError')}). "
            "4) Старайтесь размещать папку проекта ближе к корню диска (например, "
            f"{c_inline(win_sample_path)}), чтобы пути не превышали порог 240 символов.",
            styles,
        )
    )

    s.append(h2("2.3. Запуск в Docker Compose", styles))
    s.append(
        code_block(
            [
                "# Сначала создайте .env через python setup_env.py, затем поднимите контейнер:",
                "docker compose up --build -d",
                "",
                "# Просмотр логов и остановка:",
                "docker compose logs -f",
                "docker compose down",
            ],
            styles,
            title="Docker Compose (с пробросом папки ./runtime на хост)",
        )
    )

    # ==================== РАЗДЕЛ 3 ====================
    s.append(h1("03", "Интерактивная настройка через setup_env.py (правило пробела)", styles))
    s.append(
        Paragraph(
            "Чтобы вам не приходилось вручную редактировать конфигурационные файлы и следить "
            "за кавычками в JSON-массивах, в корне репозитория создан интерактивный скрипт "
            f"{c_inline('setup_env.py')} (также доступен командой {c_inline('python -m orchestra setup')}). "
            "Он сам находит созданный .venv, по очереди запрашивает нужные параметры, маскирует "
            "ключи на экране и проверяет итоговый файл .env через строгую схему Pydantic.",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "# На Linux / macOS:",
                "python3 setup_env.py",
                "",
                "# На Windows (или из активированного .venv):",
                "python setup_env.py",
            ],
            styles,
            title="Запуск мастера первичной настройки",
        )
    )

    s.append(h2("3.1. Главное правило ввода: как работает ПРОБЕЛ", styles))
    s.append(
        Paragraph(
            "На любом шаге мастера отправка одного пробела (« » + Enter) или просто пустого Enter "
            "означает: «оставить надёжное значение по умолчанию / пропустить необязательное поле». "
            "При вводе списка API-ключей программа запрашивает Ключ #1, Ключ #2, Ключ #3 и так далее "
            "до 20 слотов: как только вы ввели столько ключей, сколько хотите сейчас (хоть 1, хоть 3), "
            f"на следующем запросе просто нажмите {b_inline('Пробел + Enter')} — цикл ввода ключей "
            "мгновенно завершится, и все остальные пустые слоты прокликивать не нужно!",
            styles["Body"],
        )
    )

    wiz_steps = [
        [
            "[1/6] Base URL",
            "Адрес OpenAI-совместимого сервера. Пробел оставляет NVIDIA Build "
            "(https://integrate.api.nvidia.com/v1). Для OpenRouter укажите "
            "https://openrouter.ai/api/v1, для DeepSeek — https://api.deepseek.com/v1.",
        ],
        [
            "[2/6] Одна модель",
            "Точный строковый ID одной модели, доступной на вашем сервере (например, "
            "deepseek-ai/deepseek-v3.2 или nvidia/nemotron-3.5-lightning-30b-a3b). "
            "Именно эта модель будет вести Архитектора, разработчиков и ревью.",
        ],
        [
            "[3/6] API-ключи",
            "Поочерёдный ввод ключей (#1, #2, #3...). Дубликаты автоматически отбрасываются. "
            "На экран выводится только маска вида nvapi-...9x2A. Когда ввели все имеющиеся "
            "ключи — отправьте пробел. Если отправить пробел уже на Ключе #1, включится "
            "безопасный офлайн-режим provider=mock.",
        ],
        [
            "[4/6] Папка проектов",
            "Локальный путь хранения всех создаваемых проектов. Пробел оставляет ./runtime. "
            "Можно указать абсолютный путь на другом диске (например, D:/OrchestraData).",
        ],
        [
            "[5/6] Telegram-бот",
            "Необязательно. Отправьте пробел, если будете работать через терминал или браузер. "
            "Если введёте токен бота — мастер по очереди запросит ваши числовые Telegram User ID "
            "(завершение списка — тоже пробел).",
        ],
        [
            "[6/6] Web-пароль",
            "Необязательно. Если отправить пробел — дашборд привяжется только к вашему ПК "
            "(127.0.0.1:8000) без пароля. Если задать пароль — откроется доступ в локальной "
            "сети (0.0.0.0:8000) по Bearer-токену.",
        ],
    ]
    s.append(
        dark_table(
            ["Шаг мастера", "Что вводить и какие есть нюансы"],
            wiz_steps,
            [38, 140],
            styles,
            mono_cols={0},
        )
    )
    s.append(
        callout(
            "tip",
            "Повторный запуск мастера никогда не уничтожит старый .env бесследно",
            f"Если файл {c_inline('.env')} уже существует, перед записью новых настроек мастер "
            f"автоматически скопирует прежнюю версию в {c_inline('.env.bak')} (он тоже добавлен в "
            f"{c_inline('.gitignore')}) и выставит закрытые права доступа {c_inline('0600')} на Linux/macOS.",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 4 ====================
    s.append(h1("04", "Пул API-ключей, лимиты HTTP 429 и выбор LLM-модели", styles))
    s.append(
        Paragraph(
            "ORCHESTRA выполняет параллельную сборку независимых веток графа задач, поэтому "
            "в систему встроен промышленный менеджер ключей "
            f"{c_inline('KeyPoolManager')}. Понимание его нюансов позволит вам комфортно работать "
            "как с одним бесплатным ключом, так и с пулом из 5–20 ключей.",
            styles["Body"],
        )
    )

    s.append(h2("4.1. Нюанс работы с ОДНИМ или ДВУМЯ ключами", styles))
    s.append(
        Paragraph(
            "По умолчанию оркестратор разрешает до 6 параллельных агентов "
            f"({c_inline('MAX_CONCURRENT_AGENTS=6')}) и до 2 одновременных запросов на один ключ "
            f"({c_inline('MAX_CONCURRENT_PER_KEY=2')}). Если у вас пока только {b_inline('1 API-ключ')} "
            "с жёстким лимитом запросов в минуту (например, бесплатный тариф NVIDIA Build ~40 RPM), "
            "пул сам выстроит агентов в аккуратную очередь по 2 параллельных потока. Если же вы "
            "замечаете частые паузы из-за лимита RPM провайдера, добавьте в конец файла .env строку:",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "ORCHESTRA_KEY_POOL__MAX_CONCURRENT_PER_KEY=1",
                "ORCHESTRA_SWARM__MAX_CONCURRENT_AGENTS=2",
            ],
            styles,
            title="Рекомендуемая настройка в .env при работе всего с 1 ключом на бесплатном тарифе",
        )
    )

    s.append(h2("4.2. Как пул автоматически переживает ошибки 429 и сбои сети", styles))
    s.append(
        bullet(
            f"{b_inline('ACTIVE')} — ключ здоров и принимает задачи агентов.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('COOLING')} — сервер вернул {c_inline('HTTP 429 Too Many Requests')} или {c_inline('5xx')}. "
            "Ключ уходит на «остывание» ровно на время заголовка Retry-After (или 60 сек для 429 / "
            "15 сек для 5xx), а запрос агента без падения прозрачно перенаправляется на другой "
            "здоровый ключ пула (или ждёт окончания паузы, если ключ один).",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('DISABLED')} — сервер вернул {c_inline('401/403 AuthFailed')} (ключ отозван или неверный) "
            "либо произошло 5 сбоев подряд. Такой ключ исключается из ротации до перезапуска.",
            styles,
        )
    )

    s.append(h2("4.3. Нюансы выбора модели (DEFAULT_MODEL)", styles))
    s.append(
        callout(
            "warn",
            "Точное совпадение имени модели на сервере провайдера",
            "При старте боевой сборки оркестратор выполняет быстрый проверочный запрос "
            f"({c_inline('CapabilityProber')}: {c_inline('GET /models')} + микро-стрим на 8 токенов), "
            "чтобы убедиться, что выбранная модель реально существует у вашего провайдера, "
            "узнать её поле рассуждений (reasoning_content) и минимально допустимую температуру. "
            "Если вы скопировали имя модели с опечаткой — система сразу остановится с понятной "
            f"ошибкой {c_inline('ModelNotFoundError')}, сэкономив ваше время и токены.",
            styles,
        )
    )
    s.append(
        Paragraph(
            "Лучше всего для роя подходят модели с сильным структурным JSON-выводом и режимом "
            "рассуждений (thinking): линейки DeepSeek V3.2 / R1, Qwen 3 (235B / 32B), Nemotron Ultra / "
            "Lightning. Двухпотоковый маршрутизатор "
            f"{c_inline('DualStreamRouter')} физически отделяет поток мыслей от итогового кода — "
            "рассуждения модели никогда не попадут внутрь генерируемых .py файлов!",
            styles["Body"],
        )
    )

    # ==================== РАЗДЕЛ 5 ====================
    s.append(h1("05", "Самодиагностика (doctor) и офлайн-проверка (demo)", styles))
    s.append(
        Paragraph(
            "Перед первым боевым запуском обязательно выполните две быстрые проверочные команды. "
            "Они занимают меньше двух секунд и подтверждают, что Python, Git, песочница, кодировка "
            "UTF-8, переводы строк LF и все 32 встроенных скилла полностью исправны.",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "# 1. Полная диагностика платформы и конфигурации с вердиктом на русском:",
                ".venv/bin/python -m orchestra doctor",
                "",
                "# 2. Контрольная офлайн-сборка 34-узлового крипто-проекта без траты ключей:",
                ".venv/bin/python -m orchestra demo",
            ],
            styles,
            title="Предполётная проверка",
        )
    )
    s.append(
        Paragraph(
            "На что обратить внимание в таблице команды doctor: строка «Платформа (Git)» должна быть "
            "в статусе OK, строка «Пул API-ключей» после запуска setup_env.py должна показывать "
            "количество введённых вами ключей (> 0 для боевого режима), а «Реестр /skills/» — "
            "20 системных скиллов (плюс 12 крипто-скиллов подгружаются автоматически при сборке "
            "крипто-проектов).",
            styles["Body"],
        )
    )

    # ==================== РАЗДЕЛ 6 ====================
    s.append(h1("06", "Как правильно писать ТЗ и запускать сборку (orchestra run)", styles))
    s.append(
        Paragraph(
            "Команда запуска реальной разработки выглядит максимально просто и принимает либо "
            "текст технического задания в кавычках, либо путь к текстовому/Markdown-файлу с подробным ТЗ:",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "# Вариант А: Короткое ТЗ прямо в командной строке",
                '.venv/bin/python -m orchestra run "Напиши библиотеку расчёта комиссий и PnL на Decimal с pytest-тестами" --project-id pnl_lib',
                "",
                "# Вариант Б: Подробное ТЗ из подготовленного файла (РЕКОМЕНДУЕТСЯ для крупных задач!)",
                ".venv/bin/python -m orchestra run ./my_project_spec.md --project-id order_router_v1",
                "",
                "# Проверить статус всех узлов графа в любой момент:",
                ".venv/bin/python -m orchestra status --project-id order_router_v1",
            ],
            styles,
            title="Боевой запуск фабрики",
        )
    )

    s.append(h2("6.1. Как составить идеальное ТЗ: 6 практических правил", styles))
    s.append(
        bullet(
            f"{b_inline('1. Чётко называйте входы, выходы и типы данных')}: например, «функция "
            "принимает список сделок в JSON, считает средневзвешенную цену входа через Decimal "
            "и возвращает Pydantic-модель отчёта».",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('2. Сразу фиксируйте бизнес-константы и лимиты')}: агентам строжайше "
            "запрещено выдумывать тарифы, комиссии, тайм-ауты и лимиты риска «из головы». Если они "
            "важны — напишите их прямо в ТЗ, иначе Архитектор поставит узел на паузу и пришлёт вам вопрос.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('3. Соблюдайте модульность (атомы до 300 строк)')}: в ORCHESTRA действует "
            f"жёсткий закон {c_inline('MAX_CHUNK_LINES = 300')}. Если Судья увидит файл длиннее "
            "300 строк, он отклонит патч и заставит агента разбить код на чистые небольшие модули.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('4. Указывайте желаемый стек и ограничения')}: например, «использовать "
            "только стандартную библиотеку Python 3.11 + Pydantic v2, хранение в SQLite, без внешних API».",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('5. Давайте осмысленный --project-id латиницей без пробелов')}: например, "
            f"{c_inline('arb_scanner_01')}. Если запустить команду повторно с тем же "
            f"{c_inline('--project-id')}, система подхватит существующую папку в "
            f"{c_inline('runtime/projects/<id>/')}. Для нового проекта с нуля давайте новый ID!",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('6. Описывайте краевые сценарии')}: пустой ввод, отрицательные значения, "
            "дубликаты сообщений, обрыв связи — QA-агент превратит их в обязательные автотесты.",
            styles,
        )
    )

    s.append(h2("6.2. Что именно проверяет Независимый Судья на каждом узле", styles))
    s.append(
        Paragraph(
            "Агент-разработчик структурно лишён права пометить свой узел как DONE (попытка сделать "
            f"это вызывает исключение {c_inline('IndependentReviewViolationError')}). Он может лишь "
            "перевести узел в VERIFYING, после чего включается автоматический конвейер Судьи:",
            styles["Body"],
        )
    )
    s.append(
        bullet(
            f"{c_inline('PatchEngine')} проверяет, что агент изменил {b_inline('только файлы из своего списка owned_files')} "
            "(чужие файлы трогать запрещено!), нет коллизий регистра имён и размер каждого файла <= 300 строк.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{c_inline('ast.parse')} + {c_inline('ruff check')} проверяют синтаксическую чистоту и стандарты стиля.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{c_inline('pytest -q')} запускает тесты в изолированной песочнице без передачи ваших секретов из .env.",
            styles,
        )
    )
    s.append(
        bullet(
            "Для крипто-проектов дополнительно прогоняются все 13 AST-правил безопасности денег.",
            styles,
        )
    )
    s.append(
        bullet(
            "Если любая проверка упала — дефекты записываются в узел, и агент получает до 2 попыток "
            "точечного исправления. Если всё зелёное — делается атомарный git commit!",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 7 ====================
    s.append(h1("07", "Полный справочник всех команд CLI", styles))
    cli_rows = [
        [
            "python setup_env.py\norchestra setup",
            "Пошаговый интерактивный мастер создания .env (модель, ключи по очереди, пробел для пропуска).",
        ],
        [
            "orchestra doctor",
            "Полная диагностика ОС, UTF-8/LF, ResourceLimiter, Git, песочницы, провайдера, ключей и скиллов.",
        ],
        [
            "orchestra init",
            "Предварительная инициализация структуры ./runtime/ и манифеста встроенных скиллов.",
        ],
        [
            "orchestra demo",
            "Эталонный офлайн-прогон 34-узлового крипто-проекта (PAPER) на MockProvider без ключей.",
        ],
        [
            'orchestra run "<ТЗ>"\n  --project-id ID [--mock]',
            "Запуск полного цикла сборки проекта по тексту ТЗ или пути к файлу спецификации.",
        ],
        [
            "orchestra status\n  --project-id ID",
            "Таблица всех узлов MentalMap проекта: типы, текущие статусы, зависимости и расход токенов.",
        ],
        [
            "orchestra keys",
            "Публичная статистика пула ключей (ID-отпечаток, статус ACTIVE/COOLING, запросы, счётчик 429/5xx).",
        ],
        [
            "orchestra skills [--crypto]",
            "Список всех активных системных скиллов (с флагом --crypto добавляет и 12 крипто-скиллов).",
        ],
        [
            "orchestra talents",
            "Таблица накопленных профилей профессий: версия профиля, процент сдачи с 1-й попытки, число проектов.",
        ],
        [
            "orchestra serve\n  --project-id ID --port 8000",
            "Запуск локального HTTP + WebSocket сервера и офлайн Web-дашборда оператора.",
        ],
        [
            "orchestra version",
            "Вывод текущей версии установленного пакета ORCHESTRA.",
        ],
    ]
    s.append(
        dark_table(
            ["Команда (вызов через .venv/bin/python -m ...)", "Назначение и результат"],
            cli_rows,
            [62, 116],
            styles,
            mono_cols={0},
        )
    )

    # ==================== РАЗДЕЛ 8 ====================
    s.append(h1("08", "Веб-консоль оператора (serve) и ответы на эскалации", styles))
    s.append(
        Paragraph(
            "Веб-консоль оператора позволяет визуально наблюдать за графом узлов, читать живой "
            "поток рассуждений агентов, проверять состояние ключей и отвечать на вопросы Архитектора "
            "прямо в браузере. Все скрипты и стили лежат локально в "
            f"{c_inline('orchestra/web/')} — интерфейс не тянет ни одного внешнего CDN-скрипта "
            "и мгновенно открывается даже без интернета.",
            styles["Body"],
        )
    )
    s.append(
        code_block(
            [
                "# Безопасный локальный запуск только для вашего компьютера:",
                ".venv/bin/python -m orchestra serve --project-id order_router_v1 --host 127.0.0.1 --port 8000",
                "",
                "# Затем откройте в браузере: http://127.0.0.1:8000",
            ],
            styles,
            title="Запуск Web-дашборда",
        )
    )

    s.append(h2("8.1. Как работают неблокирующие эскалации (BLOCKED_HUMAN)", styles))
    s.append(
        Paragraph(
            "В ORCHESTRA трёхуровневая система принятия решений (L1 -> L2 -> L3): если ответ есть "
            "в карте или дневнике (L1) — агент берёт его молча; если вопрос чисто технический (L2, "
            "например выбор структуры индекса) — Архитектор решает его сам в один проход и пишет ADR; "
            "и только если вопрос касается ваших денег, бизнес-логики или исчерпаны все попытки "
            "исправления узла (L3) — узел переходит в статус "
            f"{c_inline('BLOCKED_HUMAN')}.",
            styles["Body"],
        )
    )
    s.append(
        callout(
            "nuance",
            "Нулевой простой соседних веток (Non-Blocking Escalation)",
            "Пока один спорный узел ждёт вашего ответа в статусе BLOCKED_HUMAN, "
            f"{b_inline('все остальные независимые ветви проекта продолжают собираться и тестироваться')}! "
            "Ответить на открытый вопрос можно в панели эскалаций Web-консоли, в Telegram или "
            f"POST-запросом на {c_inline('/api/v1/questions/{question_id}/answer')}.",
            styles,
        )
    )
    s.append(
        callout(
            "danger",
            "Доступ к Web-консоли по сети (0.0.0.0)",
            "Если вы запускаете сервер на внешнем интерфейсе (--host 0.0.0.0), обязательно задайте "
            f"надёжный пароль в {c_inline('ORCHESTRA_WEB__ACCESS_TOKEN')}. Тогда все эндпоинты "
            f"{c_inline('/api/v1/*')} и WebSocket {c_inline('/ws?token=...')} будут требовать авторизацию, "
            "при этом сырые API-ключи никогда не возвращаются сервером даже авторизованному оператору!",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 9 ====================
    s.append(h1("09", "Telegram-бридж оператора: настройка и нюансы безопасности", styles))
    s.append(
        Paragraph(
            "Telegram-мост удобен, когда вы запускаете крупную сборку и хотите получать краткие "
            "русскоязычные сводки (SITREP) с кнопками выбора вариантов прямо на телефон.",
            styles["Body"],
        )
    )
    s.append(
        bullet(
            f"{b_inline('Шаг 1')}: создайте нового бота через официального {c_inline('@BotFather')} "
            "и скопируйте выданный токен вида 123456789:AAH...",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('Шаг 2')}: узнайте свой числовой Telegram User ID (это только цифры, например "
            "482910422, а НЕ @username!).",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('Шаг 3')}: запустите {c_inline('python setup_env.py')} и укажите токен и свой User ID на шаге [5/6].",
            styles,
        )
    )
    s.append(
        callout(
            "danger",
            "Жёсткий режим Fail-Closed в Telegram",
            f"Если список {c_inline('ORCHESTRA_TELEGRAM__ALLOWED_USER_IDS')} пуст ({c_inline('[]')}), "
            "мост автоматически отвергает сообщения абсолютно от всех пользователей интернета, включая "
            "вас самих! Это сделано специально, чтобы никто посторонний не мог управлять вашей фабрикой, "
            "угадав имя бота. Таймаут ожидания вашего ответа по умолчанию равен 3600 сек (1 час), "
            f"после чего применяется безопасное действие {c_inline('pause_node')} (пауза узла без самодеятельности).",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 10 ====================
    s.append(h1("10", "Накопление опыта: Скиллы (/skills/) и Таланты (/agent_talents/)", styles))
    s.append(
        Paragraph(
            "Главное отличие ORCHESTRA от одноразовых генераторов кода — двухконтурная долговременная "
            "память, которая сохраняется на вашем диске между проектами:",
            styles["Body"],
        )
    )
    s.append(h2("10.1. Реестр исполняемых скиллов (/skills/) и Zero Token Waste", styles))
    s.append(
        Paragraph(
            "В поставку входят 20 системных скиллов (чтение AST без мусора, точечное наложение патчей, "
            "проверка реального существования API-символов против галлюцинаций, поиск по документации BM25, "
            "анализ мёртвого кода, сканер утечек секретов, атомарный git-коммит и др.) плюс 12 доменных "
            "крипто-скиллов. Агенты видят в своём промпте только крошечный индекс имён и сигнатур "
            "(до 600 токенов) — исходный код скиллов никогда не забивает контекстное окно модели! "
            "Сам скилл запускается оркестратором локально в отдельном подпроцессе с очищенным окружением.",
            styles["Body"],
        )
    )
    s.append(
        Paragraph(
            "Любое обновление скилла проходит через "
            f"{c_inline('SkillMutationGate')} и статический сканер безопасности {c_inline('security_scan.py')} "
            "(запрещены eval, exec, shell=True, pickle, прямые сокеты). Если регрессионный тест падает — "
            "изменение отвергается или скилл изолируется.",
            styles["Body"],
        )
    )

    s.append(h2("10.2. Профили профессий и Empirical Proof Gate дневников", styles))
    s.append(
        Paragraph(
            "После завершения каждой сборки модуль "
            f"{c_inline('PostMortemAnalyzer')} анализирует реальные ретраи и ошибки проекта, обновляя "
            "профили ролей в runtime/agent_talents/<role>/. В дневник открытий "
            f"({c_inline('DISCOVERY_DIARY.md')}) со статусом {c_inline('VERIFIED')} попадают только те "
            "уроки, которые имеют эмпирическое доказательство из песочницы (команда, exit_code == 0, "
            "SHA-256 артефакта). Сырые догадки (HYPOTHESIS) хранятся для истории, но никогда не "
            "подмешиваются в системные промпты будущих проектов.",
            styles["Body"],
        )
    )

    # ==================== РАЗДЕЛ 11 ====================
    s.append(h1("11", "Крипто-домен: мультибиржевая база, Decimal и защита LIVE-режима", styles))
    s.append(
        Paragraph(
            "В ORCHESTRA встроен специализированный доменный пакет "
            f"{c_inline('CryptoTradingDomainPack')} для построения мультибиржевых торговых платформ "
            "и исследовательских бэктестеров. Он включается автоматически на стадии S1, как только "
            "в вашем ТЗ встречаются минимум 2 профильных термина (например: crypto, binance, bybit, "
            "okx, orderbook, ohlcv, funding rate, backtest, slippage, крипто, биржа, фьючерсы, свечи, ордер).",
            styles["Body"],
        )
    )

    s.append(h2("11.1. 13 жёстких AST-правил, которые Судья проверяет механически", styles))
    crypto_rules = [
        [
            "NO_FLOAT_MONEY",
            "Полный запрет типа float для цен, объёмов, балансов, комиссий и PnL — только Decimal из строк.",
        ],
        [
            "NO_NAIVE_DATETIME",
            "Запрет datetime.now() без часового пояса и utcnow() — всё время строго в UTC.",
        ],
        [
            "NO_WALLCLOCK_IN_STRATEGY",
            "Стратегии запрещено читать системные часы ПК (только время биржевого события — для единства с бэктестом).",
        ],
        [
            "NO_DIRECT_EXCHANGE_IMPORT",
            "Стратегия не имеет права импортировать ccxt напрямую — работа только через адаптер шлюза.",
        ],
        [
            "NO_LOOKAHEAD",
            "Запрет заглядывания в будущее (например, .shift(-1)) в расчёте сигналов и бэктесте.",
        ],
        [
            "MANDATORY_CLIENT_ORDER_ID",
            "Каждая отправка ордера обязана содержать уникальный идемпотентный client_order_id.",
        ],
        [
            "ORDER_AUDIT_REQUIRED",
            "Обязательная запись намерения в append-only журнал аудита до сетевой отправки.",
        ],
        [
            "NO_UNBOUNDED_AVERAGING",
            "Запрет бесконтрольного мартингейла и усреднения без явного риск-лимита.",
        ],
        [
            "DECIMAL_JSON_SAFETY",
            "Запрет сериализации Decimal через float в JSON (только строковое представление).",
        ],
        [
            "SECRET_NOT_LITERAL",
            "Запрет хардкода api_key / api_secret строковыми литералами в исходном коде.",
        ],
        [
            "LIVE_MODE_GUARD",
            "Блокировка обращения к TradingMode.LIVE без явного флага подтверждения человека.",
        ],
        [
            "NO_EXCHANGE_LOGIC_OUTSIDE_ADAPTER",
            "Запрет утечки специфичных заголовков конкретной биржи за пределы её адаптера.",
        ],
        [
            "NO_FLOAT_CROSS_EXCHANGE",
            "Запрет float-арифметики при кросс-биржевом сведении спредов и позиций.",
        ],
    ]
    s.append(
        dark_table(
            ["Код правила AST", "От какой финансовой ошибки защищает"],
            crypto_rules,
            [68, 110],
            styles,
            mono_cols={0},
        )
    )

    s.append(
        callout(
            "danger",
            "Правила работы с биржевыми ключами и режимом LIVE (прочитайте обязательно!)",
            "1) Режим по умолчанию во всех генерируемых торговых проектах — строго "
            f"{b_inline('PAPER')} (бумажная симуляция без отправки реальных ордеров), а список активных "
            f"бирж в .env пуст ({c_inline('ORCHESTRA_EXCHANGES__ENABLED=[]')}).<br/>"
            "2) Скилл подписи запросов "
            f"{c_inline('hmac_sign_request')} принимает {b_inline('только имя переменной окружения')} "
            f"({c_inline('secret_ref')}), но не сам секрет — биржевой ключ никогда не проходит через LLM.<br/>"
            "3) Когда вы в будущем перейдёте к проверке созданного робота на реальной бирже: сначала "
            "прогоните его минимум на TESTNET / SHADOW, создавайте биржевые ключи "
            f"{b_inline('СТРОГО БЕЗ ПРАВА ВЫВОДА СРЕДСТВ (Withdraw = OFF)')} и с привязкой к вашему IP. "
            "Программное обеспечение предоставляется в инженерных и образовательных целях и не является "
            "инвестиционной рекомендацией.",
            styles,
        )
    )

    s.append(h2("11.2. Настройка списка бирж в exchanges.yaml", styles))
    s.append(
        Paragraph(
            f"В корне проекта лежит мультибиржевой реестр {c_inline('exchanges.yaml')} с профилями "
            "возможностей (точность тиков, веса лимитеров, интервалы сверки и пороги рассинхрона часов). "
            "У каждой биржи свой изолированный лимитер запросов и независимый аварийный рубильник "
            f"({c_inline('KillSwitch')}): перегрузка одной биржи не роняет соседние.",
            styles["Body"],
        )
    )

    # ==================== РАЗДЕЛ 12 ====================
    s.append(h1("12", "Бюджеты токенов, тайм-ауты и автовосстановление после сбоев", styles))
    s.append(
        bullet(
            f"{b_inline('Бюджет контекста 20 / 30 / 50')}: каждый промпт собирается с коэффициентом "
            "запаса 0.85 от окна модели: 20% отводится на устав роли и проверенные уроки дневника, "
            "30% — на локальную суб-карту узла и замороженные контракты соседей, 50% — на индекс "
            "скиллов, рабочие диффы и скользящее окно последних 6 шагов (более старые шаги сжимаются "
            "в компактные дайджесты, поэтому агент не переполняет память на длинных задачах).",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('Ограничители расходов')}: на один узел по умолчанию выделяется до "
            f"{c_inline('200 000')} токенов ({c_inline('ORCHESTRA_BUDGETS__NODE_TOKEN_LIMIT')}). Вы также "
            "можете задать жёсткий потолок токенов на весь проект целиком через "
            f"{c_inline('ORCHESTRA_BUDGETS__PROJECT_TOKEN_LIMIT')} и лимит времени в секундах через "
            f"{c_inline('ORCHESTRA_BUDGETS__PROJECT_WALLCLOCK_S')}.",
            styles,
        )
    )
    s.append(
        bullet(
            f"{b_inline('Корректная остановка (Ctrl+C) и Crash Recovery')}: при нажатии Ctrl+C "
            f"{c_inline('GracefulShutdownCoordinator')} даёт активным задачам до 30 секунд аккуратно "
            "сохранить контрольную точку. А если компьютер внезапно выключился по питанию, при следующем "
            f"старте {c_inline('StateStore')} проверит SHA-256 чексумму {c_inline('mental_map.json')}, при "
            "повреждении возьмёт последний целый .bak-снапшот, доиграет хвост событий из "
            f"{c_inline('events.jsonl')}, а {c_inline('CheckpointManager')} вернёт зависшие узлы обратно в "
            "очередь READY — все уже готовые узлы DONE сохранятся!",
            styles,
        )
    )

    # ==================== РАЗДЕЛ 13 ====================
    s.append(h1("13", "Справочник переменных .env и решение типовых проблем (FAQ)", styles))
    s.append(h2("13.1. Ключевые параметры файла .env", styles))
    env_rows = [
        [
            "ORCHESTRA_PROFILE",
            "dev",
            "Профиль окружения: dev | ci | prod (в prod маскирование секретов принудительно неотключаемо).",
        ],
        [
            "ORCHESTRA_RUNTIME_DIR",
            "./runtime",
            "Локальная папка для всех проектов, логов, кэша, талантов и скиллов.",
        ],
        [
            "ORCHESTRA_LLM__PROVIDER",
            "openai_compat",
            "Боевой режим openai_compat или офлайн-режим mock без ключей.",
        ],
        [
            "ORCHESTRA_LLM__BASE_URL",
            "https://integrate.api.nvidia.com/v1",
            "Базовый URL вашего OpenAI-совместимого провайдера.",
        ],
        [
            "ORCHESTRA_LLM__DEFAULT_MODEL",
            "nvidia/nemotron-3.5-...",
            "Точный ID одной рабочей модели LLM.",
        ],
        [
            "ORCHESTRA_KEY_POOL__KEYS",
            '["ключ1","ключ2"]',
            "JSON-массив ваших API-ключей (автоматически заполняется setup_env.py).",
        ],
        [
            "ORCHESTRA_KEY_POOL__MAX_CONCURRENT_PER_KEY",
            "2",
            "Максимум параллельных запросов через один ключ (поставьте 1 при жёстком RPM).",
        ],
        [
            "ORCHESTRA_SWARM__MAX_CONCURRENT_AGENTS",
            "6",
            "Максимум одновременно работающих агентов роя.",
        ],
        [
            "ORCHESTRA_SANDBOX__MODE",
            "subprocess",
            "Режим песочницы: subprocess (быстро) или docker (полная контейнеризация).",
        ],
        [
            "ORCHESTRA_TELEGRAM__ENABLED",
            "false",
            "Включение Telegram-бриджа (требует BOT_TOKEN и непустой ALLOWED_USER_IDS).",
        ],
        [
            "ORCHESTRA_WEB__HOST / PORT",
            "127.0.0.1 / 8000",
            "Адрес и порт локальной веб-консоли оператора.",
        ],
    ]
    s.append(
        dark_table(
            ["Переменная .env", "По умолчанию", "Описание и рекомендации"],
            env_rows,
            [70, 36, 72],
            styles,
            mono_cols={0, 1},
        )
    )
    s.append(Spacer(1, 5))

    s.append(h2("13.2. Быстрое решение частых проблем (Troubleshooting)", styles))
    faq_rows = [
        [
            "ConfigurationError: пул API-ключей пуст",
            "Вы запустили боевой orchestra run без флага --mock, но не добавили ни одного ключа.",
            "Запустите python setup_env.py и введите хотя бы 1 API-ключ (или добавьте флаг --mock для теста).",
        ],
        [
            "ModelNotFoundError при старте",
            "Указанная в DEFAULT_MODEL модель отсутствует на выбранном вами BASE_URL.",
            "Проверьте точное имя модели в личном кабинете провайдера и перезапустите python setup_env.py.",
        ],
        [
            "Частые переходы ключа в COOLING (429)",
            "Превышен минутный лимит запросов (RPM) на одном бесплатном ключе.",
            f"Уменьшите в .env:<br/>{c_inline('MAX_CONCURRENT_PER_KEY=1')} и "
            f"{c_inline('MAX_CONCURRENT_AGENTS=2')} либо добавьте 2-й ключ.",
        ],
        [
            "Узел остановился в BLOCKED_HUMAN",
            "В ТЗ не хватает важного бизнес-правила либо узел исчерпал 2 попытки авто-починки.",
            "Откройте Web-дашборд (orchestra serve) или статус узла, посмотрите вопрос/дефекты и дайте ответ.",
        ],
        [
            "Судья отклоняет код: File has > 300 lines",
            "Сработало правило атомарности кода MAX_CHUNK_LINES = 300.",
            "Штатный процесс: на следующем ретрае агент сам разобьёт крупный модуль на компактные файлы.",
        ],
        [
            "Telegram-бот молчит и не отвечает",
            "Ваш числовой ID не внесён в ALLOWED_USER_IDS (сработала защита fail-closed).",
            "Укажите свой цифровой Telegram User ID в шаге [5/6] setup_env.py и перезапустите процесс.",
        ],
        [
            "Как проверить, что я случайно не сохранил секрет в коде?",
            "Перед публикацией любых правок нужно убедиться в отсутствии ключей в файлах и коммитах.",
            "Запустите встроенный сканер: .venv/bin/python scripts/check_secrets_leak.py . (должно быть 0 findings).",
        ],
    ]
    s.append(
        dark_table(
            ["Симптом / сообщение", "Точная причина", "Что сделать оператору"],
            faq_rows,
            [48, 58, 72],
            styles,
        )
    )

    s.append(Spacer(1, 6))
    s.append(
        callout(
            "tip",
            "Шпаргалка полного рабочего цикла в 4 команды",
            f"1) {c_inline('make install')}  — один раз развернуть окружение.<br/>"
            f"2) {c_inline('python setup_env.py')}  — ввести модель и нужное число ключей (остальное — пробел).<br/>"
            f"3) {c_inline('.venv/bin/python -m orchestra doctor')}  — убедиться, что все системы зелёные.<br/>"
            f"4) {c_inline('.venv/bin/python -m orchestra run spec.md --project-id my_app')}  — собрать готовый "
            f"проверенный проект в {c_inline('./runtime/projects/my_app/workspace/')}!",
            styles,
        )
    )
    return s


def generate_pdf(output_path: Path) -> None:
    """Сгенерировать итоговый PDF-файл в тёмной теме."""
    register_fonts()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = build_styles()
    doc = DarkManualDocTemplate(str(output_path))
    story = build_story(styles)
    doc.build(story, canvasmaker=NumberedDarkCanvas)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "ORCHESTRA_USER_GUIDE_RU_DARK.pdf"
    generate_pdf(target)
    print(f"Generated dark-theme PDF manual: {target} ({target.stat().st_size} bytes)")
