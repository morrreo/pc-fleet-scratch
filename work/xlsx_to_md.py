#!/usr/bin/env python3
"""Convert the two relevant tabs of workbook.xlsx into the pipe-table text
that pipeline.py's parse() expects (Table A = Laporan PC Form, Table B = List PC)."""
import openpyxl
import datetime
import sys

wb = openpyxl.load_workbook('workbook.xlsx', data_only=True)

EXPECTED_A_HEADERS = ['Timestamp', 'Tanggal', 'Nama Operator', 'Email Address', 'Merek PC',
                      'Tipe PC', 'Kode PC', 'Tipe Bahan Bakar', 'Bahan Bakar yang di pakai',
                      'Unit Solar', 'Gas', 'Jenis Pekerjaan', 'Catatan Tambahan / Keterangan',
                      'Lokasi', 'Shift Operator']
EXPECTED_A_NCOLS = 22
EXPECTED_B_HEADERS = ['MEREK PC', 'TIPE PC', 'SPEK PC', 'KODE PC', 'NOMOR SERIAL PC']
EXPECTED_B_NCOLS = 7


def fmt_cell(v):
    if v is None:
        return ''
    if isinstance(v, datetime.datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return '%d/%d/%d' % (v.month, v.day, v.year)
        return '%d/%d/%d %d:%02d:%02d' % (v.month, v.day, v.year, v.hour, v.minute, v.second)
    if isinstance(v, datetime.date):
        return '%d/%d/%d' % (v.month, v.day, v.year)
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return str(v)
    s = str(v).strip()
    return s


def escape(s):
    # Cell text can contain embedded newlines (multi-line job notes); the
    # pipe-table format is line-based, so a literal newline would split one
    # logical row into two and desync every column after it.
    s = s.replace('\r\n', ' / ').replace('\n', ' / ').replace('\r', ' / ')
    return s.replace('|', '\\|')


def sheet_rows(ws, ncols):
    rows = []
    for row in ws.iter_rows(values_only=True):
        vals = list(row[:ncols])
        while len(vals) < ncols:
            vals.append(None)
        rows.append([fmt_cell(v) for v in vals])
    return rows


def to_md_table(rows):
    lines = []
    for r in rows:
        lines.append('| ' + ' | '.join(escape(c) for c in r) + ' |')
    return '\n'.join(lines)


def find_sheet(name):
    if name not in wb.sheetnames:
        raise SystemExit('CONVERT_FAIL: sheet %r not found. Sheets present: %s'
                         % (name, wb.sheetnames))
    return wb[name]


def trim_trailing_empty(rows, header_len):
    # Stop at first fully-empty row (after header) - keeps the table well-formed
    # for pipeline.py's row-shape scanner.
    out = []
    for i, r in enumerate(rows):
        if i > 0 and all(c == '' for c in r):
            break
        out.append(r)
    return out


ws_a = find_sheet('Laporan PC Form')
rows_a = sheet_rows(ws_a, EXPECTED_A_NCOLS)
header_a = rows_a[0]
for h in EXPECTED_A_HEADERS:
    if not any(h in c for c in header_a):
        raise SystemExit('CONVERT_FAIL: Table A header missing expected column %r. Got: %s'
                         % (h, header_a))
rows_a = trim_trailing_empty(rows_a, EXPECTED_A_NCOLS)

ws_b = find_sheet('List PC')
rows_b_all = sheet_rows(ws_b, EXPECTED_B_NCOLS)
header_b = rows_b_all[0]
for h in EXPECTED_B_HEADERS:
    if not any(h in c for c in header_b):
        raise SystemExit('CONVERT_FAIL: Table B header missing expected column %r. Got: %s'
                         % (h, header_b))
rows_b = [rows_b_all[0]] + [r for r in rows_b_all[1:] if any(c != '' for c in r)]

out = []
out.append(to_md_table(rows_a))
out.append('')
out.append(to_md_table(rows_b))
text = '\n'.join(out)

with open('sheet.txt', 'w', encoding='utf-8') as fh:
    fh.write(text)

print('OK  Table A: %d data rows (of %d parsed)  Table B: %d data rows'
      % (len(rows_a) - 1, len(rows_a) - 1, len(rows_b) - 1))
