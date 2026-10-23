"""Embed local KaTeX assets for self-contained, offline HTML exports."""
from functools import lru_cache
import base64
from pathlib import Path
import re

VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor" / "katex"


@lru_cache(maxsize=1)
def offline_math_assets() -> str:
    css = (VENDOR / "katex.min.css").read_text(encoding="utf-8")

    def font_url(match):
        path = (VENDOR / match.group(2)).resolve()
        if not path.is_relative_to((VENDOR / "fonts").resolve()):
            return match.group(0)
        mime = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[path.suffix]
        return f'url(data:{mime};base64,{base64.b64encode(path.read_bytes()).decode("ascii")})'

    css = re.sub(r'url\(([\'\"]?)(fonts/[^)\'\"]+)\1\)', font_url, css)
    main = (VENDOR / "katex.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    auto = (VENDOR / "contrib" / "auto-render.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    options = r'''renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}],throwOnError:false,trust:false,maxExpand:100,maxSize:20});'''
    # Keep the distinct code (MIT) and font (OFL) licenses in standalone exports.
    notice = "\n\n".join((VENDOR / path).read_text(encoding="utf-8") for path in ("LICENSE", "fonts/OFL.txt", "fonts/NOTICE.txt")).replace("--", "- -")
    return f'<!-- KaTeX license:\n{notice}\n--><style>{css}.katex-display{{overflow-x:auto;overflow-y:hidden}}</style><script>{main}</script><script>{auto}</script><script>{options}</script>'
