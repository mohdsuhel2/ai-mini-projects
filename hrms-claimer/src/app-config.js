import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const CONFIG_PATH = path.join(__dirname, '..', 'hrms-config.json');
export const CONFIG_EXAMPLE_PATH = path.join(__dirname, '..', 'hrms-config.example.json');

const DEFAULTS = {
  loginUrl: 'https://gthrms.wcgt.in/HRMS2020/Login.aspx?CID=DTDL',
  companyId: 'DTDL',
  username: '',
  password: '',
  defaultAmount: 2800,
  dateFormats: ['dd/MM/yyyy'],
  receiptsDirectory: '',
  claimType: 'fuel',
  manualNavigation: true,
  delayMs: 600,
};

export function loadAppConfig() {
  let file = {};
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      file = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    } catch (err) {
      throw new Error(`Invalid ${CONFIG_PATH}: ${err.message}`);
    }
  }
  return { ...DEFAULTS, ...file };
}

export function ensureConfigExists() {
  if (!fs.existsSync(CONFIG_PATH) && fs.existsSync(CONFIG_EXAMPLE_PATH)) {
    console.warn(`\n⚠ Create ${CONFIG_PATH} from hrms-config.example.json (add username & password)\n`);
  }
}
