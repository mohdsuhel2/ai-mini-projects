import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

export const HRMS = {
  loginUrl: 'https://gthrms.wcgt.in/HRMS2020/Login.aspx?CID=DTDL',
  companyId: 'DTDL',
};

export const PATHS = {
  root: ROOT,
  profileDir: path.join(ROOT, '.hrms-browser-profile'),
  authState: path.join(ROOT, '.hrms-auth.json'),
};

/** Default reimbursement amount per bill (INR) */
export const DEFAULT_BILL_AMOUNT = 2800;

/** Menu labels as shown in HRMS */
export const NAV = {
  payroll: /payroll/i,
  employee: /employee/i,
  reimbursement: /reimbursement/i,
  claimRequest: /claim\s*request/i,
};

export const CLAIM_TYPES = {
  fuel: {
    label: 'Fuel',
    menu: /fuel/i,
  },
  driver: {
    label: 'Driver slip',
    menu: /driver/i,
  },
};
