import json
from pathlib import Path

from ctd_bottle_match import process

source = Path(__file__).parents[1] / "fixtures" / "input.json"
print(json.dumps(process(json.loads(source.read_text())), ensure_ascii=False, indent=2))

