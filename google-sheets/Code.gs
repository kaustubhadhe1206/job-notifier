/**
 * Hidden Jobs → Google Sheets bridge.
 *
 * Paste this into your spreadsheet (Extensions → Apps Script), set SECRET to the same value as
 * SHEETS_SECRET in Vercel, run `setup` once, then Deploy → New deployment → Web app
 * (Execute as: Me, Who has access: Anyone). Full steps are in SETUP.md.
 *
 * The web app only accepts requests carrying the secret, and only ever adds rows to the
 * "Jobs" tab of this spreadsheet.
 */
const SECRET = 'PASTE_YOUR_SHEETS_SECRET_HERE';
const SHEET_NAME = 'Jobs';
const HEADERS = ['Found', 'Job title', 'Company', 'Location', 'Sponsorship', 'Details', 'Source', 'Posted',
                 'Link', 'Status', 'My notes', 'Job ID'];
const STATUS_OPTIONS = ['Applied', 'Interviewing', 'Offer', 'Rejected', 'Not interested'];

/** Run this once from the editor: it creates the "Jobs" tab and asks for permission. */
function setup() {
  const sheet = getSheet_();
  Logger.log('Ready: ' + SpreadsheetApp.getActiveSpreadsheet().getUrl() + ' (tab "' + sheet.getName() + '")');
}

function doGet() {
  return reply_({ ok: true, message: 'Hidden Jobs sheet bridge is running. The app talks to it with POST.' });
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply_({ ok: false, error: 'Request body is not JSON' });
  }
  if (SECRET === 'PASTE_YOUR_SHEETS_SECRET_HERE' || !body || body.secret !== SECRET) {
    return reply_({ ok: false, error: 'Wrong secret: SECRET in Code.gs must equal SHEETS_SECRET in Vercel' });
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = getSheet_();
  if (body.action === 'ping') {
    return reply_({ ok: true, url: spreadsheet.getUrl(), sheet: SHEET_NAME, rows: Math.max(0, sheet.getLastRow() - 1) });
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const idColumn = HEADERS.length;
    const last = sheet.getLastRow();
    const known = new Set(
      last > 1 ? sheet.getRange(2, idColumn, last - 1, 1).getValues().map(function (r) { return String(r[0]); }) : []
    );

    const rows = [];
    (body.jobs || []).forEach(function (j) {
      const id = String(j.id);
      if (known.has(id)) return;  // already in the sheet
      known.add(id);
      rows.push([
        j.found_at ? new Date(j.found_at) : new Date(),
        link_(j.url, j.title),
        safe_(j.company),
        safe_(j.location),
        safe_(j.sponsorship),
        safe_(j.details),
        safe_(j.source),
        j.posted_at ? new Date(j.posted_at) : '',
        safe_(j.url),
        '',
        '',
        id,
      ]);
    });

    if (rows.length) {
      // Newest jobs go right under the header.
      sheet.insertRowsAfter(1, rows.length);
      const range = sheet.getRange(2, 1, rows.length, HEADERS.length);
      range.setValues(rows).setFontWeight('normal').setBackground(null).setWrap(false);
      sheet.getRange(2, 1, rows.length, 1).setNumberFormat('yyyy-mm-dd hh:mm');
      sheet.getRange(2, 8, rows.length, 1).setNumberFormat('yyyy-mm-dd');
      sheet.getRange(2, 10, rows.length, 1).setDataValidation(
        SpreadsheetApp.newDataValidation().requireValueInList(STATUS_OPTIONS, true).setAllowInvalid(true).build()
      );
    }
    return reply_({ ok: true, appended: rows.length, url: spreadsheet.getUrl() });
  } finally {
    lock.releaseLock();
  }
}

function getSheet_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = spreadsheet.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = spreadsheet.insertSheet(SHEET_NAME, 0);
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
      .setFontWeight('bold').setBackground('#eef2ff');
    sheet.setFrozenRows(1);
    const widths = [130, 320, 160, 180, 160, 360, 140, 90, 260, 120, 220, 80];
    widths.forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
  }
  return sheet;
}

/** Clickable title. Values are inside a string literal, so they can't run as formulas. */
function link_(url, title) {
  const t = String(title || '').replace(/"/g, '""').slice(0, 250);
  const u = String(url || '').replace(/"/g, '""');
  return /^https?:\/\//i.test(u) && u.length < 1500 ? '=HYPERLINK("' + u + '","' + t + '")' : safe_(title);
}

/** Text from job sites must never be interpreted as a formula. */
function safe_(value) {
  const s = value == null ? '' : String(value);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
