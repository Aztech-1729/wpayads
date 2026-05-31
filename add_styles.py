import re

with open('telegram/keyboards.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'Button.inline' in line and 'style=' not in line:
        # Match Button.inline( ... ) at the end of the line, possibly followed by brackets or commas
        # e.g. Button.inline("Text", data),
        # e.g. [Button.inline("Text", data)]
        
        # We can just look for the last ')' and insert ', style="primary"'
        # but only if the line has exactly one Button.inline and it closes on the same line.
        if line.count('Button.inline') == 1:
            # Find the last closing parenthesis
            last_paren_idx = line.rfind(')')
            if last_paren_idx != -1:
                line = line[:last_paren_idx] + ', style="primary"' + line[last_paren_idx:]
    new_lines.append(line)

with open('telegram/keyboards.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Replaced all Button.inline calls.")
