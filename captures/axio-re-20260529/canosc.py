import sys, re
from collections import defaultdict
# find bytes that take >=2 values during capture (oscillating), with counts
d=defaultdict(lambda: defaultdict(lambda: defaultdict(int))); cnt=defaultdict(int)
for line in open(sys.argv[1]):
    m=re.search(r'can0\s+([0-9A-Fa-f]+)\s+\[(\d+)\]\s+([0-9A-Fa-f ]+)',line)
    if m:
        i=m.group(1).upper(); cnt[i]+=1
        for k,v in enumerate(m.group(3).split()): d[i][k][v.upper()]+=1
SKIP={'7DF','7E8','7E0','2C4'}
for i in sorted(d):
    if i in SKIP: continue
    for k in range(8):
        vals=d[i][k]
        if len(vals)>=2:
            # ignore pure fast counters: a byte cycling through many values
            if len(vals)>6: continue
            s=','.join(f'{v}:{c}' for v,c in sorted(vals.items()))
            print(f'ID {i} byte{k}: [{s}]')
