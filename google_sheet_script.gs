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
    const items = data.items && data.items.length
      ? data.items
      : [{ request_id: data.request_id, department: data.department || "" }];
    updateStatuses(items, data.status, data.reviewed_by || "");
    return jsonResponse({ ok: true });
  }

  const deptSheet = getSheetByName(data.department || "Other");
  if (isDuplicate(deptSheet, data)) {
    return jsonResponse({ ok: true, duplicate: true });
  }
  if (hasRemotePunchConflict(deptSheet, data)) {
    return jsonResponse({ ok: true, conflict: true });
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

  const allSheet = getSheetByName("All");
  deptSheet.appendRow(row);
  allSheet.appendRow(row);

  return jsonResponse({ ok: true, duplicate: false });
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

function normalizeText(value) {
  return String(value == null ? "" : value).trim().toLowerCase();
}

function normalizeDate(value) {
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  const text = String(value == null ? "" : value).trim();
  return text.split(" ")[0];
}

function isDuplicate(sheet, data) {
  if (sheet.getLastRow() < 2) {
    return false;
  }

  const values = sheet.getRange(1, 1, sheet.getLastRow(), sheet.getLastColumn()).getValues();
  const headers = values[0];
  const fpCol = headers.indexOf("Fingerprint Number");
  const typeCol = headers.indexOf("Request Type");
  const fromCol = headers.indexOf("From Date");
  const toCol = headers.indexOf("To Date");
  const statusCol = headers.indexOf("Status");
  const punchInCol = headers.indexOf("Punch In Time");
  const punchOutCol = headers.indexOf("Punch Out Time");
  const fromTimeCol = headers.indexOf("From Time");
  const toTimeCol = headers.indexOf("To Time");

  if (fpCol < 0 || typeCol < 0) {
    return false;
  }

  const fp = normalizeText(data.fingerprint_id);
  const type = normalizeText(data.request_type);
  const fromDate = normalizeDate(data.start_date);
  const toDate = normalizeDate(data.end_date);
  const punchIn = normalizeText(data.punch_in_time);
  const punchOut = normalizeText(data.punch_out_time);
  const fromTime = normalizeText(data.from_time);
  const toTime = normalizeText(data.to_time);

  for (let i = values.length - 1; i >= 1; i--) {
    const row = values[i];
    const status = String(row[statusCol] || "").trim();
    if (status === "Rejected") {
      continue;
    }
    if (normalizeText(row[fpCol]) !== fp) {
      continue;
    }
    if (normalizeText(row[typeCol]) !== type) {
      continue;
    }
    if (fromCol >= 0 && normalizeDate(row[fromCol]) !== fromDate) {
      continue;
    }
    if (toCol >= 0 && normalizeDate(row[toCol]) !== toDate) {
      continue;
    }
    if (punchInCol >= 0 && normalizeText(row[punchInCol]) !== punchIn) {
      continue;
    }
    if (punchOutCol >= 0 && normalizeText(row[punchOutCol]) !== punchOut) {
      continue;
    }
    if (fromTimeCol >= 0 && normalizeText(row[fromTimeCol]) !== fromTime) {
      continue;
    }
    if (toTimeCol >= 0 && normalizeText(row[toTimeCol]) !== toTime) {
      continue;
    }
    return true;
  }

  return false;
}

function isPunchType(type) {
  return type === "missing punch in" || type === "missing punch out";
}

function isRemoteType(type) {
  return type === "work remotely";
}

function datesOverlap(fromA, toA, fromB, toB) {
  const startA = fromA || toA;
  const endA = toA || fromA;
  const startB = fromB || toB;
  const endB = toB || fromB;
  if (!startA || !startB) {
    return false;
  }
  return startA <= endB && startB <= endA;
}

function hasRemotePunchConflict(sheet, data) {
  const newType = normalizeText(data.request_type);
  const newIsPunch = isPunchType(newType);
  const newIsRemote = isRemoteType(newType);
  if (!newIsPunch && !newIsRemote) {
    return false;
  }
  if (sheet.getLastRow() < 2) {
    return false;
  }

  const values = sheet.getRange(1, 1, sheet.getLastRow(), sheet.getLastColumn()).getValues();
  const headers = values[0];
  const fpCol = headers.indexOf("Fingerprint Number");
  const typeCol = headers.indexOf("Request Type");
  const fromCol = headers.indexOf("From Date");
  const toCol = headers.indexOf("To Date");
  const statusCol = headers.indexOf("Status");
  if (fpCol < 0 || typeCol < 0) {
    return false;
  }

  const fp = normalizeText(data.fingerprint_id);
  const newFrom = normalizeDate(data.start_date);
  const newTo = normalizeDate(data.end_date);

  for (let i = values.length - 1; i >= 1; i--) {
    const row = values[i];
    const status = String(row[statusCol] || "").trim();
    if (status === "Rejected") {
      continue;
    }
    if (normalizeText(row[fpCol]) !== fp) {
      continue;
    }

    const existingType = normalizeText(row[typeCol]);
    const existingFrom = fromCol >= 0 ? normalizeDate(row[fromCol]) : "";
    const existingTo = toCol >= 0 ? normalizeDate(row[toCol]) : "";
    const overlaps = datesOverlap(newFrom, newTo, existingFrom, existingTo);
    if (!overlaps) {
      continue;
    }

    if (newIsPunch && isRemoteType(existingType)) {
      return true;
    }
    if (newIsRemote && isPunchType(existingType)) {
      return true;
    }
  }

  return false;
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

function columnToLetter(column) {
  let letter = "";
  while (column > 0) {
    const remainder = (column - 1) % 26;
    letter = String.fromCharCode(65 + remainder) + letter;
    column = Math.floor((column - 1) / 26);
  }
  return letter;
}

function colorRow(sheet, row, lastCol, status) {
  const range = sheet.getRange(row, 1, 1, lastCol);
  if (status === "Approved") {
    range.setBackground("#c6efce");
  } else if (status === "Rejected") {
    range.setBackground("#ffc7ce");
  } else {
    range.setBackground("#ffffff");
  }
}

function applyStatusColors(sheet) {
  const lastCol = Math.max(sheet.getLastColumn(), headerList().length);
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const statusCol = headers.indexOf("Status") + 1;
  if (!statusCol) {
    return;
  }

  const statusLetter = columnToLetter(statusCol);
  const range = sheet.getRange(2, 1, Math.max(sheet.getMaxRows() - 1, 1), lastCol);
  const approved = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied("=" + statusLetter + '2="Approved"')
    .setBackground("#c6efce")
    .setRanges([range])
    .build();
  const rejected = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied("=" + statusLetter + '2="Rejected"')
    .setBackground("#ffc7ce")
    .setRanges([range])
    .build();

  sheet.setConditionalFormatRules([approved, rejected]);
}

function stringifyValue(value) {
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm");
  }
  return value == null ? "" : String(value);
}

function updateStatuses(items, status, reviewedBy) {
  const ids = [];
  const names = { All: true };

  (items || []).forEach(function (item) {
    if (item && item.request_id) {
      ids.push(String(item.request_id));
      if (item.department && item.department !== "ALL") {
        names[item.department] = true;
      }
    }
  });

  if (!ids.length) {
    throw new Error("Missing request id");
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  Object.keys(names).forEach(function (name) {
    const sheet = ss.getSheetByName(name);
    if (sheet) {
      updateSheetStatuses(sheet, ids, status, reviewedBy);
    }
  });
}

function updateSheetStatuses(sheet, requestIds, status, reviewedBy) {
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
  const wanted = {};
  requestIds.forEach(function (id) {
    wanted[String(id)] = true;
  });

  for (let i = 0; i < ids.length; i++) {
    if (wanted[String(ids[i][0])]) {
      const row = i + 2;
      sheet.getRange(row, statusCol).setValue(status);
      if (reviewedByCol) {
        sheet.getRange(row, reviewedByCol).setValue(reviewedBy);
      }
      if (reviewedAtCol) {
        sheet.getRange(row, reviewedAtCol).setValue(now);
      }
      colorRow(sheet, row, lastCol, status);
    }
  }

  applyStatusColors(sheet);
}
