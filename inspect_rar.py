import json, sys
from rar_backend import inspect
print(json.dumps(inspect(sys.argv[1]), indent=2, ensure_ascii=False))
