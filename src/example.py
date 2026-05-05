import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.sanitization.processor import SanitizationProcessor

OUTPUT_DIR = f"{ROOT_DIR}/downloads"
INPUT_FILE = f"{ROOT_DIR}/input/Archivew.zip"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)


def sanitize():
    result = SanitizationProcessor.sanitize_dataset(
        input_zip_path=INPUT_FILE,
        output_dir=OUTPUT_DIR,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    sanitize()
