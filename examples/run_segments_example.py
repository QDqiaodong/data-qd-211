import json
from pathlib import Path

from ctd_bottle_match import process_with_segments

source = Path(__file__).parents[1] / "fixtures" / "segments_input.json"
result = process_with_segments(json.loads(source.read_text()))
print(json.dumps(result, ensure_ascii=False, indent=2))
