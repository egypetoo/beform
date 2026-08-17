const SHEET_SECRET = "HNCAHozrrkIAst0M1O_ZMqO9eY9XB4fILazhfu79ka0";

function doPost(e) {
  const data = parseData(e);
  if (!SHEET_SECRET || data.secret !== SHEET_SECRET) {
    return jsonResponse({ ok: false, error: "unauthorized" });
  }
  const action = data.action || "create";

  if (action === "list") {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheetName = !data.department || data.department === "ALL" ? "All" : data.department;
    const sheet = ss.getSheetByName(sheetName);
    return jsonResponse({ ok: true, rows: sheet ? sheetToObjects(sheet) : [] });
  }

  if (action === "lookup") {
    return jsonResponse({ ok: true, rows: lookupByFingerprint(data.fingerprint_id || "", data.limit || 30) });
  }

  if (action === "set_status") {
    const items = data.items && data.items.length
      ? data.items
      : [{ request_id: data.request_id, department: data.department || "" }];
    updateStatuses(items, data.status, data.reviewed_by || "", data.reason || "");
    return jsonResponse({ ok: true });
  }

  if (action !== "create") {
    return jsonResponse({ ok: false, error: "unknown_action" });
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const deptSheet = getOrCreateSheet(ss, data.department || "Other");
  const deptHeaders = ensureHeaders(deptSheet);
  const lastRow = deptSheet.getLastRow();
  const values = lastRow >= 2
    ? deptSheet.getRange(1, 1, lastRow, deptSheet.getLastColumn()).getValues()
    : [deptHeaders];

  const blocked = checkCreateConflicts(values, data);
  if (blocked) {
    return jsonResponse(blocked);
  }

  const valuesByHeader = {
    "Request ID": data.request_id || "",
    "Submitted At": data.submitted_at || "",
    "Fingerprint Number": data.fingerprint_id || "",
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

function checkCreateConflicts(values, data) {
  if (!values || values.length < 2) {
    return null;
  }

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
    return null;
  }

  const fp = normalizeText(data.fingerprint_id);
  const type = normalizeText(data.request_type);
  const fromDate = normalizeDate(data.start_date);
  const toDate = normalizeDate(data.end_date);
  const punchIn = normalizeText(data.punch_in_time);
  const punchOut = normalizeText(data.punch_out_time);
  const fromTime = normalizeText(data.from_time);
  const toTime = normalizeText(data.to_time);
  const newIsSaturday = isSaturdayWorkType(type);
  const newIsLeave = isOffDayLeave(type);
  const newIsPunch = isPunchType(type);
  const newIsRemote = isRemoteType(type);
  const cycle = newIsSaturday ? payrollCycleStart(fromDate) : "";

  let duplicate = false;
  let remoteConflict = false;
  let saturdayConflict = false;

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

    if (newIsSaturday && cycle && isSaturdayWorkType(existingType) && payrollCycleStart(existingFrom) === cycle) {
      return { ok: true, duplicate: true, saturday_month: true };
    }

    if (
      !duplicate
      && existingType === type
      && (fromCol < 0 || existingFrom === fromDate)
      && (toCol < 0 || existingTo === toDate)
      && (punchInCol < 0 || normalizeText(row[punchInCol]) === punchIn)
      && (punchOutCol < 0 || normalizeText(row[punchOutCol]) === punchOut)
      && (fromTimeCol < 0 || normalizeText(row[fromTimeCol]) === fromTime)
      && (toTimeCol < 0 || normalizeText(row[toTimeCol]) === toTime)
    ) {
      duplicate = true;
    }

    if ((newIsPunch || newIsRemote || newIsSaturday || newIsLeave) && datesOverlap(fromDate, toDate, existingFrom, existingTo)) {
      if (newIsPunch && isRemoteType(existingType)) {
        remoteConflict = true;
      }
      if (newIsRemote && isPunchType(existingType)) {
        remoteConflict = true;
      }
      if (newIsSaturday && isOffDayLeave(existingType)) {
        saturdayConflict = true;
      }
      if (newIsLeave && isSaturdayWorkType(existingType)) {
        saturdayConflict = true;
      }
    }
  }

  if (duplicate) {
    return { ok: true, duplicate: true };
  }
  if (remoteConflict) {
    return { ok: true, conflict: true };
  }
  if (saturdayConflict) {
    return { ok: true, conflict: true, conflict_type: "saturday" };
  }
  return null;
}

function isSaturdayWorkType(type) {
  return type === "monthly saturday work";
}

function isOffDayLeave(type) {
  return type === "work remotely"
    || type === "annual vacation"
    || type === "sickness vacation"
    || type === "sick leave"
    || type === "unpaid leave";
}

function payrollCycleStart(dateText) {
  const parts = String(dateText || "").split("-");
  if (parts.length < 3) {
    return "";
  }
  let year = parseInt(parts[0], 10);
  let month = parseInt(parts[1], 10);
  const day = parseInt(parts[2], 10);
  if (!year || !month || !day) {
    return "";
  }
  if (day < 25) {
    month -= 1;
    if (month < 1) {
      month = 12;
      year -= 1;
    }
  }
  return year + "-" + String(month).padStart(2, "0") + "-25";
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

function appendMappedRow(sheet, valuesByHeader, headers) {
  if (!headers || !headers.length) {
    headers = ensureHeaders(sheet);
  }
  const row = headers.map(function (header) {
    return Object.prototype.hasOwnProperty.call(valuesByHeader, header) ? valuesByHeader[header] : "";
  });
  sheet.appendRow(row);
}

function getOrCreateSheet(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
  }
  return sheet;
}

function getSheetByName(name) {
  const sheet = getOrCreateSheet(SpreadsheetApp.getActiveSpreadsheet(), name);
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
    "Rejection Reason",
    "Team",
  ];
}

function ensureHeaders(sheet) {
  const needed = headerList();
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(needed);
    formatHeader(sheet);
    return needed;
  }

  let headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
  let changed = false;

  if (String(headers[0]).trim() !== "Request ID") {
    sheet.insertColumnBefore(1);
    sheet.getRange(1, 1).setValue("Request ID");
    headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    changed = true;
  }

  const missing = needed.filter(function (name) {
    return headers.indexOf(name) === -1;
  });
  if (missing.length) {
    sheet.getRange(1, sheet.getLastColumn() + 1, 1, missing.length).setValues([missing]);
    headers = headers.concat(missing);
    changed = true;
  }

  if (changed) {
    formatHeader(sheet);
  }
  return headers;
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
  if (typeof value === "number" && value === Math.floor(value)) {
    return String(value);
  }
  return value == null ? "" : String(value);
}

function normalizeFingerprint(value) {
  return String(value == null ? "" : value).trim().replace(/\.0$/, "");
}

function lookupByFingerprint(fingerprint, limit) {
  const fp = normalizeFingerprint(fingerprint);
  if (!fp) {
    return [];
  }

  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("All");
  if (!sheet) {
    return [];
  }

  const max = Math.max(1, Math.min(parseInt(limit, 10) || 30, 50));
  return sheetToObjects(sheet).filter(function (row) {
    return normalizeFingerprint(row["Fingerprint Number"]) === fp;
  }).slice(0, max);
}

function updateStatuses(items, status, reviewedBy, reason) {
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
      updateSheetStatuses(sheet, ids, status, reviewedBy, reason || "");
    }
  });
}

function updateSheetStatuses(sheet, requestIds, status, reviewedBy, reason) {
  if (sheet.getLastRow() < 2) {
    return;
  }

  ensureHeaders(sheet);
  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const idCol = headers.indexOf("Request ID") + 1;
  const statusCol = headers.indexOf("Status") + 1;
  const reviewedByCol = headers.indexOf("Reviewed By") + 1;
  const reviewedAtCol = headers.indexOf("Reviewed At") + 1;
  const reasonCol = headers.indexOf("Rejection Reason") + 1;

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
      if (reasonCol) {
        sheet.getRange(row, reasonCol).setValue(status === "Rejected" ? reason : "");
      }
      colorRow(sheet, row, lastCol, status);
    }
  }

  applyStatusColors(sheet);
}
