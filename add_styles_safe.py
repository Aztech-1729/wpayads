import sys

def add_style(content):
    idx = 0
    while True:
        idx = content.find("Button.inline(", idx)
        if idx == -1:
            break
        # find the matching closing parenthesis
        depth = 0
        in_string = False
        escape = False
        string_char = None
        for i in range(idx + 14, len(content)):
            char = content[i]
            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == string_char:
                    in_string = False
            else:
                if char in ('"', "'"):
                    in_string = True
                    string_char = char
                elif char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0:
                        # Found the matching parenthesis!
                        call_content = content[idx+14:i]
                        if "style=" not in call_content:
                            # check for trailing comma
                            has_comma = False
                            for j in range(i-1, idx+13, -1):
                                if content[j] not in ' \n\t\r':
                                    if content[j] == ',':
                                        has_comma = True
                                    break
                            
                            insert_str = 'style="primary"' if has_comma else ', style="primary"'
                            content = content[:i] + insert_str + content[i:]
                        
                        idx = i + 1
                        break
                    else:
                        depth -= 1
    return content

with open('telegram/keyboards.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = add_style(text)

with open('telegram/keyboards.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated all Button.inline calls!")
