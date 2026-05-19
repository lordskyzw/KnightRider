"""
Build FINDINGS.md as PDF using fpdf2.
Run: python discovery_track/build_findings_pdf.py

Generates: discovery_track/FINDINGS.pdf
"""
from fpdf import FPDF
import os
import re


class FindingsPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font('Helvetica', 'I', 8)
            self.cell(0, 5, 'Discovery Track: GP Feature Evolution Findings', align='C')
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', align='C')


def sanitize(text):
    """Replace Unicode characters that core Helvetica can't render."""
    replacements = {
        '\u2014': '--',   # em dash
        '\u2013': '-',    # en dash
        '\u2018': "'",    # left single quote
        '\u2019': "'",    # right single quote
        '\u201c': '"',    # left double quote
        '\u201d': '"',    # right double quote
        '\u2026': '...',  # ellipsis
        '\u2022': '-',    # bullet
        '\u2192': '->',   # right arrow
        '\u2264': '<=',   # less-equal
        '\u2265': '>=',   # greater-equal
        '\u03b8': 'theta',
        '\u03c9': 'omega',
        '\u03b1': 'alpha',
        '\u03c1': 'rho',
        '\u03c3': 'sigma',
        '\u03bb': 'lambda',
        '\u2248': '~',    # approximately
        '\u2260': '!=',   # not equal
        '\u221a': 'sqrt', # square root
        '\u00d7': 'x',    # multiplication sign
        '\u2713': '[PASS]',  # checkmark
        '\u2717': '[FAIL]',  # cross mark
        '\u2713': '[PASS]',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Fallback: replace any remaining non-latin1 chars
    result = []
    for ch in text:
        try:
            ch.encode('latin-1')
            result.append(ch)
        except UnicodeEncodeError:
            result.append('?')
    return ''.join(result)


def parse_and_render(pdf, md_path):
    """Parse FINDINGS.md and render to PDF."""
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    # Sanitize all lines upfront
    lines = [sanitize(l) for l in lines]

    W = pdf.w - pdf.l_margin - pdf.r_margin
    in_code = False
    in_table = False
    table_rows = []
    code_block = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip('\n')

        # Code block toggle
        if line.strip().startswith('```'):
            if in_code:
                # End code block - render it
                pdf.set_font('Courier', '', 7)
                pdf.set_fill_color(248, 248, 248)
                for cl in code_block:
                    cl = cl if cl.strip() else ' '
                    if len(cl) > 90:
                        cl = cl[:87] + '...'
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(0, 3.5, cl, fill=True)
                pdf.ln(2)
                code_block = []
                in_code = False
            else:
                # Flush any pending table
                if in_table:
                    _render_table(pdf, table_rows, W)
                    table_rows = []
                    in_table = False
                in_code = True
            i += 1
            continue

        if in_code:
            code_block.append(line)
            i += 1
            continue

        # Table rows
        if line.strip().startswith('|') and '|' in line.strip()[1:]:
            # Check if separator row
            stripped = line.strip()
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                i += 1
                continue
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(cells)
            i += 1
            continue
        else:
            if in_table:
                _render_table(pdf, table_rows, W)
                table_rows = []
                in_table = False

        # Headings
        if line.startswith('# ') and not line.startswith('##'):
            # Title
            pdf.set_font('Helvetica', 'B', 16)
            pdf.multi_cell(0, 9, line[2:].strip())
            pdf.ln(4)
            i += 1
            continue

        if line.startswith('## '):
            if pdf.get_y() > pdf.h - pdf.b_margin - 15:
                pdf.add_page()
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_fill_color(230, 230, 250)
            pdf.cell(0, 9, line[3:].strip(), fill=True, new_x='LMARGIN', new_y='NEXT')
            pdf.ln(3)
            i += 1
            continue

        if line.startswith('### '):
            if pdf.get_y() > pdf.h - pdf.b_margin - 12:
                pdf.add_page()
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(0, 7, line[4:].strip(), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(2)
            i += 1
            continue

        # Horizontal rule
        if line.strip() == '---':
            pdf.ln(3)
            i += 1
            continue

        # Empty line
        if line.strip() == '':
            pdf.ln(2)
            i += 1
            continue

        # Indented code/math (4 spaces)
        if line.startswith('    ') and not line.strip().startswith('-') and not line.strip().startswith('*'):
            # Collect consecutive indented lines
            math_lines = []
            while i < len(lines) and (lines[i].startswith('    ') or lines[i].strip() == ''):
                if lines[i].strip() == '':
                    math_lines.append('')
                else:
                    math_lines.append(lines[i].rstrip('\n'))
                i += 1
            # Trim trailing empties
            while math_lines and math_lines[-1] == '':
                math_lines.pop()
            if math_lines:
                pdf.set_font('Courier', '', 7)
                pdf.set_fill_color(248, 248, 248)
                pdf.set_x(pdf.l_margin)
                for ml in math_lines:
                    # Truncate overly long lines and handle empty
                    ml = ml.strip() if ml.strip() else ' '
                    if len(ml) > 90:
                        ml = ml[:87] + '...'
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(0, 3.5, ml, fill=True)
                pdf.ln(2)
            continue

        # Bold text lines (standalone **text**)
        # Regular paragraph text
        # Collect consecutive non-empty, non-special lines
        para_lines = []
        while i < len(lines):
            cl = lines[i].rstrip('\n')
            if (cl.strip() == '' or cl.startswith('#') or cl.startswith('```')
                    or cl.startswith('---') or (cl.strip().startswith('|') and '|' in cl.strip()[1:])
                    or (cl.startswith('    ') and not cl.strip().startswith('-') and not cl.strip().startswith('*'))):
                break
            para_lines.append(cl.strip())
            i += 1

        if para_lines:
            text = ' '.join(para_lines)
            # Render with basic bold handling
            _render_paragraph(pdf, text)
            pdf.ln(2)
            continue

        i += 1

    # Flush remaining table
    if in_table:
        _render_table(pdf, table_rows, W)


def _render_paragraph(pdf, text):
    """Render a paragraph, handling **bold** markers simply."""
    # Strip markdown bold for PDF - just render as regular text
    # Replace **text** with text (fpdf2 doesn't easily do inline bold)
    clean = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Handle bullet points
    if clean.startswith('- ') or clean.startswith('* '):
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(5, 5, '-')
        pdf.multi_cell(0, 5, clean[2:])
    else:
        pdf.set_font('Helvetica', '', 9)
        pdf.multi_cell(0, 5, clean)


def _render_table(pdf, rows, W):
    """Render a markdown table."""
    if not rows:
        return
    n_cols = len(rows[0])
    if n_cols == 0:
        return

    # Calculate column widths proportionally
    col_widths = []
    for col_i in range(n_cols):
        max_len = max(len(r[col_i]) if col_i < len(r) else 0 for r in rows)
        col_widths.append(max(max_len, 5))

    total = sum(col_widths)
    col_widths = [w / total * min(W, 180) for w in col_widths]

    pdf.set_font('Helvetica', '', 8)

    for row_i, row in enumerate(rows):
        if pdf.get_y() > pdf.h - pdf.b_margin - 10:
            pdf.add_page()
        if row_i == 0:
            pdf.set_font('Helvetica', 'B', 8)
            pdf.set_fill_color(235, 235, 245)
        else:
            pdf.set_font('Helvetica', '', 8)

        for col_i, cell in enumerate(row):
            if col_i < len(col_widths):
                cw = col_widths[col_i]
            else:
                cw = 20
            # Clean markdown
            cell_clean = re.sub(r'\*\*(.+?)\*\*', r'\1', cell)
            pdf.cell(cw, 5.5, cell_clean[:40], border=1,
                     fill=(row_i == 0), align='C' if col_i > 0 else 'L')
        pdf.ln()

    pdf.ln(2)


def build():
    pdf = FindingsPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Find FINDINGS.md
    script_dir = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(script_dir, 'FINDINGS.md')

    if not os.path.exists(md_path):
        print(f"ERROR: {md_path} not found")
        return None

    parse_and_render(pdf, md_path)

    out_path = os.path.join(script_dir, 'FINDINGS.pdf')
    pdf.output(out_path)
    print(f"PDF generated: {out_path}")
    print(f"Pages: {pdf.page_no()}")
    return out_path


if __name__ == '__main__':
    build()
