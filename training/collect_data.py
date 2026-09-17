"""Utility to scan, validate, and convert open-source Luau code into training samples."""
from pathlib import Path
import json
import argparse

def process_file(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("--!strict"):
        return None
    return {
        "instruction": f"Implement {path.stem} in strict Luau following Roblox production standards.",
        "input": f"Module: {path.name}",
        "output": json.dumps({
            "files": [{"path": str(path.as_posix()), "content": text}],
            "services": ["Players"],
            "summary": f"Clean implementation of {path.stem}"
        })
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect Luau files into JSONL dataset")
    parser.add_argument("source_dir", type=Path, help="Directory containing .luau files")
    parser.add_argument("output_file", type=Path, help="Target JSONL file")
    args = parser.parse_args()

    count = 0
    with open(args.output_file, "a", encoding="utf-8") as out:
        for p in args.source_dir.rglob("*.luau"):
            record = process_file(p)
            if record:
                out.write(json.dumps(record) + "\n")
                count += 1
    print(f"Collected {count} strict Luau training samples into {args.output_file}")
