import re

with open('import_opgg.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Define the new function as a string
new_func = '''def parse_lane_meta_text(text: str):
    """
    Parse the LolListLaneMetaChampions text into a dict:
    {
        'top':   [{'champion': str, 'win_rate': float, ...}, ...],
        'jungle': [...],
        'mid':   [...],
        'adc':   [...],
        'support': [...]
    }
    """
    # Find the Positions([...]) part
    match = re.search(r'Positions\((\[.*?\])\)', text, re.DOTALL)
    if not match:
        raise ValueError("Could not find Positions in lane meta text")
    inner = match.group(1)  # the [...] containing five sublists
    # Remove outer brackets
    if inner.startswith('[') and inner.endswith(']'):
        inner = inner[1:-1]
    # Split by '],[' to get the five position lists
    parts = re.split(r'],\\s*[', inner)
    if len(parts) != 5:
        # Try alternative split (maybe there are spaces)
        parts = re.split(r'],\\s*[', inner)
    position_names = ['top', 'jungle', 'mid', 'adc', 'support']
    result = {}
    for pos_name, part in zip(position_names, parts):
        # part is like "[Top(...), Top(...), ...]" or "Top(...), Top(...), ..." (without brackets)
        # Remove any surrounding brackets
        if part.startswith('[') and part.endswith(']'):
            part = part[1:-1]
        # Now part is a comma-separated list of entries like: Top("Ambessa",false,...),Top("Renekton",false,...),...
        # Split by '),' to get tokens
        tokens = part.split('),')
        champs = []
        for token in tokens:
            token = token.strip()
            # Remove leading "Top(" if present
            if token.startswith('Top('):
                token = token[4:]
            # Remove trailing ")" if present
            if token.endswith(')'):
                token = token[:-1]
            if not token:
                continue
            # token is now the argument string: e.g., '"Ambessa",false,298,166,1602,0.56,0.05,0.8,0.04,1.82,1,1,5,37'
            fields = [f.strip() for f in token.split(',')]
            # Expect 14 fields
            if len(fields) < 14:
                # pad missing trailing fields with None
                fields += [None] * (14 - len(fields))
            champ_name = fields[0].strip('\"\'')
            is_rip = fields[1].lower() == 'true' if fields[1] not in (None, 'null') else False
            play = int(fields[2]) if fields[2] not in (None, 'null') else 0
            win = int(fields[3]) if fields[3] not in (None, 'null') else 0
            kill = int(fields[4]) if fields[4] not in (None, 'null') else 0
            win_rate = float(fields[5]) if fields[5] not in (None, 'null') else 0.0
            pick_rate = float(fields[6]) if fields[6] not in (None, 'null') else 0.0
            role_rate = float(fields[7]) if fields[7] not in (None, 'null') else 0.0
            ban_rate = float(fields[8]) if fields[8] not in (None, 'null') else 0.0
            kda = float(fields[9]) if fields[9] not in (None, 'null') else 0.0
            tier = int(fields[10]) if fields[10] not in (None, 'null') else 0
            rank = int(fields[11]) if fields[11] not in (None, 'null') else 0
            rank_prev = int(fields[12]) if fields[12] not in (None, 'null') else 0
            rank_prev_patch = int(fields[13]) if fields[13] not in (None, 'null') else 0
            champs.append({
                'champion': champ_name,
                'is_rip': is_rip,
                'play': play,
                'win': win,
                'kill': kill,
                'win_rate': win_rate,
                'pick_rate': pick_rate,
                'role_rate': role_rate,
                'ban_rate': ban_rate,
                'kda': kda,
                'tier': tier,
                'rank': rank,
                'rank_prev': rank_prev,
                'rank_prev_patch': rank_prev_patch
            })
        result[pos_name] = champs
    return result'''

# Replace the function using regex: from the start of the function definition to the end of the function
# We'll match from "def parse_lane_meta_text(text: str):" up to the line before the next function definition or end of file.
# We'll use a pattern that matches the function and everything until we see a line that starts with 'def ' (not indented) or end of string.
pattern = r'def parse_lane_meta_text\(text: str\):.*?(?=\n\ndef |\n\n\n|\Z)'
# Use DOTALL to match across lines
new_content = re.sub(pattern, new_func, content, flags=re.DOTALL)

with open('import_opgg.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Function replaced.")
