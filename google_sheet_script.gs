function doPost(e) {
  const data = parseData(e);
  const action = data.action || "create";

  if (action === "list") {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheetName = !data.department || data.department === "ALL" ? "All" : data.department;
    const sheet = ss.getSheetByName(sheetName);
    return jsonResponse({ ok: true, rows: sheet ? sheetToObjects(sheet) : [] });
  }

  if (action === "set_status") {
    updateStatus(data.request_id, data.status, data.reviewed_by || "", data.department || "");
    return jsonResponse({ ok: true });
  }

  const row = [
    data.request_id || "",
    data.submitted_at || "",
    data.fingerprint_id || "",
    data.name || "",
    data.department || "",
    data.request_type || "",
    data.request_date || "",
    data.punch_in_time || "",
    data.punch_out_time || "",
    data.from_time || "",
    data.to_time || "",
    data.start_date || "",
    data.end_date || "",
    data.notes || "",
    data.status || "Pending",
    "",
    "",
  ];

  getSheetByName(data.department || "Other").appendRow(row);
  getSheetByName("All").appendRow(row);

  return jsonResponse({ ok: true });
}

function parseData(e) {
  if (e.postData && e.postData.contents) {
    return JSON.parse(e.postData.contents);
  }
  return e.parameter || {};
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheetByName(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
  }
  ensureHeaders(sheet);
  return sheet;
}

function headerList() {
  return [
    "Request ID",
    "Submitted At",
    "Fingerprint Number",
    "Name",
    "Department",
    "Request Type",
    "Request Date",
    "Punch In Time",
    "Punch Out Time",
    "From Time",
    "To Time",
    "From Date",
    "To Date",
    "Notes",
    "Status",
    "Reviewed By",
    "Reviewed At",
  ];
}

function ensureHeaders(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headerList());
    formatHeader(sheet);
    return;
  }

  let headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
  let changed = false;

  if (String(headers[0]).trim() !== "Request ID") {
    sheet.insertColumnBefore(1);
    sheet.getRange(1, 1).setValue("Request ID");
    changed = true;
  }

  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  ["Status", "Reviewed By", "Reviewed At"].forEach(function (name) {
    if (headers.indexOf(name) === -1) {
      sheet.getRange(1, sheet.getLastColumn() + 1).setValue(name);
      headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
      changed = true;
    }
  });

  if (changed) {
    formatHeader(sheet);
  }
}

function formatHeader(sheet) {
  const lastCol = Math.max(sheet.getLastColumn(), headerList().length);
  sheet.getRange(1, 1, 1, lastCol).setFontWeight("bold");
  sheet.setFrozenRows(1);
}

function sheetToObjects(sheet) {
  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow < 2) {
    return [];
  }

  const values = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  const headers = values[0];
  const rows = [];

  for (let i = 1; i < values.length; i++) {
    const obj = {};
    headers.forEach(function (header, index) {
      obj[header] = stringifyValue(values[i][index]);
    });
    if (!obj["Request ID"] && !obj["Name"]) {
      continue;
    }
    rows.push(obj);
  }

  return rows.reverse();
}

function stringifyValue(value) {
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm");
  }
  return value == null ? "" : String(value);
}

function updateStatus(requestId, status, reviewedBy, department) {
  if (!requestId) {
    throw new Error("Missing request id");
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const names = ["All"];
  if (department && department !== "ALL") {
    names.push(department);
  }

  names.forEach(function (name) {
    const sheet = ss.getSheetByName(name);
    if (sheet) {
      updateSheetStatus(sheet, requestId, status, reviewedBy);
    }
  });
}

function updateSheetStatus(sheet, requestId, status, reviewedBy) {
  if (sheet.getLastRow() < 2) {
    return;
  }

  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const idCol = headers.indexOf("Request ID") + 1;
  const statusCol = headers.indexOf("Status") + 1;
  const reviewedByCol = headers.indexOf("Reviewed By") + 1;
  const reviewedAtCol = headers.indexOf("Reviewed At") + 1;

  if (!idCol || !statusCol) {
    return;
  }

  const ids = sheet.getRange(2, idCol, sheet.getLastRow() - 1, 1).getValues();
  const now = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss");

  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]) === String(requestId)) {
      const row = i + 2;
      sheet.getRange(row, statusCol).setValue(status);
      if (reviewedByCol) {
        sheet.getRange(row, reviewedByCol).setValue(reviewedBy);
      }
      if (reviewedAtCol) {
        sheet.getRange(row, reviewedAtCol).setValue(now);
      }
    }
  }
}
