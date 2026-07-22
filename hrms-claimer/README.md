# HRMS Claim Uploader (DTDL)

Automates **Payroll → Employee → Reimbursement → Claim Request** on GT HRMS.

## Setup

```bash
cd hrms-claimer
npm install
npx playwright install chromium
cp hrms-config.example.json hrms-config.json
```

Edit **`hrms-config.json`**:

```json
{
  "username": "YOUR_USER_ID",
  "password": "YOUR_PASSWORD",
  "receiptsDirectory": "/path/to/your/receipts",
  "claimType": "fuel",
  "defaultAmount": 2800,
  "autoSave": true
}
```

## Run batch upload

```bash
npm run upload
```

Prompts (if not in config):

1. **Document type** — `fuel` or `driver`
2. **Receipts folder** — folder with files like `2026AA3210_14Jan2026.png`

Then it will:

1. Login using config credentials
2. Navigate to Claim Request → Fuel (or Driver)
3. For each file **sorted by receipt ID**:
   - Fill Bill No + Bill Details
   - Upload PNG/PDF
   - Fill Bill Date + Amount **after upload** (fixes fields clearing)
   - Click **Save**

## Filename format

`RECEIPTID_14Jan2026.png` (DocForge fuel export)

| HRMS field   | Value                          |
|--------------|--------------------------------|
| Bill No      | `2026AA3210`                   |
| Bill Details | `2026AA3210 - 14 Jan 2026`     |
| Bill Date    | `14/01/2026` (tries 3 formats) |
| Bill Amount  | `2800`                         |

## Other commands

```bash
npm run login      # test login only
npm run inspect -- fuel   # dump form field IDs
```

## Fields not filling?

Create `hrms-field-map.json` — see `hrms-field-map.json.example`.

## Security

`hrms-config.json` is gitignored — never commit passwords.
