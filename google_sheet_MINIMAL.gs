/**
 * BE FORM - Google Sheet bridge (paste ALL of this into Apps Script)
 * 1) Save
 * 2) Set SHEET_SECRET via setupSheetSecret() (see below) — do NOT commit secrets
 * 3) Deploy → New deployment → Web app
 * 4) Execute as: Me | Who has access: Anyone
 * 5) Copy Web App URL into server .env as GOOGLE_SHEET_WEBHOOK=
 * 6) SHEET_SECRET in Script Properties must match server .env SHEET_SECRET=
 * 7) Open the Web App URL in Chrome — you must see version beform-2026-09-09
 */

var NEW_SHEET_SECRET = "";

function setupSheetSecret() {
  var value = String(NEW_SHEET_SECRET || "").trim();
  if (!value) {
    throw new Error("Set NEW_SHEET_SECRET temporarily, run setupSheetSecret, then clear it.");
  }
  PropertiesService.getScriptProperties().setProperty("SHEET_SECRET", value);
}

function getSheetSecret() {
  return String(PropertiesService.getScriptProperties().getProperty("SHEET_SECRET") || "").trim();
}

function doGet() {
  return jsonResponse({
    ok: true,
    ping: true,
    version: "beform-2026-09-09",
    via: "GET",
    secret_configured: Boolean(getSheetSecret()),
  });
}

function doPost(e) {
  const data = parseData(e);
  const sheetSecret = getSheetSecret();
  if (!sheetSecret || data.secret !== sheetSecret) {
    return jsonResponse({ ok: false, error: "unauthorized" });
  }

  const action = data.action || "create";

  if (action === "ping") {
    return jsonResponse({
      ok: true,
      ping: true,
      version: "beform-2026-09-09",
      via: "POST",
      secret_configured: true,
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
    if (!needle) throw new Error("Missing notes_contains");
    return jsonResponse({ ok: true, deleted: deleteRowsByNotes(needle) });
  }

  if (action === "delete_requests") {
    return jsonResponse({ ok: true, deleted: deleteRequests(data.request_ids || []) });
  }

  // create
  const result = createRequest(data);
  return jsonResponse(result);
}

function parseData(e) {
  if (!e || !e.postData || !e.postData.contents) return {};
  return JSON.parse(e.postData.contents);
}

function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function headerList() {
  return [
    "Request ID", "Submitted At", "Fingerprint Number", "Device", "Name", "Department", "Team",
    "Request Type", "Request Date", "Punch In Time", "Punch Out Time", "From Time", "To Time",
    "From Date", "To Date", "Notes", "Status", "Reviewed By", "Reviewed At", "Rejection Reason",
  ];
}

function ensureHeaders(sheet) {
  const headers = headerList();
  if (sheet.getLastRow() < 1) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    return headers;
  }
  const lastCol = Math.max(sheet.getLastColumn(), headers.length);
  const existing = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(String);
  let changed = false;
  headers.forEach(function (header, index) {
    if (existing[index] !== header) {
      existing[index] = header;
      changed = true;
    }
  });
  if (changed || existing.length < headers.length) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  return headers;
}

function getOrCreateSheet(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  return sheet;
}

function appendMappedRow(sheet, valuesByHeader, headers) {
  if (!headers || !headers.length) headers = ensureHeaders(sheet);
  const row = headers.map(function (header) {
    return Object.prototype.hasOwnProperty.call(valuesByHeader, header) ? valuesByHeader[header] : "";
  });
  while (row.length < headers.length) row.push("");
  sheet.appendRow(row.slice(0, headers.length));
}

function recentSheetValues(sheet, headers, limit) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [headers];
  const lastCol = Math.max(sheet.getLastColumn(), headers.length);
  const count = Math.min(Math.max(lastRow - 1, 0), limit || 800);
  const startRow = lastRow - count + 1;
  return [headers].concat(sheet.getRange(startRow, 1, count, lastCol).getValues());
}

function sheetToObjects(sheet) {
  const headers = ensureHeaders(sheet);
  const values = recentSheetValues(sheet, headers, 800);
  if (values.length < 2) return [];
  return values.slice(1).map(function (row) {
    const obj = {};
    headers.forEach(function (header, index) {
      obj[header] = row[index] == null ? "" : row[index];
    });
    return obj;
  });
}

function createRequest(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const department = data.department || "General";
  const headers = headerList();
  const deptSheet = getOrCreateSheet(ss, department);
  const allSheet = getOrCreateSheet(ss, "All");
  ensureHeaders(deptSheet);
  ensureHeaders(allSheet);

  const requestId = data.request_id || Utilities.getUuid();
  const row = {
    "Request ID": requestId,
    "Submitted At": data.submitted_at || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss"),
    "Fingerprint Number": data.fingerprint_id || "",
    "Device": data.device || "",
    "Name": data.name || "",
    "Department": department,
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

  // Idempotent create
  const existing = recentSheetValues(allSheet, headers, 800).slice(1);
  for (var i = 0; i < existing.length; i++) {
    if (String(existing[i][0]) === String(requestId)) {
      return { ok: true, duplicate: true, request_id: requestId };
    }
  }

  appendMappedRow(deptSheet, row, headers);
  appendMappedRow(allSheet, row, headers);
  return { ok: true, duplicate: false, request_id: requestId };
}

function lookupByFingerprint(fingerprint, limit, name) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("All");
  if (!sheet) return [];
  const rows = sheetToObjects(sheet);
  const wanted = String(fingerprint || "").replace(/\D/g, "");
  const wantedName = String(name || "").trim().toLowerCase();
  return rows.filter(function (row) {
    const fp = String(row["Fingerprint Number"] || "").replace(/\D/g, "");
    if (fp !== wanted) return false;
    if (wantedName && String(row["Name"] || "").trim().toLowerCase() !== wantedName) return false;
    return true;
  }).slice(0, limit || 30);
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

function deleteRequests(requestIds) {
  return 0;
}

function checkCreateConflicts(values, data) {
  return null;
}
