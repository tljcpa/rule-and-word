import re
import unicodedata
import opencc

converter = opencc.OpenCC('t2s')

def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKC', text)
    text = converter.convert(text)
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    text = text.lower()
    return text.strip()
