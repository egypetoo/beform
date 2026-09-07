/**
 * BE FORM - Google Sheet bridge (paste ALL of this into Apps Script)
 * 1) Save
 * 2) Deploy → New deployment → Web app
 * 3) Execute as: Me | Who has access: Anyone
 * 4) Copy Web App URL into server .env as GOOGLE_SHEET_WEBHOOK=
 * 5) SHEET_SECRET below must match .env SHEET_SECRET=
 * 6) Open the Web App URL in Chrome — you must see version beform-2026-09-07
 */

const SHEET_SECRET = "HNCAHozrrkIAst0M1O_ZMqO9eY9XB4fILazhfu79ka0";

function doGet() {
  return jsonResponse({
    ok: true,
    ping: true,
    version: "beform-2026-09-07",
    via: "GET",
  });
}

function doPost(e) {
  const data = parseData(e);
  if (!SHEET_SECRET || data.secret !== SHEET_SECRET) {
    return jsonResponse({ ok: false, error: "unauthorized" });
  }

  const action = data.action || "create";

  if (action === "ping") {
    return jsonResponse({
      ok: true,
      ping: true,
      version: "beform-2026-09-07",
      via: "POST",
    });
  }

  if (action === "list") {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheetName = !data.department || data.department === "ALL" ? "All" : data.department;
    const sheet = ss.getSheetByName(sheetName);
    return jsonResponse({ ok: true, rows: sheet ? sheetToObjects(sheet) : [] });
  }

  if (action === "lookup") {
    return jsonResponse({
      ok: true,
      rows: lookupByFingerprint(data.fingerprint_id || "", data.limit || 30, data.name || ""),
    });
  }

  if (action === "set_status") {
    const items = data.items && data.items.length
      ? data.items
      : [{ request_id: data.request_id, department: data.department || "" }];
    updateStatuses(items, data.status, data.reviewed_by || "", data.reason || "");
    return jsonResponse({ ok: true });
  }

  if (action === "delete_by_notes") {
    const needle = String(data.notes_contains || "").trim().toLowerCase();
    if (!needle || needle.length < 4) {
      return jsonResponse({ ok: false, error: "notes_required" });
    }
    return jsonResponse({ ok: true, deleted: deleteRowsByNotes(needle) });
  }

  if (action !== "create") {
    return jsonResponse({ ok: false, error: "unknown_action" });
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const deptSheet = getOrCreateSheet(ss, data.department || "Other");
  const deptHeaders = ensureHeaders(deptSheet);
  const values = recentSheetValues(deptSheet, deptHeaders, 800);
  const blocked = checkCreateConflicts(values, data);
  if (blocked) {
    return jsonResponse(blocked);
  }

  const valuesByHeader = {
    "Request ID": data.request_id || "",
    "Submitted At": data.submitted_at || "",
    "Fingerprint Number": data.fingerprint_id || "",
    "Device": data.device || "",
    "Name": data.name || "",
    "Department": data.department || "",
    "Team": data.team || "",
    "Request Type": data.request_type || "",
    "Request Date": data.request_date || "",
    "Punch In Time": data.punch_in_time || "",
    "Punch Out Time": data.punch_out_time || "",
    "From Time": data.from_time || "",
    "To Time": data.to_time || "",
    "From Date": data.start_date || "",
    "To Date": data.end_date || "",
    "Notes": data.notes || "",
    "Status": data.status || "Pending",
    "Reviewed By": "",
    "Reviewed At": "",
    "Rejection Reason": "",
  };

  const allSheet = getOrCreateSheet(ss, "All");
  const allHeaders = ensureHeaders(allSheet);
  appendMappedRow(deptSheet, valuesByHeader, deptHeaders);
  appendMappedRow(allSheet, valuesByHeader, allHeaders);
  return jsonResponse({ ok: true, duplicate: false });
}

function parseData(e) {
  if (e && e.postData && e.postData.contents) {
    return JSON.parse(e.postData.contents);
  }
  return (e && e.parameter) || {};
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function normalizeText(value) {
  return String(value == null ? "" : value).trim().toLowerCase();
}

function normalizeDate(value) {
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  return String(value == null ? "" : value).trim().split(" ")[0];
}

function normalizeFingerprint(value) {
  return String(value == null ? "" : value).trim().replace(/\.0$/, "");
}

function headerList() {
  return [
    "Request ID", "Submitted At", "Fingerprint Number", "Device", "Name", "Department",
    "Team", "Request Type", "Request Date", "Punch In Time", "Punch Out Time",
    "From Time", "To Time", "From Date", "To Date", "Notes", "Status",
    "Reviewed By", "Reviewed At", "Rejection Reason",
  ];
}

function getOrCreateSheet(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  return sheet;
}

function ensureHeaders(sheet) {
  const needed = headerList();
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, needed.length).setValues([needed]);
    sheet.setFrozenRows(1);
    return needed;
  }
  let headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
  const missing = needed.filter(function (name) { return headers.indexOf(name) === -1; });
  if (missing.length) {
    sheet.getRange(1, sheet.getLastColumn() + 1, 1, missing.length).setValues([missing]);
    headers = headers.concat(missing);
  }
  return headers;
}

function appendMappedRow(sheet, valuesByHeader, headers) {
  if (!headers || !headers.length) headers = ensureHeaders(sheet);
  const row = headers.map(function (header) {
    return Object.prototype.hasOwnProperty.call(valuesByHeader, header) ? valuesByHeader[header] : "";
  });
  sheet.getRange(Math.max(sheet.getLastRow(), 1) + 1, 1, 1, row.length).setValues([row]);
}

function recentSheetValues(sheet, headers, limit) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [headers];
  const count = Math.min(Math.max(lastRow - 1, 0), limit || 800);
  const startRow = lastRow - count + 1;
  return [headers].concat(sheet.getRange(startRow, 1, count, Math.max(sheet.getLastColumn(), headers.length)).getValues());
}

function sheetToObjects(sheet) {
  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow < 2) return [];
  const values = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  const headers = values[0];
  const rows = [];
  for (let i = 1; i < values.length; i++) {
    const obj = {};
    headers.forEach(function (header, index) {
      const value = values[i][index];
      if (Object.prototype.toString.call(value) === "[object Date]") {
        obj[header] = Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss");
      } else {
        obj[header] = value == null ? "" : String(value);
      }
    });
    if (!obj["Request ID"] && !obj["Name"]) continue;
    rows.push(obj);
  }
  return rows.reverse();
}

function lookupByFingerprint(fingerprint, limit, name) {
  const fp = normalizeFingerprint(fingerprint);
  if (!fp) return [];
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("All");
  if (!sheet) return [];
  const wantedName = normalizeText(name || "");
  const max = Math.max(1, Math.min(parseInt(limit, 10) || 30, 50));
  return sheetToObjects(sheet).filter(function (row) {
    if (normalizeFingerprint(row["Fingerprint Number"]) !== fp) return false;
    if (!wantedName) return true;
    const rowName = normalizeText(row["Name"]);
    return rowName === wantedName || rowName.indexOf(wantedName) !== -1 || wantedName.indexOf(rowName) !== -1;
  }).slice(0, max);
}

function updateStatuses(items, status, reviewedBy, reason) {
  const ids = [];
  const names = { All: true };
  (items || []).forEach(function (item) {
    if (item && item.request_id) {
      ids.push(String(item.request_id));
      if (item.department && item.department !== "ALL") names[item.department] = true;
    }
  });
  if (!ids.length) throw new Error("Missing request id");
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  Object.keys(names).forEach(function (name) {
    const sheet = ss.getSheetByName(name);
    if (sheet) updateSheetStatuses(sheet, ids, status, reviewedBy, reason || "");
  });
}

function updateSheetStatuses(sheet, requestIds, status, reviewedBy, reason) {
  if (sheet.getLastRow() < 2) return;
  ensureHeaders(sheet);
  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const idCol = headers.indexOf("Request ID") + 1;
  const statusCol = headers.indexOf("Status") + 1;
  const reviewedByCol = headers.indexOf("Reviewed By") + 1;
  const reviewedAtCol = headers.indexOf("Reviewed At") + 1;
  const reasonCol = headers.indexOf("Rejection Reason") + 1;
  if (!idCol || !statusCol) return;
  const ids = sheet.getRange(2, idCol, sheet.getLastRow(), idCol).getValues();
  const now = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss");
  const wanted = {};
  requestIds.forEach(function (id) { wanted[String(id)] = true; });
  for (let i = 0; i < ids.length; i++) {
    if (wanted[String(ids[i][0])]) {
      const row = i + 2;
      sheet.getRange(row, statusCol).setValue(status);
      if (reviewedByCol) sheet.getRange(row, reviewedByCol).setValue(reviewedBy);
      if (reviewedAtCol) sheet.getRange(row, reviewedAtCol).setValue(now);
      if (reasonCol) sheet.getRange(row, reasonCol).setValue(status === "Rejected" ? reason : "");
    }
  }
}

function deleteRowsByNotes(needle) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let deleted = 0;
  ss.getSheets().forEach(function (sheet) {
    deleted += deleteSheetRowsByNotes(sheet, needle);
  });
  return deleted;
}

function deleteSheetRowsByNotes(sheet, needle) {
  if (sheet.getLastRow() < 2) return 0;
  ensureHeaders(sheet);
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const notesCol = headers.indexOf("Notes") + 1;
  if (!notesCol) return 0;
  const lastRow = sheet.getLastRow();
  const values = sheet.getRange(2, notesCol, lastRow, notesCol).getValues();
  let deleted = 0;
  for (let i = values.length - 1; i >= 0; i--) {
    const text = String(values[i][0] == null ? "" : values[i][0]).toLowerCase();
    if (text.indexOf(needle) !== -1) {
      sheet.deleteRow(i + 2);
      deleted += 1;
    }
  }
  return deleted;
}

function checkCreateConflicts(values, data) {
  return null;
}
