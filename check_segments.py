import sqlite3, json
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
c.row_factory = sqlite3.Row
rows = c.execute('SELECT data_json FROM prospects').fetchall()
seg_counts = {}
for r in rows:
    d = json.loads(r['data_json'])
    segs = d.get('segments', [])
    for s in segs:
        seg_counts[s] = seg_counts.get(s, 0) + 1
    if not segs:
        seg_counts['(none)'] = seg_counts.get('(none)', 0) + 1
for seg, cnt in sorted(seg_counts.items(), key=lambda x: -x[1]):
    print(f"  {seg}: {cnt}")
c.close()