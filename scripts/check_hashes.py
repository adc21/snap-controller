import hashlib
from pathlib import Path
for p in ['example_3D.NAP', 'JSSI2.NAP', 'example_3D.s8i', 'JSSI2.s8i']:
    data = Path('C:/Users/keita/App/ADC/snap-controller/example_model/example_3D/' + p).read_bytes()
    print(p, 'size=', len(data), 'md5=', hashlib.md5(data).hexdigest())
