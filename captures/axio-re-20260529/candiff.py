import sys, re
from collections import defaultdict
def load(fn):
    d=defaultdict(lambda: defaultdict(lambda: defaultdict(int))); cnt=defaultdict(int)
    for line in open(fn):
        m=re.search(r'can0\s+([0-9A-Fa-f]+)\s+\[(\d+)\]\s+([0-9A-Fa-f ]+)',line)
        if m:
            i=m.group(1).upper(); cnt[i]+=1
            for k,v in enumerate(m.group(3).split()): d[i][k][v.upper()]+=1
    return d,cnt
a,ca=load(sys.argv[1]); b,cb=load(sys.argv[2])
SKIP={'7DF','7E8','7E0','640'}
for i in sorted(set(a)|set(b)):
    if i in SKIP: continue
    for k in range(8):
        va=set(a[i].get(k,{})); vb=set(b[i].get(k,{})); newv=vb-va
        if newv and len(va)<=3:
            # show value:count for active
            act=','.join(f'{v}:{b[i][k][v]}' for v in sorted(b[i][k]))
            print(f'ID {i} byte{k}: base={sorted(va)} -> active[{act}]  (act new={sorted(newv)})')
