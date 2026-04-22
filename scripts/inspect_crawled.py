"""Quick exploratory script to inspect crawled VietnamWorks data."""
import sys, io, gzip, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import Counter
from src.storage.minio_client import MinioClient

m = MinioClient()
keys = sorted([o['Key'] for o in m.s3_client.list_objects_v2(Bucket='talentpulse-raw', Prefix='details/vietnamworks/html/').get('Contents', [])])

CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)

def decode_rsc(html: str) -> str:
    out = []
    for m in CHUNK_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            pass
    return '\n'.join(out)

FIELD_RE_CACHE = {}
def field(text: str, name: str):
    if name not in FIELD_RE_CACHE:
        FIELD_RE_CACHE[name] = re.compile('"' + re.escape(name) + r'":(\"(?:\\.|[^"\\])*\"|-?\d+(?:\.\d+)?|true|false|null)')
    m = FIELD_RE_CACHE[name].search(text)
    if not m:
        return None
    v = m.group(1)
    if v.startswith('"'):
        try:
            return json.loads(v)
        except Exception:
            return v[1:-1]
    if v in ('true','false','null'):
        return {'true':True,'false':False,'null':None}[v]
    return float(v) if '.' in v else int(v)

print(f"{'job_id':9} | {'title':48} | {'company':30} | {'level':14} | {'salary VND':18} | expires")
print("-" * 145)

rows = []
for key in keys:
    body = m.s3_client.get_object(Bucket='talentpulse-raw', Key=key)['Body'].read()
    html = gzip.decompress(body).decode('utf-8', errors='replace')
    rsc = decode_rsc(html)

    title = field(rsc, 'jobTitle')
    company = field(rsc, 'companyName')
    level = field(rsc, 'jobLevel')
    smin = field(rsc, 'salaryMin')
    smax = field(rsc, 'salaryMax')
    expires = field(rsc, 'expiredOn')
    views = field(rsc, 'numOfViews')
    apps = field(rsc, 'numOfApplications')

    salary = '-'
    if smin and smax and smin != 0:
        salary = f"{int(smin):,}-{int(smax):,}"

    job_id = key.split('/')[-1].replace('.html.gz', '')
    print(f"{job_id:9} | {str(title or '?')[:48]:48} | {str(company or '?')[:30]:30} | {str(level or '?')[:14]:14} | {salary:18} | {str(expires or '-')[:10]}")
    rows.append({'job_id': job_id, 'title': title, 'company': company,
                 'level': level, 'salary_min': smin, 'salary_max': smax,
                 'expires': expires, 'views': views, 'apps': apps})

print("\n" + "=" * 60)
print("AGGREGATE STATS")
print("=" * 60)
print(f"Total jobs           : {len(rows)}")
print(f"Titles extracted     : {sum(1 for r in rows if r['title'])}/{len(rows)}")
print(f"Companies extracted  : {sum(1 for r in rows if r['company'])}/{len(rows)}")
print(f"Salary visible       : {sum(1 for r in rows if r['salary_min'] and r['salary_min']!=0)}/{len(rows)}")
print(f"View counts present  : {sum(1 for r in rows if r['views'] is not None)}/{len(rows)}")
print(f"Unique companies     : {len(set(r['company'] for r in rows if r['company']))}")

print("\nLevel distribution:")
for lvl, cnt in Counter(r['level'] for r in rows if r['level']).most_common():
    print(f"  {lvl:25}: {cnt}")

print("\nTop companies:")
for co, cnt in Counter(r['company'] for r in rows if r['company']).most_common(5):
    print(f"  {co[:50]:50}: {cnt}")

print("\nVisible salary jobs:")
for r in rows:
    if r['salary_min'] and r['salary_min'] != 0:
        title_safe = (r['title'] or '?')[:55]
        print(f"  {title_safe:55}: {int(r['salary_min']):>12,} - {int(r['salary_max']):>12,}")

print("\nMost-viewed jobs:")
for r in sorted([r for r in rows if r['views']], key=lambda x: -x['views'])[:5]:
    print(f"  {(r['title'] or '?')[:55]:55}: {r['views']:>5} views, {r['apps'] or 0} apps")
