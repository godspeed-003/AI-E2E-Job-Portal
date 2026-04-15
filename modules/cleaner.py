import re

def clean_text(text: str) -> str:
    if not text:
        return ""
    # Merge spaced characters (e.g., P h o n e -> Phone)
    text = re.sub(r'(?<=\b[a-zA-Z]) (?=[a-zA-Z]\b)', '', text)
    # Remove excessive newlines
    text = re.sub(r'\n+', '\n', text)
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()
