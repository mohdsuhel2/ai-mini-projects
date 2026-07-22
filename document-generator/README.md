# DocForge — Document Generator

Enterprise-ready static website for generating **fuel receipts**, **rent receipts**, **driver slips**, **ecommerce tax invoices**, and **postpaid bills** with live preview and export.

No build step. Deploy anywhere static files are hosted.

## Site structure

| Page | URL | Description |
|------|-----|-------------|
| **Fuel Receipt** (default) | `/` or `/fuel-receipt.html` | Fuel receipt generator |
| **Rent Receipt** | `/rent-receipt.html` | Rent receipt with signature & fee slip |
| **Driver Slip** | `/driver-slip.html` | Driver license verification slip |
| **Ecommerce Invoice** | `/ecommerce-invoice.html` | GST invoice generator |
| **Postpaid Bill** | `/postpaid-bill.html` | Mobile/broadband bill generator |
| **Features** | `/features.html` | Feature overview |
| **About** | `/about.html` | What DocForge does and how it works |
| **404** | `/404.html` | Not found page |

```
document-generator/
├── index.html              # Redirects to fuel-receipt.html
├── about.html
├── fuel-receipt.html       # → generator engine (symlink)
├── rent-receipt.html
├── driver-slip.html
├── ecommerce-invoice.html
├── postpaid-bill.html
├── generator.html          # Core app (also reachable directly)
├── features.html
├── 404.html
├── css/site.css
├── js/site.js
├── netlify.toml
├── pdf-lib.min.js
├── html2canvas.min.js
├── jszip.min.js
└── assets (images, fonts, …)
```

## Local development

```bash
cd document-generator
python3 -m http.server 8765
```

- Default: http://localhost:8765/ → Fuel Receipt
- Rent: http://localhost:8765/rent-receipt.html
- Driver: http://localhost:8765/driver-slip.html
- About: http://localhost:8765/about.html

## Generator features

| Document type | Export | Modes |
|---------------|--------|--------|
| Fuel Receipt | PNG | Single or bulk ZIP |
| Rent Receipt | PDF | Single or monthly bulk (signature + fee slip upload) |
| Driver Slip | PDF | Single or monthly bulk (photo upload) |
| Ecommerce Invoice | PDF | Multi-line GST invoice |
| Postpaid Bill | PDF | Single or monthly bulk |

Bulk preview URLs:

- Fuel: `?view=bulk` · form tab: `?mode=bulk`
- Postpaid: `?view=bulk-bill` · form tab: `?mode=bulk`
- Rent: `?view=bulk-rent` · form tab: `?mode=bulk`
- Driver: `?view=bulk-driver` · form tab: `?mode=bulk`

## Notes

- Theme and panel width preferences are stored in `localStorage`
- Serve via HTTP — not `file://` — for PDF/PNG export and fonts
- Exported PDFs are image-based (HTML screenshot)
- Uploaded images are embedded as data URLs in generated PDFs

## License

Private / personal use. Third-party: pdf-lib, html2canvas, JSZip, Flatpickr.
