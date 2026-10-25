# Copyright and license

Copyright (C) 2026 Folio contributors.

Folio is free software licensed under **GNU Affero General Public License v3.0 only** (SPDX: AGPL-3.0-only). See [LICENSE](LICENSE) for the complete terms. It is provided without warranty. The corresponding source is available at <https://github.com/rqyhcj/folio-paper-reader>.

The application does not include a model, credentials, paper library, or publisher content. Original PDFs, extracted figures, translations, and user exports remain subject to the rights and terms applicable to their respective content; this software license does not grant rights to redistribute them.

## Direct runtime dependencies

| Project | License | Upstream license |
| --- | --- | --- |
| PyMuPDF / MuPDF | GNU AGPL v3, alternative commercial licensing | <https://github.com/pymupdf/PyMuPDF/blob/main/COPYING> |
| FastAPI | MIT | <https://github.com/fastapi/fastapi/blob/master/LICENSE> |
| Uvicorn | BSD-3-Clause | <https://github.com/Kludex/uvicorn/blob/main/LICENSE.md> |
| HTTPX | BSD-3-Clause | <https://github.com/encode/httpx/blob/master/LICENSE.md> |
| python-multipart | Apache-2.0 | <https://github.com/Kludex/python-multipart/blob/master/LICENSE.txt> |
| Ziamath | MIT | <https://github.com/cdelker/ziamath/blob/master/LICENSE.txt> |
| Ziafont (via Ziamath) | MIT | <https://github.com/cdelker/ziafont/blob/master/LICENSE.txt> |
| latex2mathml (via Ziamath) | MIT | <https://github.com/roniemartinez/latex2mathml/blob/master/LICENSE> |
| STIX Two Math font (via Ziamath) | SIL OFL-1.1 | <https://github.com/stipub/stixfonts/blob/master/OFL.txt> |
| KaTeX 0.19.0 scripts and CSS | MIT | `static/vendor/katex/LICENSE`; <https://github.com/KaTeX/KaTeX/blob/main/LICENSE> |
| KaTeX font files | SIL OFL-1.1 | `static/vendor/katex/fonts/OFL.txt` and `fonts/NOTICE.txt`; <https://openfontlicense.org/documents/OFL.txt> |

This repository distributes application source, dependency declarations, and the explicitly vendored KaTeX browser distribution. It does not bundle Python, Python dependency binaries, or models. Dependencies installed by pip retain their own copyright and license materials. PyMuPDF's built-in fonts and components belong to its upstream distribution. A future bundled binary/container distribution requires a complete inventory, notices, and corresponding-source compliance for its exact components.

Provider names are descriptive configuration suggestions and do not imply affiliation, endorsement, or access entitlement. Users supply their own accounts, authorized models, and API keys.

The unmodified KaTeX fonts retain their own OFL license, copyright, and Reserved Font Names; they are not relicensed as AGPL. All required text is distributed alongside the fonts. Offline HTML exports embed font data and preserve their license notices within the exported HTML.
