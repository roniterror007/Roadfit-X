"""Readable review copy of the standalone manuscript; not an ACM typeset PDF."""
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT/'output/pdf'
    out.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont('Review', 'C:/Windows/Fonts/arial.ttf'))
    pdfmetrics.registerFont(TTFont('ReviewBold', 'C:/Windows/Fonts/arialbd.ttf'))
    pdfmetrics.registerFontFamily('Review', normal='Review', bold='ReviewBold')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BodyReview', fontName='Review', fontSize=10.2, leading=14.5,
        spaceAfter=8, alignment=TA_LEFT, splitLongWords=True))
    styles.add(ParagraphStyle(name='SectionReview', fontName='ReviewBold', fontSize=15, leading=19,
        textColor=colors.HexColor('#0f766e'), spaceBefore=16, spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name='SubReview', fontName='ReviewBold', fontSize=11.5, leading=15,
        spaceBefore=10, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle(name='CaptionReview', fontName='Review', fontSize=9, leading=12,
        textColor=colors.HexColor('#475569'), spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name='ReferenceReview', fontName='Review', fontSize=8.5, leading=12,
        spaceAfter=7, splitLongWords=True))
    source = (ROOT/'research/roadfit_paper.tex').read_text(encoding='utf-8')
    refs = re.findall(r'\\bibitem\{([^}]+)\}\s*(.*?)(?=\\bibitem|\\end\{thebibliography\})', source, re.S)
    numbers = {key: str(i+1) for i, (key, _) in enumerate(refs)}
    def plain(text):
        text = re.sub(r'\\cite\{([^}]+)\}', lambda m: '['+', '.join(numbers[x] for x in m[1].split(','))+']', text)
        text = re.sub(r'\\ref\{([^}]+)\}', lambda m: {'tab:regions':'1', 'tab:contrasts':'2', 'tab:eta':'3'}.get(m[1], m[1]), text)
        text = re.sub(r'\\(?:texttt|emph|textbf|mathrm|rm)\{([^}]+)\}', r'\1', text)
        text = re.sub(r'\\url\{([^}]+)\}', r'\1', text)
        commands = {'Delta':'Delta', 'mu':'mu', 'ell':'ell', 'log':'log', 'min':'min', 'max':'max',
                    'in':' in ', 'sum':'sum', 'pm':'+/-', 'times':'x', 'left':'', 'right':'', 'mathrm':''}
        for key, value in commands.items():
            text = re.sub(r'\\'+key+r'\b', lambda _: value, text)
        text = text.replace('\\%', '%').replace('\\_', '_').replace('$', '').replace('\\ ', ' ')
        text = text.replace('``', '"').replace("''", '"').replace('--', '-').replace('{', '').replace('}', '')
        text = re.sub(r'\\([A-Za-z]+)', r'\1', text)
        return re.sub(r'\s+', ' ', text).strip()
    def paragraph(text, style='BodyReview'):
        return Paragraph(html.escape(plain(text)), styles[style])
    title = re.search(r'\\title\{([^}]+)\}', source)[1]
    story = [Paragraph('ROADFIT-X / RESEARCH REVIEW', styles['CaptionReview']),
             Paragraph(html.escape(title), ParagraphStyle(name='CoverTitle', fontName='ReviewBold', fontSize=23,
                       leading=28, textColor=colors.HexColor('#0f172a'), spaceAfter=12)),
             paragraph('1 October 2026. Internal review draft. This readable copy was generated separately because the native LaTeX compiler reported a Windows runtime error. It is not the ACM typeset submission PDF.'),
             Paragraph('Abstract', styles['SubReview'])]
    abstract = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', source, re.S)[1]
    story.append(paragraph(abstract))
    body = source.split('\\maketitle', 1)[1].split('\\begin{thebibliography}', 1)[0]
    tokens = re.split(r'(\\section\{[^}]+\}|\\subsection\{[^}]+\}|\\begin\{table\}.*?\\end\{table\}|\\begin\{figure\}.*?\\end\{figure\}|\\begin\{equation\}.*?\\end\{equation\})', body, flags=re.S)
    section = table_number = equation_number = 0
    equations = [
        'mu[e,b] = (capacity[e] / 3600) x max(0.05, 1 - background_flow[e,b] / capacity[e])',
        'Q[e,b+1] = max(0, Q[e,b] + reserved_PCU[e,b] - mu[e,b] x Delta)',
        'q[e](t) = max(0, Q[e,b] + (reserved_PCU[e,b] / Delta - mu[e,b]) x (t - b x Delta))',
        'J(path) = ETA(path) + 0.5 x sum(queue / service_rate) + 20 x vehicle_PCU x sum(local_km x (1 + density))',
        'E(P) = -sum( (C_bar/C_e) x (f_e/N) x log(f_e/N) ), N = sum(f_e)']
    for token in tokens:
        if not token.strip():
            continue
        if token.startswith('\\section'):
            section += 1
            story.append(Paragraph(f'{section}. '+html.escape(re.search(r'\{(.*?)\}', token)[1]), styles['SectionReview']))
        elif token.startswith('\\subsection'):
            story.append(Paragraph(html.escape(re.search(r'\{(.*?)\}', token)[1]), styles['SubReview']))
        elif token.startswith('\\begin{equation}'):
            story.append(paragraph(f'Equation {equation_number+1}: '+equations[equation_number])); equation_number += 1
        elif token.startswith('\\begin{table}'):
            table_number += 1
            caption = re.search(r'\\caption\{([^}]+)\}', token)[1]
            rows_text = re.search(r'\\begin\{tabular\}\{[^}]+\}(.*?)\\end\{tabular\}', token, re.S)[1]
            rows_text = re.sub(r'\\(?:toprule|midrule|bottomrule)', '', rows_text)
            rows = [[paragraph(cell, 'ReferenceReview') for cell in row.split('&')] for row in rows_text.split('\\\\') if row.strip()]
            widths = [205, 125, 150] if table_number != 3 else [230, 80, 80, 90]
            table = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e2f1ef')),
                ('LINEBELOW', (0,0), (-1,0), .7, colors.HexColor('#0f766e')),
                ('LINEBELOW', (0,-1), (-1,-1), .5, colors.HexColor('#cbd5e1')),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
                ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 7),
                ('RIGHTPADDING', (0,0), (-1,-1), 7), ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
            story.append(KeepTogether([paragraph(f'Table {table_number}. '+caption, 'CaptionReview'), table, Spacer(1, 10)]))
        elif token.startswith('\\begin{figure}'):
            story.append(Image(str(ROOT/'research/results.png'), width=480, height=190.2))
            story.append(paragraph('Figure 1. Recomputed TNTP seed-cluster contrasts and recorded Chengdu prediction feature ablations. These figures accompany the review copy; exportable SVG/PDF versions are in the research directory.', 'CaptionReview'))
        else:
            for block in token.strip().split('\n\n'):
                if block.strip():
                    story.append(paragraph(block))
    story.append(Image(str(ROOT/'research/locality.png'), width=450, height=252))
    story.append(paragraph('Figure 2. Local-street exposure in the restricted truck main-road cohort. Traffic and vehicle limits are simulated; footprint proxies come from OSM.', 'CaptionReview'))
    story.append(Paragraph('References', styles['SectionReview']))
    for i, (_, text) in enumerate(refs):
        story.append(paragraph(f'[{i+1}] '+text, 'ReferenceReview'))
    appendix = source.split('\\section{Artifact entry points}', 1)[1].split('\\end{document}', 1)[0]
    story.append(Paragraph('Appendix. Artifact entry points', styles['SectionReview']))
    story.append(paragraph(appendix))
    def footer(canvas, document):
        canvas.saveState(); canvas.setStrokeColor(colors.HexColor('#cbd5e1'))
        canvas.line(52, 42, A4[0]-52, 42); canvas.setFont('Review', 8)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(52, 29, 'RoadFit-X | internal review copy | simulation evidence is not field validation')
        canvas.drawRightString(A4[0]-52, 29, str(document.page)); canvas.restoreState()
    path = out/'roadfit_review.pdf'
    document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=52, rightMargin=52,
                                  topMargin=48, bottomMargin=57, title=title, author='Research review draft')
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    reader = PdfReader(path)
    text = '\n'.join(page.extract_text() for page in reader.pages)
    for expected in ['22.45', '750,484', '20.65', 'References', 'Missing']:
        if expected.lower() not in text.lower():
            raise ValueError(f'Missing review text: {expected}')
    (out/'review_text.txt').write_text(text, encoding='utf-8')
    print(f'Review PDF generated: {len(reader.pages)} pages. Native LaTeX compilation remains unverified.')


if __name__ == '__main__':
    main()
