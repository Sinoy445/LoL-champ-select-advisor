# Read the target file
with open('import_opgg.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Read the new function from test_parse2.py (only the function definition)
with open('test_parse2.py', 'r', encoding='utf-8') as f:
    new_func_lines = []
    for line in f:
        if line.startswith('# Test with the actual file'):
            break
        new_func_lines.append(line)

# Now, we need to know the start and end indices of the old function in lines.
# We'll find the line that starts with 'def parse_lane_meta_text(text: str):'
start_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith('def parse_lane_meta_text(text: str):'):
        start_idx = i
        break

if start_idx is None:
    print("Could not find the function definition")
    exit(1)

# Find the end of the function: look for the next line that starts with 'def ' (and is not indented)
end_idx = None
for i in range(start_idx + 1, len(lines)):
    if lines[i].strip().startswith('def ') and not lines[i].startswith(' '):
        end_idx = i
        break
if end_idx is None:
    end_idx = len(lines)

# Replace the lines
new_lines = lines[:start_idx] + new_func_lines + lines[end_idx:]

# Write back
with open('import_opgg.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Replaced lines {start_idx} to {end_idx-1} with the new function.")
