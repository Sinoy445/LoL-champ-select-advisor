import re

with open('../data/_probe/lane_meta.txt', 'r', encoding='utf-8') as f:
    text = f.read()

match = re.search(r'Positions\((\[.*?\])\)', text, re.DOTALL)
if match:
    inner = match.group(1)
    print('inner length:', len(inner))
    print('inner first 200 chars:', inner[:200])
    if inner.startswith('[') and inner.endswith(']'):
        inner = inner[1:-1]
    print('after stripping outer brackets length:', len(inner))
    print('inner after stripping first 200 chars:', inner[:200])
    parts = re.split(r'],\s*\[', inner)
    print('number of parts:', len(parts))
    for i, p in enumerate(parts):
        print(f'part {i} length: {len(p)}')
        print(f'  part {i} first 100 chars: {p[:100]}')
        print(f'  part {i} last 100 chars: {p[-100:] if len(p) > 100 else p}')
else:
    print('No match')
