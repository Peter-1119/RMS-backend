# RMS-backend

Flask + MySQL backend for RMS (document drafting/management) with:
- Token auth via HMAC (offline signature)
- Draft/document attributes
- Dynamic content blocks (process flow, management, exceptions, references, MCR parameters)
- Media upload service (images, draw.io → PNG optional)
- CSV-backed lookups (machines, projects)

> Tip: if you later move media endpoints into `modules/media.py`, expose them under `/uploads/*` (to match your frontend).

## Requirements

- Python 3.10+ recommended
- MySQL server reachable from this machine
- Windows users: optional Draw.io CLI if you plan to convert `.drawio` → PNG
- Recommended: virtualenv or conda

