"""
Generate a PDF from the PROJECT_DOCUMENTATION.md file.

Uses markdown (to convert MD -> HTML) and weasyprint (to convert HTML -> PDF).
"""

import sys
from pathlib import Path

import markdown
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parent.parent
MD_FILE = ROOT / "PROJECT_DOCUMENTATION.md"
PDF_FILE = ROOT / "PROJECT_DOCUMENTATION.pdf"

CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm 2.2cm 1.8cm;
}

body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 11px;
    line-height: 1.5;
    color: #222222;
}

h1 {
    font-size: 24px;
    color: #1a1a2e;
    border-bottom: 2px solid #6c63ff;
    padding-bottom: 6px;
    margin-top: 0;
}

h2 {
    font-size: 17px;
    color: #16213e;
    border-bottom: 1px solid #dddddd;
    padding-bottom: 3px;
    margin-top: 24px;
}

h3 {
    font-size: 13px;
    color: #0f3460;
    margin-top: 16px;
}

h4 {
    font-size: 12px;
    color: #333333;
}

p { margin: 6px 0; }

code {
    font-family: Courier, monospace;
    background: #f4f4f8;
    padding: 1px 3px;
    font-size: 10px;
    color: #c7254e;
}

pre {
    background: #1e1e2e;
    color: #e0e0e0;
    padding: 10px;
    font-size: 9px;
    line-height: 1.3;
}

pre code {
    background: none;
    color: #e0e0e0;
    padding: 0;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0;
    font-size: 9.5px;
}

th {
    background: #6c63ff;
    color: white;
    padding: 5px 6px;
    text-align: left;
    border: 1px solid #555555;
}

td {
    padding: 4px 6px;
    border: 1px solid #dddddd;
    vertical-align: top;
}

ul, ol {
    margin: 6px 0;
    padding-left: 20px;
}

li { margin: 2px 0; }

strong { color: #16213e; }

hr {
    border: none;
    border-top: 1px solid #dddddd;
    margin: 16px 0;
}

blockquote {
    border-left: 3px solid #6c63ff;
    margin: 8px 0;
    padding: 4px 12px;
    background: #f8f8fc;
    color: #555555;
}

a {
    color: #6c63ff;
    text-decoration: none;
}
"""


def main() -> int:
    if not MD_FILE.exists():
        print(f"ERROR: Markdown file not found: {MD_FILE}", file=sys.stderr)
        return 1

    md_text = MD_FILE.read_text(encoding="utf-8")

    # Convert markdown -> HTML with extensions
    html_body = markdown.markdown(
        md_text,
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "sane_lists",
            "nl2br",
        ],
        extension_configs={
            "toc": {"toc_depth": "2-3"},
        },
    )

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Cross-Browser Testing Platform - Documentation</title>
    <style>
{CSS}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""

    print(f"Converting to PDF: {PDF_FILE}")
    with open(PDF_FILE, "wb") as out_file:
        status = pisa.CreatePDF(
            src=full_html,
            dest=out_file,
            encoding="utf-8",
        )

    if status.err:
        print(f"ERROR: PDF generation failed with error code {status.err}", file=sys.stderr)
        return 1

    size_kb = PDF_FILE.stat().st_size / 1024
    print(f"PDF generated successfully: {PDF_FILE} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
