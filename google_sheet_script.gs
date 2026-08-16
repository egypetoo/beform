function doPost(e) {
  const sheet = getSheet();
  const data = JSON.parse(e.postData.contents);
  sheet.appendRow([
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
  ]);

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName("HR Requests");
  if (!sheet) {
    sheet = ss.insertSheet("HR Requests");
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow([
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
    ]);
    sheet.getRange(1, 1, 1, 13).setFontWeight("bold");
    sheet.setFrozenRows(1);
  }
  return sheet;
}
