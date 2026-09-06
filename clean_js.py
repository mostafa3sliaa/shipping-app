import re
with open('test.js', 'r', encoding='utf-8') as f:
    js = f.read()
js = re.sub(r'\{%[^%]*%\}', '', js)
js = re.sub(r'\{\{[^\}]*\}\}', '""', js)
with open('test_clean.js', 'w', encoding='utf-8') as f:
    f.write(js)
