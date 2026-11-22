"""Build formal English and Chinese papers using the shared typography engine."""
from pathlib import Path
import sys

lang = sys.argv[1] if len(sys.argv)>1 else 'en'
assert lang in ('en','zh')
base = Path(__file__).parent
template = base / ('build_second_draft_pdf.py' if lang=='en' else 'build_second_draft_zh_pdf.py')
code = template.read_text(encoding='utf8')
code = code.replace("SOURCE=ROOT/'WEVJ_second_draft.md'", "SOURCE=ROOT/'SOH_game_dispatch_en.md'")
code = code.replace("SOURCE=ROOT/'WEVJ_second_draft_zh.md'", "SOURCE=ROOT/'SOH_game_dispatch_zh.md'")
import re
code = re.sub(r"OUTPUT=ROOT/'[^']+'", f"OUTPUT=ROOT/'SOH_game_dispatch_{lang}.pdf'", code)
code = code.replace('parper/second_draft_equations/manifest.json','parper/soh_paper_equations/manifest.json')
code = re.sub(r"canvas.drawString\(54,A4\[1\]-34,.*?\)", "canvas.drawString(54,A4[1]-34,'SOH-aware EV charging microgrid')", code)
code = re.sub(r"    assert all\(s in joined for s in .*?\)\n", '', code)
code = re.sub(r"    assert all\('Figure '.*?\n", '', code)
code = re.sub(r"    assert all\('Table '.*?\n", '', code)
code = re.sub(r"    assert all\('图.*?\n", '', code)
code = re.sub(r"    assert all\('表.*?\n", '', code)
code = code.replace("second_draft_feasibility_pdf_audit_20261118.json", f"soh_paper_{lang}_pdf_audit.json")
code = code.replace("second_draft_zh_feasibility_pdf_audit_20261118.json", f"soh_paper_{lang}_pdf_audit.json")
code = code.replace("elif n==6:", "elif n==4: widths=[WIDTH*.37]+[WIDTH*.21]*3\n            elif n==6:")
code = code.replace("    audit=dict(", "    assert figures==2 and equation==11\n    assert '@@' not in text\n    assert not re.search(r'first draft|second draft|review draft|一稿|二稿|审阅版',text,re.I)\n    audit=dict(")
exec(compile(code, str(template), 'exec'), {'__name__':'__main__'})
